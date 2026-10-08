# rag-notas

Asistente de preguntas y respuestas sobre documentación técnica en español, construido con RAG (Retrieval-Augmented Generation): busca en los documentos los fragmentos relevantes para cada pregunta y responde a partir de ellos.

> Proyecto de aprendizaje personal, en desarrollo. Cada etapa se implementa primero sin frameworks para entender qué hace, y se mide antes de pasar a la siguiente.

**Estado actual:** ingesta, búsqueda vectorial, reranker, generación de respuestas con citas y evaluación de la recuperación. Pendiente: evaluar la calidad de las respuestas y búsqueda híbrida.

## Corpus

20 documentos técnicos públicos en español (documentación de Kubernetes, Pro Git y MDN Web Docs), unas 50.000 palabras, con un conjunto de evaluación de 28 preguntas escritas a mano: comandos literales, cifras, una pregunta que necesita dos documentos, un documento de acceso restringido y 5 preguntas sin respuesta en el corpus. Detalles en [`docs/corpus.md`](docs/corpus.md); licencias en [`LICENCIAS.md`](LICENCIAS.md).

## Resultados

Recuperación evaluada sobre las 23 preguntas del conjunto que tienen respuesta en el corpus. Un acierto es que alguno de los k primeros fragmentos recuperados sea del documento esperado y contenga el texto de la respuesta.

| Configuración | Recall@1 | Recall@5 | Latencia de búsqueda (CPU) |
|---|---|---|---|
| Solo vectorial | 0,57 | 0,91 | ~0,1 s |
| Vectorial + reranker (25 candidatos) | 0,65 | 0,91 | ~8 s |

Por tipo de pregunta (recall@5, igual en las dos configuraciones): comandos literales 5/5, cifras 2/2, factuales 13/14, multi-hop 0/1.

**Qué aporta el reranker:** sube el fragmento correcto al primer puesto en 6 preguntas, pero lo baja en otras 4 (de 1.º a 2.º en dos de ellas). Ganancia neta: 2 preguntas más con el fragmento correcto en primer lugar. No cambia el recall@5: los fallos están antes, en los candidatos que recibe.

**Qué fallos hay y por qué:**

- **"¿Cuál es la regla de oro del rebase?"** La traducción española de Pro Git no usa "rebase" ni "regla de oro", sino "reorganizar": *"Nunca reorganices confirmaciones que hayas enviado a un repositorio público"*. La búsqueda vectorial deja ese fragmento en el puesto 31, fuera de los 25 candidatos del reranker. Ni ampliando a 50 candidatos lo sube al top 10: es un problema de vocabulario de la pregunta, no de orden. Candidato a reescritura de la consulta.
- **"¿Qué tipo de base de datos usa Kubernetes para guardar los Secrets?"** Necesita combinar dos documentos (los Secrets se guardan en etcd; etcd es un almacén clave-valor). Una sola búsqueda no lo resuelve.

Cada ejecución de la evaluación se guarda con fecha en `eval/resultados/`.

## Decisiones técnicas

| Decisión | Motivo |
|---|---|
| Embeddings `intfloat/multilingual-e5-large` en local | Documentos en español; los datos no salen de la máquina |
| Troceado en dos fases: por cabeceras Markdown y después en fragmentos de 800 caracteres con 100 de solape | Cada fragmento pertenece a una sola sección. El tamaño es un punto de partida, pendiente de comparar con otros |
| Ruta de secciones al inicio de cada fragmento (p. ej. `Pods > Uso de Pods`) | El fragmento conserva su contexto aunque se recupere suelto |
| Reranker cross-encoder `BAAI/bge-reranker-v2-m3` sobre 25 candidatos | +0,08 de recall@1 a cambio de ~8 s por consulta en CPU. Con GPU el coste bajaría mucho |
| LLM local `qwen2.5:14b` con Ollama, servido desde otro equipo de la red con GPU (RTX 4080) | Sin coste por consulta y los documentos no salen de la red local. El firewall solo admite conexiones del equipo de desarrollo, porque Ollama no tiene autenticación |
| Prompt que obliga a usar solo el contexto, con una frase fija para "no lo tengo" y citas `[n]` | Permitir explícitamente el "no lo sé" reduce las invenciones; las citas hacen la respuesta auditable. Las citas a fragmentos inexistentes se detectan en post-proceso |
| Evaluación por contenido (`texto_esperado`), no por id de fragmento | Se puede cambiar el troceado sin rehacer el conjunto de evaluación |

## Stack

Python · ChromaDB · sentence-transformers · LangChain text splitters · Ollama

## Limitaciones

- Conjunto de evaluación pequeño (23 preguntas con respuesta): los números orientan, no son concluyentes.
- Por ahora solo se mide la recuperación; la calidad de las respuestas generadas todavía no se ha evaluado.
- El LLM es un modelo abierto de 14B cuantizado a 4 bits: responde peor que los modelos comerciales grandes.
- Los modelos se ejecutan en CPU: la primera ingesta tarda varios minutos y el reranker añade unos 8 s por consulta.

## Ejecutarlo en local

```bash
git clone https://github.com/laloba04/rag-notas.git
cd rag-notas
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt   # incluye torch para CPU; la primera vez descarga ~2 GB de modelo
cp .env.example .env             # dirección de Ollama y modelo
ollama pull qwen2.5:14b          # en el equipo que ejecute Ollama
python ingest.py                  # indexa los documentos de data/ en chroma/
python retrieve.py "¿Qué es un Pod?"               # búsqueda con reranker
python retrieve.py --sin-rerank "¿Qué es un Pod?"  # solo vectorial
python generate.py "¿Qué es un Pod?"               # respuesta con citas
python evaluate.py                # reproduce la tabla de resultados (~4 min en CPU)
```

## Estructura

```
ingest.py              troceado, embeddings e indexación en Chroma
retrieve.py            búsqueda vectorial y reranking
generate.py            prompt, llamada al LLM y comprobación de citas
evaluate.py            recall@k de cada configuración sobre el conjunto de evaluación
data/                  los 20 documentos del corpus
eval/golden_set.json   preguntas de evaluación
eval/resultados/       resultados de cada evaluación, con fecha
extra/                 documento para la prueba de prompt injection
scripts/               descarga y limpieza del corpus
```
