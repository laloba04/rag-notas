"""
Recuperación: dada una pregunta, busca en Chroma los trozos más parecidos, opcionalmente
los combina con una búsqueda por palabras (BM25) y los reordena con un reranker.

Uso (desde la raíz del repo, después de python ingest.py):
    python retrieve.py "¿Cómo deshago el último commit?"
    python retrieve.py --sin-rerank "¿Cómo deshago el último commit?"
    python retrieve.py --hibrida "¿Cómo deshago el último commit?"
    python retrieve.py --acceso=publico,restringido "¿Qué tamaño máximo puede tener un Secret?"

Permisos: cada trozo lleva el metadato "confidencialidad" (publico / restringido). La búsqueda
solo ve los trozos de los niveles de acceso que se le pasan (por defecto, solo "publico"). El
filtro se aplica DENTRO de la búsqueda, nunca en el prompt: lo que no se recupera no puede
llegar al LLM, y el LLM no es un control de acceso.
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
_bm25 = {}          # un índice BM25 por combinación de niveles de acceso


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
# Permisos
# ---------------------------------------------------------------------------

def normalizar_acceso(acceso=None):
    """
    Devuelve los niveles de acceso como tupla ordenada. None = config.ACCESO_POR_DEFECTO.
    Un nivel desconocido o una lista vacía es un error: si "pubico" (con errata) devolviera
    cero resultados sin avisar, el fallo pasaría desapercibido.
    """
    if acceso is None:
        acceso = config.ACCESO_POR_DEFECTO
    if isinstance(acceso, str):
        acceso = (acceso,)
    acceso = tuple(sorted(set(acceso)))
    desconocidos = [n for n in acceso if n not in config.NIVELES_ACCESO]
    if not acceso or desconocidos:
        raise ValueError(f"Nivel de acceso no válido: {desconocidos or 'ninguno'}. "
                         f"Opciones: {', '.join(config.NIVELES_ACCESO)}")
    return acceso


def filtro_acceso(acceso):
    """Filtro de metadatos de Chroma: solo trozos cuyo nivel esté entre los permitidos.
    Un trozo SIN el metadato no cumple el filtro: si falta la etiqueta, no se ve."""
    return {"confidencialidad": {"$in": list(acceso)}}


def separar_acceso(args):
    """Para los scripts: saca --acceso=a,b de la lista de argumentos. Devuelve (acceso, resto)."""
    acceso = None
    resto = []
    for a in args:
        if a.startswith("--acceso="):
            acceso = tuple(n for n in a.removeprefix("--acceso=").split(",") if n)
        else:
            resto.append(a)
    return acceso, resto


# ---------------------------------------------------------------------------
# BM25: búsqueda por palabras exactas sobre los mismos trozos de Chroma
# ---------------------------------------------------------------------------

def tokenizar(texto):
    """Minúsculas y sin tildes, para que "Configuración" y "configuracion" coincidan."""
    sin_tildes = "".join(c for c in unicodedata.normalize("NFKD", texto.lower())
                         if not unicodedata.combining(c))
    return re.findall(r"\w+", sin_tildes)


def indice_bm25(acceso=None):
    """
    Construye en memoria el índice BM25 con los trozos de Chroma que permite el nivel de acceso
    (sin re-trocear). Un índice por nivel, en vez de uno global filtrado después, por dos motivos:
    los trozos restringidos no están en el índice público, así que no pueden salir por ningún
    descuido; y no influyen en sus estadísticas (la frecuencia de cada palabra en el corpus).
    """
    acceso = normalizar_acceso(acceso)
    if acceso not in _bm25:
        from rank_bm25 import BM25Okapi
        datos = coleccion().get(where=filtro_acceso(acceso), include=["documents", "metadatas"])
        textos = [t.removeprefix("passage: ") for t in datos["documents"]]
        _bm25[acceso] = {
            # BM25Okapi falla con un corpus vacío: si el nivel no tiene trozos, no hay índice.
            "indice": BM25Okapi([tokenizar(t) for t in textos]) if textos else None,
            "trozos": list(zip(datos["ids"], textos, datos["metadatas"])),
        }
    return _bm25[acceso]


def buscar_bm25(pregunta, k, acceso=None):
    """Devuelve [(id, texto, metadatos)] de los k trozos con más puntuación BM25."""
    bm25 = indice_bm25(acceso)
    if bm25["indice"] is None:
        return []
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


def buscar_vectorial_con_ids(pregunta, k, acceso=None):
    """Devuelve [(id, texto, metadatos, similitud)] de los k trozos más parecidos."""
    # "query: " es el prefijo que pide e5 para las preguntas (los documentos llevan "passage: ").
    # where filtra por permisos en la propia consulta: los k resultados son ya k trozos permitidos.
    res = coleccion().query(query_texts=[f"query: {pregunta}"], n_results=k,
                            where=filtro_acceso(normalizar_acceso(acceso)))
    return [
        # Chroma devuelve la distancia coseno: 0 = idénticos. La pasamos a similitud (1 = idénticos).
        (id_, texto.removeprefix("passage: "), meta, 1 - distancia)
        for id_, texto, meta, distancia in zip(
            res["ids"][0], res["documents"][0], res["metadatas"][0], res["distances"][0])
    ]


def buscar_vectorial(pregunta, k=5, acceso=None):
    """Devuelve una lista de (texto, metadatos, similitud) con los k trozos más parecidos."""
    return [(texto, meta, sim)
            for _, texto, meta, sim in buscar_vectorial_con_ids(pregunta, k, acceso)]


def buscar_hibrida(pregunta, k, acceso=None):
    """Vectorial + BM25 fusionadas con RRF. Devuelve [(texto, metadatos, puntuación RRF)].
    Las dos búsquedas filtran por permisos: si solo filtrara la vectorial, el documento
    restringido entraría por BM25."""
    vectorial = buscar_vectorial_con_ids(pregunta, k, acceso)
    palabras = buscar_bm25(pregunta, k, acceso)
    trozos = {id_: (texto, meta) for id_, texto, meta, _ in vectorial}
    trozos.update({id_: (texto, meta) for id_, texto, meta in palabras})
    fusion = rrf([[t[0] for t in vectorial], [t[0] for t in palabras]])
    return [(*trozos[id_], puntuacion) for id_, puntuacion in fusion[:k]]


def buscar(pregunta, k=config.K, rerank=True, k_inicial=config.K_INICIAL, hibrida=None,
           acceso=None):
    """
    Devuelve los k mejores trozos como (texto, metadatos, puntuación).

    acceso: niveles de confidencialidad que puede ver quien pregunta, p. ej. ("publico",)
    o ("publico", "restringido"). Por defecto, config.ACCESO_POR_DEFECTO (solo "publico").

    hibrida (por defecto config.HIBRIDA): si es True, los candidatos salen de fusionar la
    búsqueda vectorial y BM25 con RRF; si es False, solo de la vectorial.
    rerank=True: se sacan k_inicial candidatos (rápido pero impreciso) y el reranker los
    reordena (lento pero preciso). La puntuación es la del reranker (0-1).
    rerank=False: la puntuación es la similitud coseno (vectorial) o la de RRF (híbrida).
    """
    hibrida = config.HIBRIDA if hibrida is None else hibrida
    acceso = normalizar_acceso(acceso)
    candidatos_de = buscar_hibrida if hibrida else buscar_vectorial
    if not rerank:
        return candidatos_de(pregunta, k, acceso)
    candidatos = candidatos_de(pregunta, k_inicial, acceso)
    puntuaciones = reranker().predict([(pregunta, texto) for texto, _, _ in candidatos])
    reordenados = sorted(
        ((texto, meta, float(p)) for (texto, meta, _), p in zip(candidatos, puntuaciones)),
        key=lambda x: x[2], reverse=True)
    return reordenados[:k]


if __name__ == "__main__":
    acceso, args = separar_acceso(sys.argv[1:])
    rerank = "--sin-rerank" not in args
    hibrida = True if "--hibrida" in args else None
    pregunta = " ".join(a for a in args if not a.startswith("--")) or "¿Cómo deshago el último commit?"
    modo = "híbrida" if (hibrida or config.HIBRIDA) else "vectorial"
    acceso = normalizar_acceso(acceso)
    print(f"Pregunta: {pregunta}  (búsqueda {modo}, {'con' if rerank else 'sin'} reranker, "
          f"acceso: {', '.join(acceso)})\n")
    resultados = buscar(pregunta, rerank=rerank, hibrida=hibrida, acceso=acceso)
    for n, (texto, meta, puntuacion) in enumerate(resultados, start=1):
        print(f"[{n}] {meta['fuente']} [{meta['confidencialidad']}]  (puntuación {puntuacion:.3f})")
        print(f"    sección: {meta['seccion']}")
        print("    " + texto[:400].replace("\n", "\n    "))
        print()
