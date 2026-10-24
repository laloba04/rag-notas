"""
Configuración común: proveedor y modelo del LLM (siempre desde .env) y límites del grafo.
"""
import os

from dotenv import load_dotenv

load_dotenv()

# Frase fija cuando la documentación no cubre la pregunta (la usan generate.py y el grafo).
NO_LO_TENGO = "No lo tengo en la documentación."

# --- LLM: proveedor y modelo SOLO por variables de entorno, nunca en el código ---
LLM_PROVEEDOR = os.environ.get("LLM_PROVEEDOR")      # p. ej. "ollama"
LLM_MODELO = os.environ.get("LLM_MODELO")            # p. ej. "qwen2.5:14b"
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434")
PROVEEDORES = ("ollama",)

# Temperatura baja en todos los nodos: queremos fidelidad al contexto, no creatividad.
TEMPERATURA = float(os.environ.get("LLM_TEMPERATURA", "0.1"))
# Tiempo máximo de cada llamada al LLM. Holgado porque la primera carga el modelo en la GPU.
TIMEOUT_LLM_S = float(os.environ.get("TIMEOUT_LLM_S", "120"))

# --- Límites del grafo ---
# Una sola reescritura: si la pregunta reformulada tampoco encuentra nada útil, es más
# probable que el corpus no tenga la respuesta que otra reformulación la encuentre, y
# cada reintento cuesta una búsqueda completa (~13 s en CPU) más una llamada al LLM.
MAX_REINTENTOS = 1
# Camino más largo posible con 1 reintento: decidir, recuperar, evaluar, reescribir,
# recuperar, evaluar, generar = 7 pasos. 10 deja margen y corta cualquier bucle por error.
MAX_PASOS = 10
# Tiempo máximo de un paso del grafo (una búsqueda con reranker en CPU ronda los 15 s).
TIMEOUT_PASO_S = 180

# --- Evaluación de relevancia de los chunks ---
# "reranker": usa la puntuación del reranker que ya devuelve buscar(); no añade llamadas.
# "llm": un LLM juez dice sí/no por chunk; más caro (una llamada por chunk).
METODO_EVALUACION = os.environ.get("METODO_EVALUACION", "reranker")
# bge-reranker-v2-m3 devuelve una probabilidad (0-1). 0,5 es su frontera de decisión:
# por encima, el modelo considera más probable que el chunk sea relevante que no.
# No se calibra con el golden set (28 preguntas): sería sobreajustarlo.
UMBRAL_RERANKER = float(os.environ.get("UMBRAL_RERANKER", "0.5"))

# Nº de chunks que se recuperan en cada búsqueda.
K = 5

# Trazas del grafo: una línea JSON por respuesta.
FICHERO_TRAZAS = os.environ.get("FICHERO_TRAZAS", "logs/trazas.jsonl")


def comprobar_llm():
    """Falla con un mensaje claro si falta el proveedor o el modelo en .env."""
    if not LLM_PROVEEDOR or not LLM_MODELO:
        raise RuntimeError(
            "Falta configurar el LLM: define LLM_PROVEEDOR y LLM_MODELO en .env "
            "(mira .env.example).")
    if LLM_PROVEEDOR not in PROVEEDORES:
        raise RuntimeError(
            f"Proveedor de LLM no soportado: {LLM_PROVEEDOR!r}. Opciones: {', '.join(PROVEEDORES)}")


def crear_llm():
    """Devuelve el modelo de chat de LangChain según .env."""
    comprobar_llm()
    from langchain_ollama import ChatOllama
    return ChatOllama(
        model=LLM_MODELO,
        base_url=OLLAMA_URL,
        temperature=TEMPERATURA,
        client_kwargs={"timeout": TIMEOUT_LLM_S},
    )
