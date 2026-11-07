"""
Ingesta: documentos Markdown de data/ -> trozos (chunks) -> embeddings -> Chroma.

Uso (desde la raíz del repo):  python ingest.py
"""
from pathlib import Path

import chromadb
import frontmatter
from chromadb.utils import embedding_functions

# Modelo que convierte texto en vectores (embeddings). Multilingüe, así que entiende español.
MODELO = "intfloat/multilingual-e5-large"



def trocear(ruta):
    """Devuelve (ids, textos, metadatos) de los trozos de un documento Markdown."""
    # Se importa aquí porque carga torch (~4 s), y quien importa este módulo puede no necesitarlo.
    from langchain_text_splitters import MarkdownHeaderTextSplitter, RecursiveCharacterTextSplitter
    # Paso 1: partir por cabeceras (# , ##, ###), para que cada trozo sea de una sola sección.
    por_cabecera = MarkdownHeaderTextSplitter(
        headers_to_split_on=[("#", "h1"), ("##", "h2"), ("###", "h3")])
    # Paso 2: si una sección es larga, partirla en trozos de ~800 caracteres que se solapan 100.
    fino = RecursiveCharacterTextSplitter(chunk_size=800, chunk_overlap=100)
    doc = frontmatter.load(ruta)              # separa los metadatos (--- ... ---) del texto
    ids, textos, metas = [], [], []
    for s, seccion in enumerate(por_cabecera.split_text(doc.content)):
        ruta_secciones = " > ".join(seccion.metadata.values())   # p. ej. "Pods > Uso de Pods"
        for c, trozo in enumerate(fino.split_text(seccion.page_content)):
            ids.append(f"{ruta.stem}-s{s}-c{c}")
            # "passage: " es un prefijo que pide el modelo e5 para los documentos.
            # Añadimos la ruta de secciones para que el trozo no pierda su contexto.
            textos.append(f"passage: {ruta_secciones}\n{trozo}")
            metas.append({
                "fuente": ruta.name,
                "proyecto": doc["proyecto"],
                "seccion": ruta_secciones,
                "url": doc["url"],
                "confidencialidad": doc["confidencialidad"],
            })
    return ids, textos, metas


def main():
    ef = embedding_functions.SentenceTransformerEmbeddingFunction(model_name=MODELO)
    client = chromadb.PersistentClient(path="./chroma")   # la base de datos se guarda en chroma/

    # Empezamos de cero en cada ejecución, para que no queden trozos viejos si cambias el chunking.
    try:
        client.delete_collection("docs")
    except Exception:
        pass
    col = client.create_collection("docs", embedding_function=ef,
                                   metadata={"hnsw:space": "cosine"})

    for ruta in sorted(Path("data").glob("*.md")):
        ids, textos, metas = trocear(ruta)
        col.add(ids=ids, documents=textos, metadatas=metas)   # aquí se calculan los embeddings
        print(f"{ruta.name:40s} {len(ids):4d} chunks")

    print(f"\nTotal: {col.count()} chunks en chroma/")


if __name__ == "__main__":
    main()
