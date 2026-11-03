"""
Recuperación: dada una pregunta, busca en Chroma los trozos más parecidos, opcionalmente
los combina con una búsqueda por palabras (BM25) y los reordena con un reranker.

Uso (desde la raíz del repo, después de python ingest.py):
    python retrieve.py "¿Cómo deshago el último commit?"
    python retrieve.py --sin-rerank "¿Cómo deshago el último commit?"
    python retrieve.py --hibrida "¿Cómo deshago el último commit?"
"""
import re
import sys
import unicodedata

import config

# Tiene que ser el MISMO modelo que en ingest.py: si no, los vectores no son comparables.
MODELO = "intfloat/multilingual-e5-large"
# Reranker multilingüe: lee pregunta y trozo JUNTOS y puntúa si el trozo responde a la pregunta.
MODELO_RERANKER = "BAAI/bge-reranker-v2-m3"

# Los modelos se cargan la primera vez que se usan, no al importar el módulo: así los tests
# que no los necesitan no cargan ~4 GB y quien importe retrieve arranca rápido.
_col = None
_reranker = None
_bm25 = None


def coleccion():
    global _col
    if _col is None:
        import chromadb
        from chromadb.utils import embedding_functions
        ef = embedding_functions.SentenceTransformerEmbeddingFunction(model_name=MODELO)
        _col = chromadb.PersistentClient(path="./chroma").get_collection("docs", embedding_function=ef)
    return _col


def reranker():
    global _reranker
    if _reranker is None:
        from sentence_transformers import CrossEncoder
        _reranker = CrossEncoder(MODELO_RERANKER)
    return _reranker


# ---------------------------------------------------------------------------
# BM25: búsqueda por palabras exactas sobre los mismos trozos de Chroma
# ---------------------------------------------------------------------------

def tokenizar(texto):
    """Minúsculas y sin tildes, para que "Configuración" y "configuracion" coincidan."""
    sin_tildes = "".join(c for c in unicodedata.normalize("NFKD", texto.lower())
                         if not unicodedata.combining(c))
    return re.findall(r"\w+", sin_tildes)


def indice_bm25():
    """Construye el índice BM25 en memoria con los trozos que ya hay en Chroma (sin re-trocear)."""
    global _bm25
    if _bm25 is None:
        from rank_bm25 import BM25Okapi
        datos = coleccion().get(include=["documents", "metadatas"])
        textos = [t.removeprefix("passage: ") for t in datos["documents"]]
        _bm25 = {
            "indice": BM25Okapi([tokenizar(t) for t in textos]),
            "trozos": list(zip(datos["ids"], textos, datos["metadatas"])),
        }
    return _bm25


def buscar_bm25(pregunta, k):
    """Devuelve [(id, texto, metadatos)] de los k trozos con más puntuación BM25."""
    bm25 = indice_bm25()
    puntuaciones = bm25["indice"].get_scores(tokenizar(pregunta))
    mejores = sorted(range(len(puntuaciones)), key=lambda i: puntuaciones[i], reverse=True)[:k]
    return [bm25["trozos"][i] for i in mejores if puntuaciones[i] > 0]


# ---------------------------------------------------------------------------
# Fusión y búsqueda
# ---------------------------------------------------------------------------

def rrf(listas, k=config.RRF_K):
    """
    Reciprocal Rank Fusion: combina varias listas ordenadas de ids usando solo su posición.
    Cada id suma 1 / (k + puesto) por cada lista en la que aparece. Devuelve [(id, puntuación)]
    ordenado de mayor a menor. No necesita que las puntuaciones originales sean comparables.
    """
    puntuacion = {}
    for lista in listas:
        for puesto, id_ in enumerate(lista, start=1):
            puntuacion[id_] = puntuacion.get(id_, 0.0) + 1.0 / (k + puesto)
    return sorted(puntuacion.items(), key=lambda x: x[1], reverse=True)


def buscar_vectorial_con_ids(pregunta, k):
    """Devuelve [(id, texto, metadatos, similitud)] de los k trozos más parecidos."""
    # "query: " es el prefijo que pide e5 para las preguntas (los documentos llevan "passage: ").
    res = coleccion().query(query_texts=[f"query: {pregunta}"], n_results=k)
    return [
        # Chroma devuelve la distancia coseno: 0 = idénticos. La pasamos a similitud (1 = idénticos).
        (id_, texto.removeprefix("passage: "), meta, 1 - distancia)
        for id_, texto, meta, distancia in zip(
            res["ids"][0], res["documents"][0], res["metadatas"][0], res["distances"][0])
    ]


def buscar_vectorial(pregunta, k=5):
    """Devuelve una lista de (texto, metadatos, similitud) con los k trozos más parecidos."""
    return [(texto, meta, sim) for _, texto, meta, sim in buscar_vectorial_con_ids(pregunta, k)]


def buscar_hibrida(pregunta, k):
    """Vectorial + BM25 fusionadas con RRF. Devuelve [(texto, metadatos, puntuación RRF)]."""
    vectorial = buscar_vectorial_con_ids(pregunta, k)
    palabras = buscar_bm25(pregunta, k)
    trozos = {id_: (texto, meta) for id_, texto, meta, _ in vectorial}
    trozos.update({id_: (texto, meta) for id_, texto, meta in palabras})
    fusion = rrf([[t[0] for t in vectorial], [t[0] for t in palabras]])
    return [(*trozos[id_], puntuacion) for id_, puntuacion in fusion[:k]]


def buscar(pregunta, k=config.K, rerank=True, k_inicial=config.K_INICIAL, hibrida=None):
    """
    Devuelve los k mejores trozos como (texto, metadatos, puntuación).

    hibrida (por defecto config.HIBRIDA): si es True, los candidatos salen de fusionar la
    búsqueda vectorial y BM25 con RRF; si es False, solo de la vectorial.
    rerank=True: se sacan k_inicial candidatos (rápido pero impreciso) y el reranker los
    reordena (lento pero preciso). La puntuación es la del reranker (0-1).
    rerank=False: la puntuación es la similitud coseno (vectorial) o la de RRF (híbrida).
    """
    hibrida = config.HIBRIDA if hibrida is None else hibrida
    candidatos_de = buscar_hibrida if hibrida else buscar_vectorial
    if not rerank:
        return candidatos_de(pregunta, k)
    candidatos = candidatos_de(pregunta, k_inicial)
    puntuaciones = reranker().predict([(pregunta, texto) for texto, _, _ in candidatos])
    reordenados = sorted(
        ((texto, meta, float(p)) for (texto, meta, _), p in zip(candidatos, puntuaciones)),
        key=lambda x: x[2], reverse=True)
    return reordenados[:k]


if __name__ == "__main__":
    args = sys.argv[1:]
    rerank = "--sin-rerank" not in args
    hibrida = True if "--hibrida" in args else None
    pregunta = " ".join(a for a in args if not a.startswith("--")) or "¿Cómo deshago el último commit?"
    modo = "híbrida" if (hibrida or config.HIBRIDA) else "vectorial"
    print(f"Pregunta: {pregunta}  (búsqueda {modo}, {'con' if rerank else 'sin'} reranker)\n")
    for n, (texto, meta, puntuacion) in enumerate(buscar(pregunta, rerank=rerank, hibrida=hibrida), start=1):
        print(f"[{n}] {meta['fuente']}  (puntuación {puntuacion:.3f})")
        print(f"    sección: {meta['seccion']}")
        print("    " + texto[:400].replace("\n", "\n    "))
        print()
