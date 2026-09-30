"""
Recuperación: dada una pregunta, busca en Chroma los trozos más parecidos.

Uso (desde la raíz del repo, después de python ingest.py):
    python retrieve.py "¿Cómo deshago el último commit?"
"""
import sys

import chromadb
from chromadb.utils import embedding_functions

# Tiene que ser el MISMO modelo que en ingest.py: si no, los vectores no son comparables.
MODELO = "intfloat/multilingual-e5-large"

ef = embedding_functions.SentenceTransformerEmbeddingFunction(model_name=MODELO)
client = chromadb.PersistentClient(path="./chroma")
col = client.get_collection("docs", embedding_function=ef)


def buscar(pregunta, k=5):
    """Devuelve una lista de (texto, metadatos, similitud) con los k trozos más parecidos."""
    # "query: " es el prefijo que pide e5 para las preguntas (los documentos llevan "passage: ").
    res = col.query(query_texts=[f"query: {pregunta}"], n_results=k)
    return [
        # Chroma devuelve la distancia coseno: 0 = idénticos. La pasamos a similitud (1 = idénticos).
        (texto.removeprefix("passage: "), meta, 1 - distancia)
        for texto, meta, distancia in zip(
            res["documents"][0], res["metadatas"][0], res["distances"][0])
    ]


if __name__ == "__main__":
    pregunta = " ".join(sys.argv[1:]) or "¿Cómo deshago el último commit?"
    print(f"Pregunta: {pregunta}\n")
    for n, (texto, meta, similitud) in enumerate(buscar(pregunta), start=1):
        print(f"[{n}] {meta['fuente']}  (similitud {similitud:.3f})")
        print(f"    sección: {meta['seccion']}")
        print("    " + texto[:400].replace("\n", "\n    "))
        print()
