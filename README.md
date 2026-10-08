# rag-notas

Asistente de preguntas y respuestas sobre documentación técnica en español, construido con RAG (Retrieval-Augmented Generation): busca en los documentos los fragmentos relevantes para cada pregunta y responde a partir de ellos.

> Proyecto de aprendizaje personal, en desarrollo. Cada etapa se implementa primero sin frameworks para entender qué hace, y se mide antes de pasar a la siguiente.

**Estado actual:** ingesta, búsqueda vectorial y evaluación de la recuperación. Pendiente: búsqueda híbrida, reranker y generación de respuestas con citas.

## Corpus

20 documentos técnicos públicos en español (documentación de Kubernetes, Pro Git y MDN Web Docs), unas 50.000 palabras, con un conjunto de evaluación de 28 preguntas escritas a mano: comandos literales, cifras, una pregunta que necesita dos documentos, un documento de acceso restringido y 5 preguntas sin respuesta en el corpus. Detalles en [`docs/corpus.md`](docs/corpus.md); licencias en [`LICENCIAS.md`](LICENCIAS.md).

## Resultados

Recuperación evaluada sobre las 23 preguntas del conjunto que tienen respuesta en el corpus. Un acierto es que alguno de los k primeros fragmentos recuperados sea del documento esperado y contenga el texto de la respuesta.

| Configuración | Recall@1 | Recall@5 |
|---|---|---|
| Solo vectorial | 0,57 | 0,91 |

Por tipo de pregunta (recall@5): comandos literales 5/5, cifras 2/2, factuales 13/14, multi-hop 0/1.

**Qué fallos hay y por qué:**

- **"¿Cuál es la regla de oro del rebase?"** La traducción española de Pro Git no usa "rebase" ni "regla de oro", sino "reorganizar": *"Nunca reorganices confirmaciones que hayas enviado a un repositorio público"*. La búsqueda llega al documento correcto pero no a ese fragmento.
- **"¿Qué tipo de base de datos usa Kubernetes para guardar los Secrets?"** Necesita combinar dos documentos (los Secrets se guardan en etcd; etcd es un almacén clave-valor). Una sola búsqueda no lo resuelve.
- **Recall@1 de 0,57:** el fragmento correcto suele estar entre los 5 primeros, pero no en el primer puesto. Es el margen que debería cubrir un reranker.

Cada ejecución de la evaluación se guarda con fecha en `eval/resultados/`.

## Decisiones técnicas

| Decisión | Motivo |
|---|---|
| Embeddings `intfloat/multilingual-e5-large` en local | Documentos en español; los datos no salen de la máquina |
| Troceado en dos fases: por cabeceras Markdown y después en fragmentos de 800 caracteres con 100 de solape | Cada fragmento pertenece a una sola sección. El tamaño es un punto de partida, pendiente de comparar con otros |
| Ruta de secciones al inicio de cada fragmento (p. ej. `Pods > Uso de Pods`) | El fragmento conserva su contexto aunque se recupere suelto |
| Evaluación por contenido (`texto_esperado`), no por id de fragmento | Se puede cambiar el troceado sin rehacer el conjunto de evaluación |

## Stack

Python · ChromaDB · sentence-transformers · LangChain text splitters

## Limitaciones

- Conjunto de evaluación pequeño (23 preguntas con respuesta): los números orientan, no son concluyentes.
- Por ahora solo se mide la recuperación; todavía no se generan respuestas.
- El modelo de embeddings se ejecuta en CPU: la primera ingesta tarda varios minutos.

## Ejecutarlo en local

```bash
git clone https://github.com/laloba04/rag-notas.git
cd rag-notas
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt   # incluye torch para CPU; la primera vez descarga ~2 GB de modelo
python ingest.py                  # indexa los documentos de data/ en chroma/
python retrieve.py "¿Qué es un Pod?"
python evaluate.py                # reproduce la tabla de resultados
```

## Estructura

```
ingest.py              troceado, embeddings e indexación en Chroma
retrieve.py            búsqueda vectorial
evaluate.py            recall@k sobre el conjunto de evaluación
data/                  los 20 documentos del corpus
eval/golden_set.json   preguntas de evaluación
eval/resultados/       resultados de cada evaluación, con fecha
extra/                 documento para la prueba de prompt injection
scripts/               descarga y limpieza del corpus
```
