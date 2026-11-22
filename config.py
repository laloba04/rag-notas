"""
Configuración común: proveedor y modelo del LLM (siempre desde .env) y límites del grafo.
"""
import os

from dotenv import load_dotenv

load_dotenv()

# Frase fija cuando la documentación no cubre la pregunta (la usan generate.py y el grafo).
NO_LO_TENGO = "No lo tengo en la documentación."

# --- LLM: proveedor y modelo SOLO por variables de entorno, nunca en el código ---
# Ollama por defecto: los datos solo salen de casa si alguien elige un proveedor externo en .env.
LLM_PROVEEDOR = os.environ.get("LLM_PROVEEDOR", "ollama")
LLM_MODELO = os.environ.get("LLM_MODELO")            # p. ej. "qwen2.5:14b"
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434")
PROVEEDORES = ("ollama", "groq")
# Proveedores que corren en nuestra red. Cualquier otro (incluido uno desconocido) se trata
# como externo: falla en cerrado.
PROVEEDORES_LOCALES = ("ollama",)
# Destino de lo que devuelve el servidor MCP (mcp_server.py): lo lee el modelo del cliente, que
# no controlamos y puede correr fuera de la red. No está en PROVEEDORES_LOCALES: es externo.
CLIENTE_MCP = "cliente_mcp"
# Lo mismo para lo que devuelve la API web (api.py): lo lee quien esté al otro lado de internet.
CLIENTE_API = "cliente_api"

# --- API web (api.py) y servidor MCP ---
VERSION = "1.0.0"
# Pregunta más larga que se acepta. Las del golden set no pasan de ~110 caracteres; 500 deja
# margen para una pregunta real y corta prompts enormes que solo gastan cuota.
LONGITUD_MAX_PREGUNTA = 500
# Orígenes del frontend que pueden llamar a la API desde el navegador, separados por comas.
# Nunca "*": un comodín dejaría a cualquier web usar la API (y la cuota) desde el navegador
# de quien la visite.
CORS_ORIGENES = [o.strip() for o in os.environ.get("CORS_ORIGENES", "http://localhost:4200").split(",")
                 if o.strip() and o.strip() != "*"]
# Peticiones por minuto y por IP a /buscar y /preguntar.
LIMITE_POR_MINUTO = int(os.environ.get("LIMITE_POR_MINUTO", "10"))
# Tope diario de /preguntar para todo el servidor, sea cual sea la IP. Con Groq (1000
# peticiones al día en esta cuenta) y entre 2 y 4 llamadas al LLM por pregunta con el grafo,
# 200 preguntas deja margen. Se cuenta en memoria: vuelve a 0 al reiniciar el proceso.
LIMITE_PREGUNTAS_DIA = int(os.environ.get("LIMITE_PREGUNTAS_DIA", "200"))
# IP de los proxies de confianza (separadas por comas). Solo si la petición llega desde uno de
# ellos se lee la IP real de X-Forwarded-For; si no, esa cabecera se ignora, porque cualquiera
# puede escribirla y saltarse así el límite por IP.
PROXIES_DE_CONFIANZA = {p.strip() for p in os.environ.get("PROXIES_DE_CONFIANZA", "").split(",")
                        if p.strip()}
# Tiempo máximo de una petición a la API (búsqueda + grafo completo, incluida la espera a que
# termine otra petición). Por encima, 504.
TIMEOUT_PETICION_S = float(os.environ.get("TIMEOUT_PETICION_S", "180"))
# Cargar los modelos al arrancar la API, en segundo plano (~20 s en CPU). Desactivado por defecto:
# en local, la API arranca al momento y la primera consulta tarda más. En un despliegue conviene
# activarlo para que el primer visitante no espere la carga.
CARGAR_MODELOS_AL_ARRANCAR = os.environ.get("CARGAR_MODELOS_AL_ARRANCAR", "false").lower() == "true"

# LLM juez (lo usa la evaluación de respuestas). Si no se define, es el mismo que el principal.
# Si se define el proveedor, el modelo es obligatorio: un nombre de Ollama no vale en Groq.
LLM_JUEZ_PROVEEDOR = os.environ.get("LLM_JUEZ_PROVEEDOR")
LLM_JUEZ_MODELO = os.environ.get("LLM_JUEZ_MODELO")
# Reintentos del juez ante errores transitorios (429 por el límite de Groq, 5xx, cortes). Se
# respeta la cabecera retry-after; si no viene, espera exponencial: 2, 4, 8, 16, 32 s (máx. 60).
JUEZ_REINTENTOS = int(os.environ.get("JUEZ_REINTENTOS", "5"))
JUEZ_ESPERA_BASE_S = 2.0
JUEZ_ESPERA_MAX_S = 60.0

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
# Candidatos que se le pasan al reranker (y que aporta cada búsqueda antes de fusionar).
K_INICIAL = 25

