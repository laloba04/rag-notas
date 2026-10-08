"""
Recuperación: dada una pregunta, busca en Chroma los trozos más parecidos
y, opcionalmente, los reordena con un reranker.

Uso (desde la raíz del repo, después de python ingest.py):
    python retrieve.py "¿Cómo deshago el último commit?"
    python retrieve.py --sin-rerank "¿Cómo deshago el último commit?"
"""
import sys

import chromadb
from chromadb.utils import embedding_functions
from sentence_transformers import CrossEncoder

# Tiene que ser el MISMO modelo que en ingest.py: si no, los vectores no son comparables.
MODELO = "intfloat/multilingual-e5-large"
# Reranker multilingüe: lee pregunta y trozo JUNTOS y puntúa si el trozo responde a la pregunta.
MODELO_RERANKER = "BAAI/bge-reranker-v2-m3"

ef = embedding_functions.SentenceTransformerEmbeddingFunction(model_name=MODELO)
client = chromadb.PersistentClient(path="./chroma")
col = client.get_collection("docs", embedding_function=ef)

_reranker = None


def reranker():
    """Carga el reranker la primera vez que se usa (son ~2 GB: no lo cargamos si no hace falta)."""
    global _reranker
    if _reranker is None:
        _reranker = CrossEncoder(MODELO_RERANKER)
    return _reranker


def buscar_vectorial(pregunta, k=5):
    """Devuelve una lista de (texto, metadatos, similitud) con los k trozos más parecidos."""
    # "query: " es el prefijo que pide e5 para las preguntas (los documentos llevan "passage: ").
    res = col.query(query_texts=[f"query: {pregunta}"], n_results=k)
    return [
        # Chroma devuelve la distancia coseno: 0 = idénticos. La pasamos a similitud (1 = idénticos).
        (texto.removeprefix("passage: "), meta, 1 - distancia)
        for texto, meta, distancia in zip(
            res["documents"][0], res["metadatas"][0], res["distances"][0])
    ]


def buscar(pregunta, k=5, rerank=True, k_inicial=25):
    """
    Devuelve los k mejores trozos como (texto, metadatos, puntuación).

    Con rerank=True: la búsqueda vectorial saca k_inicial candidatos (rápido pero impreciso)
    y el reranker los reordena (lento pero preciso). La puntuación es la del reranker.
    Con rerank=False: solo búsqueda vectorial. La puntuación es la similitud coseno.
    """
    if not rerank:
        return buscar_vectorial(pregunta, k)
    candidatos = buscar_vectorial(pregunta, k_inicial)
    puntuaciones = reranker().predict([(pregunta, texto) for texto, _, _ in candidatos])
    reordenados = sorted(
        ((texto, meta, float(p)) for (texto, meta, _), p in zip(candidatos, puntuaciones)),
        key=lambda x: x[2], reverse=True)
    return reordenados[:k]


if __name__ == "__main__":
    args = sys.argv[1:]
    rerank = "--sin-rerank" not in args
    pregunta = " ".join(a for a in args if a != "--sin-rerank") or "¿Cómo deshago el último commit?"
    print(f"Pregunta: {pregunta}  ({'con' if rerank else 'sin'} reranker)\n")
    for n, (texto, meta, puntuacion) in enumerate(buscar(pregunta, rerank=rerank), start=1):
        print(f"[{n}] {meta['fuente']}  (puntuación {puntuacion:.3f})")
        print(f"    sección: {meta['seccion']}")
        print("    " + texto[:400].replace("\n", "\n    "))
        print()
