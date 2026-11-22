"""
Comprobaciones comunes a las dos puertas de entrada que no son la línea de comandos: el
servidor MCP (mcp_server.py) y la API web (api.py). Cada una traduce ErrorServicio a su
formato (ToolError en MCP, una respuesta HTTP en la API).

Nada de aquí reimplementa la búsqueda, la generación ni los controles de seguridad: los llama
(retrieve, config.comprobar_envio) y convierte sus fallos en mensajes claros.
"""
import logging
import threading
from contextlib import contextmanager

import config

log = logging.getLogger("rag-notas")

K_MIN, K_MAX = 1, 10

# Los modelos no se comparten entre hilos y cargarlos dos veces a la vez gastaría el doble de
# RAM: una sola búsqueda o generación por proceso a la vez.
CERROJO = threading.Lock()
# True cuando una búsqueda completa (embeddings + reranker) ha terminado: a partir de ahí, las
# consultas ya no pagan la carga. Lo enseña /salud.
modelos_cargados = False


class ErrorServicio(Exception):
    """Un fallo previsto, con un mensaje que se puede enseñar a quien llama.
    tipo: "peticion" (la pregunta no vale), "no_disponible" (colección o LLM),
    "bloqueado" (falla en cerrado por permisos) o "timeout"."""
    def __init__(self, mensaje, tipo):
        super().__init__(mensaje)
        self.tipo = tipo


def comprobar_pregunta(pregunta):
    """La pregunta sin espacios sobrantes. Vacía o demasiado larga es un error."""
    pregunta = (pregunta or "").strip()
    if not pregunta:
        raise ErrorServicio("La pregunta está vacía: escribe la pregunta en español.", "peticion")
    if len(pregunta) > config.LONGITUD_MAX_PREGUNTA:
        raise ErrorServicio(f"La pregunta es demasiado larga (máximo "
                            f"{config.LONGITUD_MAX_PREGUNTA} caracteres).", "peticion")
    return pregunta


def limitar_k(k):
    """k dentro de [K_MIN, K_MAX]. Devuelve (k usado, ¿se ha cambiado?)."""
    usado = min(max(k, K_MIN), K_MAX)
    return usado, usado != k


def comprobar_salida(docs, destino):
    """Falla en cerrado: si algún fragmento no es público, no se devuelve nada. El mensaje no
    nombra los documentos; el detalle va al log del servidor.
    docs: [(texto, metadatos, puntuación)]; destino: config.CLIENTE_MCP o config.CLIENTE_API."""
    try:
        config.comprobar_envio(docs, destino)
    except PermissionError as e:
        log.error("Salida bloqueada: %s", e)
        raise ErrorServicio("Bloqueado: la salida contenía fragmentos no públicos y no se "
                            "devuelve nada. Es un error de configuración del servidor, no de la "
                            "pregunta.", "bloqueado") from None


def comprobar_coleccion():
    """La colección "docs" existe y tiene fragmentos. Carga el modelo de embeddings la primera vez."""
    import chromadb.errors
    import retrieve
    try:
        n = retrieve.coleccion().count()
    except chromadb.errors.NotFoundError:
        raise ErrorServicio("No existe la colección de documentos: hay que ejecutar "
                            "'python ingest.py' en el servidor.", "no_disponible") from None
    if n == 0:
        raise ErrorServicio("La colección de documentos está vacía: hay que ejecutar "
                            "'python ingest.py' en el servidor.", "no_disponible")


def cargar_modelos():
    """Carga el modelo de embeddings y el reranker con una búsqueda de prueba, que es lo único que
    garantiza que los dos están en memoria. Con el cerrojo: si llega una pregunta, espera."""
    import retrieve
    global modelos_cargados
    with CERROJO:
        retrieve.buscar("calentamiento", k=1, rerank=True, acceso=config.ACCESO_POR_DEFECTO)
        modelos_cargados = True


def contar_fragmentos():
    """Fragmentos de la colección sin cargar el modelo de embeddings (para /salud). None si no
    existe la colección."""
    import chromadb
    import chromadb.errors
    try:
        return chromadb.PersistentClient(path="./chroma").get_collection("docs").count()
    except chromadb.errors.NotFoundError:
        return None


def error_ollama(mostrar_url):
    """Mensaje para Ollama apagado. La URL (con la IP de la red local) solo se enseña a quien ya
    está en el equipo, como el cliente MCP por stdio; nunca en la API pública."""
    donde = f" (Ollama en {config.OLLAMA_URL})" if mostrar_url else ""
    return (f"No se puede conectar con el LLM{donde}: puede que el equipo esté apagado. La "
            "búsqueda sigue funcionando sin LLM.")


def llm_responde():
    """¿El LLM está configurado y, si es Ollama, responde en 3 s? Devuelve (ok, comprobado):
    con Groq solo se comprueba la configuración, para no gastar cuota en cada comprobación."""
    try:
        config.comprobar_llm()
    except RuntimeError:
        return False, True
    if config.LLM_PROVEEDOR != "ollama":
        return True, False
    import httpx
    try:
        httpx.get(f"{config.OLLAMA_URL}/api/tags", timeout=3).raise_for_status()
        return True, True
    except httpx.HTTPError:
        return False, True


def comprobar_llm(mostrar_url=False):
    """Como llm_responde, pero falla con un mensaje claro. Así un equipo apagado da error en 3 s
    en vez de esperar al timeout de la generación."""
    try:
        config.comprobar_llm()
    except RuntimeError as e:
        raise ErrorServicio(f"El LLM del servidor no está configurado: {e}", "no_disponible") from None
    ok, _ = llm_responde()
    if not ok:
        raise ErrorServicio(error_ollama(mostrar_url), "no_disponible")


@contextmanager
def errores_claros(mostrar_url=False):
    """Traduce los fallos previsibles del LLM a ErrorServicio. Lo demás sigue como excepción y
    cada puerta de entrada lo convierte en un error genérico, sin traza."""
    try:
        yield
    except ConnectionError:         # el cliente de Ollama la lanza si no puede conectar
        raise ErrorServicio(error_ollama(mostrar_url), "no_disponible") from None
    except TimeoutError:
        raise ErrorServicio("El LLM ha tardado demasiado en responder; prueba otra vez.",
                            "timeout") from None