# --- Búsqueda híbrida (vectorial + BM25, fusionadas con Reciprocal Rank Fusion) ---
# Desactivada por defecto hasta medirla con evaluate.py.
HIBRIDA = os.environ.get("HIBRIDA", "false").lower() == "true"
# Constante de RRF: puntuación = suma de 1 / (RRF_K + puesto). 60 es el valor del artículo
# original (Cormack et al., 2009); suaviza la diferencia entre los primeros puestos.
RRF_K = 60

# --- Permisos ---
# Niveles de confidencialidad que existen en el frontmatter de data/*.md.
NIVELES_ACCESO = ("publico", "restringido")
# Mínimo privilegio: si quien llama no dice nada, solo ve lo público. Va fijo en el código
# y no en .env a propósito: una línea olvidada en .env no debe poder abrir todo el corpus.
ACCESO_POR_DEFECTO = ("publico",)
# Lo único que puede salir hacia un proveedor externo. Si algún fragmento tiene otro nivel (o
# ninguno), no se envía nada y salta un error. Fijo en el código por la misma razón.
NIVELES_PARA_EXTERNOS = ("publico",)

# --- Prompt injection ---
# Contexto delimitado y marcado como datos no confiables, instrucción de no obedecerlo y
# validación de la salida. Solo se apaga para medir el "antes" en scripts/prueba_injection.py;
# igual que el acceso, no se lee de .env: una línea olvidada no debe poder desactivarlo.
MITIGAR_INJECTION = True

# Trazas del grafo: una línea JSON por respuesta.
FICHERO_TRAZAS = os.environ.get("FICHERO_TRAZAS", "logs/trazas.jsonl")


def comprobar_llm(proveedor=None, modelo=None):
    """Falla con un mensaje claro si falta el proveedor, el modelo o la clave en .env.
    Sin argumentos comprueba el LLM principal."""
    proveedor = proveedor or LLM_PROVEEDOR
    modelo = modelo or LLM_MODELO
    if not proveedor or not modelo:
        raise RuntimeError(
            "Falta configurar el LLM: define LLM_PROVEEDOR y LLM_MODELO en .env "
            "(mira .env.example).")
    if proveedor not in PROVEEDORES:
        raise RuntimeError(
            f"Proveedor de LLM no soportado: {proveedor!r}. Opciones: {', '.join(PROVEEDORES)}")
    if proveedor == "groq" and not os.environ.get("GROQ_API_KEY"):
        raise RuntimeError("Falta GROQ_API_KEY en .env (mira .env.example).")


def crear_llm(proveedor=None, modelo=None):
    """Devuelve el modelo de chat de LangChain según .env (sin argumentos, el principal). Es el
    único sitio que conoce a los proveedores; cada paquete se importa solo si se usa."""
    proveedor = proveedor or LLM_PROVEEDOR
    modelo = modelo or LLM_MODELO
    comprobar_llm(proveedor, modelo)
    if proveedor == "groq":
        from langchain_groq import ChatGroq
        return ChatGroq(
            model=modelo,
            api_key=os.environ["GROQ_API_KEY"],
            temperature=TEMPERATURA,
            timeout=TIMEOUT_LLM_S,
        )
    from langchain_ollama import ChatOllama
    return ChatOllama(
        model=modelo,
        base_url=OLLAMA_URL,
        temperature=TEMPERATURA,
        client_kwargs={"timeout": TIMEOUT_LLM_S},
    )


def configuracion_juez():
    """(proveedor, modelo) del LLM juez. Sin LLM_JUEZ_* en .env, el mismo que el principal."""
    if not LLM_JUEZ_PROVEEDOR and not LLM_JUEZ_MODELO:
        return LLM_PROVEEDOR, LLM_MODELO
    return LLM_JUEZ_PROVEEDOR or LLM_PROVEEDOR, LLM_JUEZ_MODELO


def crear_llm_juez():
    """El modelo de chat del juez, con la misma fábrica que el principal."""
    proveedor, modelo = configuracion_juez()
    if not modelo:
        raise RuntimeError("Falta LLM_JUEZ_MODELO en .env: es obligatorio si defines "
                           "LLM_JUEZ_PROVEEDOR (mira .env.example).")
    return crear_llm(proveedor, modelo)


def es_externo(proveedor):
    """¿Los datos salen de nuestra red al usar este proveedor?"""
    return proveedor not in PROVEEDORES_LOCALES


def comprobar_envio(docs, proveedor):
    """Antes de mandar fragmentos a un LLM: si el proveedor es externo y alguno no es público
    (o no tiene etiqueta), no se manda nada. Es independiente del filtro de búsqueda: aunque
    alguien pida acceso total, lo restringido no sale de casa.
    docs: [(texto, metadatos, puntuación)], como los devuelve retrieve.buscar."""
    if not es_externo(proveedor):
        return
    bloqueados = sorted({meta.get("fuente", "?") for _, meta, _ in docs
                         if meta.get("confidencialidad") not in NIVELES_PARA_EXTERNOS})
    if bloqueados:
        raise PermissionError(
            f"No se envían fragmentos no públicos al proveedor externo {proveedor!r}: "
            f"{', '.join(bloqueados)}. Usa un proveedor local o acceso solo público.")
