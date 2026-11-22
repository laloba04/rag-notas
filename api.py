"""
API web de rag-notas (FastAPI). Envuelve lo que ya existe; no reimplementa nada:
- POST /buscar     -> retrieve.buscar con reranker, sin LLM.
- POST /preguntar  -> grafo.responder o generate.responder. Con "Accept: text/event-stream",
                      en streaming (SSE): el progreso del grafo nodo a nodo y, al final, la
                      respuesta ya validada.
- GET  /salud      -> estado de la colección y del LLM, y versión.

Seguridad (los mismos principios que el resto del proyecto):
- Acceso siempre config.ACCESO_POR_DEFECTO (solo "publico"). No hay autenticación, así que el
  nivel nunca sale de la petición: un campo "acceso" en el cuerpo se rechaza (422).
- Antes de devolver nada, servicio.comprobar_salida(..., config.CLIENTE_API): si llegara algo no
  público, la petición falla y no se devuelve ningún fragmento.
- El streaming es del progreso, no de los tokens: la respuesta solo sale cuando ha pasado la
  validación de salida (generate.validar_salida). Mandar los tokens según se generan enseñaría
  una respuesta con prompt injection antes de poder bloquearla.
- Límite por IP y tope diario de preguntas (protege la cuota del LLM), timeouts, CORS solo para
  los orígenes de config.CORS_ORIGENES y errores sin trazas internas.

Uso (desde la raíz del repo, con el entorno activado):
    uvicorn api:app                 # http://127.0.0.1:8000 (documentación en /docs)
"""
import asyncio
import json
import logging
import re
import threading
import time
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from datetime import date
from typing import Literal, Optional

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

import config
import servicio
from servicio import K_MAX, K_MIN, ErrorServicio

log = logging.getLogger("rag-notas.api")

ESTADO_HTTP = {"peticion": 400, "no_disponible": 503, "bloqueado": 500, "timeout": 504}


# ---------------------------------------------------------------------------
# Modelos de entrada y salida
# ---------------------------------------------------------------------------

class PeticionBuscar(BaseModel):
    # extra="forbid": un campo que no existe (por ejemplo "acceso") da 422 en vez de ignorarse
    # en silencio, para que nadie crea que ha pedido otro nivel de acceso.
    model_config = ConfigDict(extra="forbid")
    pregunta: str = Field(min_length=1, max_length=config.LONGITUD_MAX_PREGUNTA)
    k: int = Field(5, ge=K_MIN, le=K_MAX)


class PeticionPreguntar(BaseModel):
    model_config = ConfigDict(extra="forbid")
    pregunta: str = Field(min_length=1, max_length=config.LONGITUD_MAX_PREGUNTA)
    modo: Literal["grafo", "directo"] = "grafo"


class Fragmento(BaseModel):
    numero: int                 # el [n] con el que lo cita la respuesta
    documento: str
    seccion: str
    url: str
    puntuacion: float
    texto: str                  # contenido NO confiable: el cliente debe pintarlo como texto


class Traza(BaseModel):
    ruta: list[str]
    reintentos: int
    pregunta_reescrita: Optional[str]
    latencias: list[dict]
    error: Optional[str]


class Respuesta(BaseModel):
    respuesta: str
    no_lo_tengo: bool           # la respuesta es exactamente "No lo tengo…" (un resultado válido)
    salida_bloqueada: bool      # la validación de salida la cambió por "No lo tengo…"
    modo: str
    citas: list[Fragmento]      # los fragmentos que cita la respuesta con [n]
    traza: Optional[Traza]      # solo en modo grafo
    segundos: float


class RespuestaBuscar(BaseModel):
    fragmentos: list[Fragmento]


# ---------------------------------------------------------------------------
# Límite de peticiones (en memoria: un solo proceso; se pierde al reiniciar)
# ---------------------------------------------------------------------------

class Limitador:
    """Ventana deslizante de un minuto por IP y un tope diario global de preguntas."""

    def __init__(self, por_minuto, preguntas_dia, reloj=time.monotonic, hoy=date.today):
        self.por_minuto, self.preguntas_dia = por_minuto, preguntas_dia
        self.reloj, self.hoy = reloj, hoy
        self.recientes = defaultdict(deque)     # ip -> instantes de sus últimas peticiones
        self.dia, self.preguntas = None, 0

    def comprobar(self, ip, es_pregunta):
        """None si se admite (y se cuenta); si no, (mensaje, segundos para reintentar)."""
        ahora = self.reloj()
        cola = self.recientes[ip]
        while cola and ahora - cola[0] >= 60:
            cola.popleft()
        if len(cola) >= self.por_minuto:
            return (f"Demasiadas peticiones: el límite es {self.por_minuto} por minuto.",
                    int(60 - (ahora - cola[0])) + 1)
        if es_pregunta:
            if self.dia != self.hoy():
                self.dia, self.preguntas = self.hoy(), 0
            if self.preguntas >= self.preguntas_dia:
                return ("Se ha alcanzado el límite diario de preguntas del servidor; vuelve "
                        "mañana. La búsqueda (/buscar) sigue disponible.", 3600)
            self.preguntas += 1
        cola.append(ahora)
        # Las IP sin peticiones en el último minuto no ocupan memoria.
        for otra in [i for i, c in self.recientes.items() if not c]:
            del self.recientes[otra]
        return None


LIMITADOR = Limitador(config.LIMITE_POR_MINUTO, config.LIMITE_PREGUNTAS_DIA)


def ip_cliente(request):
    """IP de quien llama. X-Forwarded-For solo cuenta si la conexión viene de un proxy de
    confianza; entonces se toma el primer salto, desde la derecha, que no sea un proxy (los de
    la izquierda los puede escribir cualquiera)."""
    ip = request.client.host if request.client else "desconocida"
    if ip not in config.PROXIES_DE_CONFIANZA:
        return ip
    saltos = [s.strip() for s in request.headers.get("x-forwarded-for", "").split(",") if s.strip()]
    for salto in reversed(saltos):
        if salto not in config.PROXIES_DE_CONFIANZA:
            return salto
    return ip


def limitar(request, es_pregunta):
    rechazo = LIMITADOR.comprobar(ip_cliente(request), es_pregunta)
    if rechazo:
        mensaje, segundos = rechazo
        raise ErrorLimite(mensaje, segundos)


class ErrorLimite(Exception):
    def __init__(self, mensaje, segundos):
        super().__init__(mensaje)
        self.segundos = segundos


# ---------------------------------------------------------------------------
# Lo que se ejecuta en un hilo (los modelos y el LLM son síncronos)
# ---------------------------------------------------------------------------

def fragmentos(docs):
    return [Fragmento(numero=n, documento=meta.get("fuente", ""), seccion=meta.get("seccion", ""),
                      url=meta.get("url", ""), puntuacion=round(float(p or 0), 3), texto=texto or "")
            for n, (texto, meta, p) in enumerate(docs, start=1)]


def es_no_lo_tengo(respuesta):
    """¿La respuesta es solo "No lo tengo en la documentación." (con o sin citas [n])? Que la
    frase aparezca dentro de una respuesta más larga no cuenta: ahí hay contenido que enseñar."""
    def normalizar(texto):
        return re.sub(r"\[\d+\]", "", texto).strip().rstrip(".").strip().lower()
    return normalizar(respuesta) == normalizar(config.NO_LO_TENGO)


def buscar_sync(pregunta, k):
    import retrieve
    servicio.comprobar_coleccion()
    docs = retrieve.buscar(pregunta, k=k, rerank=True, acceso=config.ACCESO_POR_DEFECTO)
    servicio.modelos_cargados = True
    servicio.comprobar_salida(docs, config.CLIENTE_API)
    return RespuestaBuscar(fragmentos=fragmentos(docs))


class Cancelado(Exception):
    """El cliente se ha ido: no merece la pena seguir."""


def preguntar_sync(pregunta, modo, cancelado, avisar=None):
    """cancelado: threading.Event que se activa si el cliente se va. El grafo se para al terminar
    el nodo en curso (un nodo a medias, como una llamada al LLM, no se puede interrumpir desde
    fuera). avisar: función opcional que recibe el nombre de cada nodo (para el SSE)."""
    def al_pasar(nodo):
        if cancelado.is_set():
            raise Cancelado()
        if avisar:
            avisar(nodo)

    servicio.comprobar_coleccion()
    servicio.comprobar_llm()
    t0 = time.perf_counter()
    try:
        with servicio.errores_claros():
            if modo == "grafo":
                import grafo
                r = grafo.responder(pregunta, acceso=config.ACCESO_POR_DEFECTO, al_pasar=al_pasar)
            else:
                import generate
                r = generate.responder(pregunta, acceso=config.ACCESO_POR_DEFECTO)
    except PermissionError:
        # config.comprobar_envio hacia el LLM: no debería pasar con acceso solo público.
        log.error("PermissionError hacia el LLM con acceso %s", config.ACCESO_POR_DEFECTO)
        raise ErrorServicio("Bloqueado: la consulta habría enviado fragmentos no públicos al "
                            "LLM. Es un error de configuración del servidor.", "bloqueado") from None
    if r.get("ruta", ["recuperar"]) != ["decidir", "responder_directo"]:
        servicio.modelos_cargados = True        # la búsqueda (con reranker) ya ha corrido
    docs = list(r.get("docs", []))
    fuentes = r.get("fuentes", [])
    # Lo que vio el generador y las fuentes: ninguno puede ser no público.
    servicio.comprobar_salida(docs + [(None, m, None) for m in fuentes], config.CLIENTE_API)
    respuesta = r["respuesta"]
    no_lo_tengo = es_no_lo_tengo(respuesta)
    # Citas: solo los fragmentos que la respuesta cita con [n], con el número del prompt. Si la
    # salida se bloqueó o no hubo generación (sin fuentes), o si la respuesta es "No lo tengo",
    # no hay citas: enseñar el resto del contexto haría creer que la respuesta se apoya en él.
    citadas = {int(n) for n in re.findall(r"\[(\d+)\]", respuesta)}
    citas = [] if not fuentes or no_lo_tengo else [f for f in fragmentos(docs) if f.numero in citadas]
    traza = None
    if modo == "grafo":
        reescrita = r.get("pregunta") if r.get("pregunta") != pregunta else None
        traza = Traza(ruta=r.get("ruta", []), reintentos=r.get("intentos", 0),
                      pregunta_reescrita=reescrita, latencias=r.get("latencias", []),
                      error=r.get("error"))
    return Respuesta(
        respuesta=respuesta,
        no_lo_tengo=no_lo_tengo, salida_bloqueada=bool(r.get("salida_bloqueada", False)),
        modo=modo, citas=citas, traza=traza, segundos=round(time.perf_counter() - t0, 2))


async def en_hilo(funcion, *args, cancelado=None):
    """Ejecuta en un hilo, de una en una (servicio.CERROJO) y con el timeout de la petición.
    Si al conseguir el turno la petición ya está cancelada (el cliente se fue mientras esperaba),
    no se ejecuta. Si se pasa del tiempo, se responde 504 y se cancela: el grafo se para al
    terminar el nodo en curso y suelta el cerrojo."""
    cancelado = cancelado or threading.Event()

    def con_cerrojo():
        with servicio.CERROJO:
            if cancelado.is_set():
                raise Cancelado()
            return funcion(*args)
    try:
        return await asyncio.wait_for(asyncio.to_thread(con_cerrojo), config.TIMEOUT_PETICION_S)
    except asyncio.TimeoutError:
        cancelado.set()
        raise ErrorServicio("La consulta ha tardado demasiado; prueba otra vez.", "timeout") from None


# Tareas que siguen tras irse el cliente (terminan en el nodo en curso). Se guardan para que no
# las recoja el recolector de basura antes de acabar.
_pendientes = set()
# Cada cuánto se mira si el cliente de una petición sin streaming se ha ido.
VIGILAR_CADA_S = 0.5


async def vigilando(request, funcion, *args):
    """en_hilo que, mientras espera, mira cada VIGILAR_CADA_S si el cliente se ha ido; si se ha
    ido, cancela (para las peticiones sin streaming, en las que nadie leerá la respuesta)."""
    cancelado = threading.Event()
    trabajo = asyncio.ensure_future(en_hilo(funcion, *args, cancelado, cancelado=cancelado))
    while not trabajo.done():
        await asyncio.wait({trabajo}, timeout=VIGILAR_CADA_S)
        if not trabajo.done() and await request.is_disconnected():
            cancelado.set()
            log.info("Cliente desconectado: se cancela la consulta")
            _pendientes.add(trabajo)
            trabajo.add_done_callback(_pendientes.discard)
            raise Cancelado()
    return trabajo.result()


# ---------------------------------------------------------------------------
# Aplicación
# ---------------------------------------------------------------------------

def precargar():
    """Carga los modelos al arrancar (CARGAR_MODELOS_AL_ARRANCAR). En un hilo aparte: el servidor
    escucha desde el principio y /salud responde mientras tanto; las preguntas que lleguen
    esperan el turno (servicio.CERROJO) en vez de cargar los modelos dos veces."""
    t0 = time.perf_counter()
    try:
        servicio.cargar_modelos()
        log.info("Modelos cargados en %.1f s", time.perf_counter() - t0)
    except Exception:
        log.exception("No se han podido cargar los modelos al arrancar")


@asynccontextmanager
async def ciclo_de_vida(app):
    # Los mensajes de rag-notas (cancelaciones, bloqueos, precarga) a stderr, junto a los de
    # uvicorn. Nunca llevan el texto de la pregunta.
    raiz = logging.getLogger("rag-notas")
    if not raiz.handlers:
        manejador = logging.StreamHandler()
        manejador.setFormatter(logging.Formatter("%(asctime)s %(name)s %(levelname)s %(message)s"))
        raiz.addHandler(manejador)
        raiz.setLevel(logging.INFO)
    if config.CARGAR_MODELOS_AL_ARRANCAR:
        threading.Thread(target=precargar, name="precarga", daemon=True).start()
    yield


app = FastAPI(title="rag-notas", version=config.VERSION, lifespan=ciclo_de_vida,
              description="RAG sobre documentación técnica en español (Kubernetes, Git y HTTP).")
app.add_middleware(CORSMiddleware, allow_origins=config.CORS_ORIGENES,
                   allow_methods=["GET", "POST"], allow_headers=["Content-Type", "Accept"])


def cuerpo_error(mensaje, tipo):
    return {"error": mensaje, "tipo": tipo}


@app.exception_handler(ErrorServicio)
async def _error_servicio(request, e):
    return JSONResponse(cuerpo_error(str(e), e.tipo), status_code=ESTADO_HTTP.get(e.tipo, 500))


@app.exception_handler(Cancelado)
async def _cancelado(request, e):
    # Nadie va a leer esta respuesta: el cliente ya se fue. 499 es el código que usa nginx.
    return JSONResponse(cuerpo_error("Consulta cancelada: el cliente se ha desconectado.",
                                     "cancelado"), status_code=499)


@app.exception_handler(ErrorLimite)
async def _error_limite(request, e):
    return JSONResponse(cuerpo_error(str(e), "limite"), status_code=429,
                        headers={"Retry-After": str(e.segundos)})


@app.exception_handler(RequestValidationError)
async def _error_validacion(request, e):
    # Solo los campos y el motivo: no se devuelve lo que envió el cliente.
    campos = [{"campo": ".".join(str(p) for p in err["loc"] if p != "body"), "motivo": err["type"]}
              for err in e.errors()]
    return JSONResponse({**cuerpo_error("Petición no válida.", "peticion"), "campos": campos},
                        status_code=422)


@app.exception_handler(Exception)
async def _error_inesperado(request, e):
    log.exception("Error inesperado en %s", request.url.path)
    return JSONResponse(cuerpo_error("Error interno del servidor.", "interno"), status_code=500)


@app.post("/buscar", response_model=RespuestaBuscar)
async def buscar(peticion: PeticionBuscar, request: Request):
    limitar(request, es_pregunta=False)
    pregunta = servicio.comprobar_pregunta(peticion.pregunta)
    return await en_hilo(buscar_sync, pregunta, peticion.k)


@app.post("/preguntar", response_model=Respuesta,
          responses={200: {"content": {"text/event-stream": {}}}})
async def preguntar(peticion: PeticionPreguntar, request: Request):
    limitar(request, es_pregunta=True)
    pregunta = servicio.comprobar_pregunta(peticion.pregunta)
    if "text/event-stream" in request.headers.get("accept", ""):
        return StreamingResponse(eventos(pregunta, peticion.modo), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
    return await vigilando(request, preguntar_sync, pregunta, peticion.modo)


def evento(tipo, datos):
    return f"event: {tipo}\ndata: {json.dumps(datos, ensure_ascii=False)}\n\n"


async def eventos(pregunta, modo):
    """SSE: "nodo" por cada nodo del grafo al terminarlo (solo el nombre), y al final
    "respuesta" (ya validada) o "error". En modo directo no hay nodos: solo el final."""
    bucle = asyncio.get_running_loop()
    cola = asyncio.Queue()
    cancelado = threading.Event()

    def avisar(nodo):           # se llama desde el hilo del grafo
        bucle.call_soon_threadsafe(cola.put_nowait, evento("nodo", {"nodo": nodo}))

    async def trabajo():
        try:
            r = await en_hilo(preguntar_sync, pregunta, modo, cancelado, avisar,
                              cancelado=cancelado)
            await cola.put(evento("respuesta", r.model_dump()))
        except Cancelado:
            log.info("Cliente desconectado: consulta cancelada")
        except ErrorServicio as e:
            await cola.put(evento("error", cuerpo_error(str(e), e.tipo)))
        except Exception:
            log.exception("Error inesperado en /preguntar (streaming)")
            await cola.put(evento("error", cuerpo_error("Error interno del servidor.", "interno")))
        finally:
            await cola.put(None)

    tarea = asyncio.create_task(trabajo())
    try:
        while (mensaje := await cola.get()) is not None:
            yield mensaje
    finally:
        # Si el cliente cierra la conexión, Starlette cancela este generador y se llega aquí sin
        # haber terminado: se avisa al grafo para que se pare en el siguiente nodo y no se espera.
        cancelado.set()
        if not tarea.done():
            _pendientes.add(tarea)
            tarea.add_done_callback(_pendientes.discard)


@app.get("/salud")
def salud():
    """No usa servicio.CERROJO, así que responde aunque haya una consulta en curso. Rápido y sin
    cargar modelos: cuenta los fragmentos directamente en Chroma y, con Ollama,
    comprueba que responde (3 s como mucho). Con Groq solo comprueba la configuración, para no
    gastar cuota. No devuelve direcciones internas."""
    n = servicio.contar_fragmentos()
    llm_ok, comprobado = servicio.llm_responde()
    coleccion_ok = bool(n)
    cuerpo = {
        "estado": "ok" if coleccion_ok and llm_ok else "degradado",
        "version": config.VERSION,
        "coleccion": {"ok": coleccion_ok, "fragmentos": n or 0},
        "llm": {"proveedor": config.LLM_PROVEEDOR, "modelo": config.LLM_MODELO, "ok": llm_ok,
                "comprobado": comprobado},
        "modelos_cargados": servicio.modelos_cargados,
    }
    # Sin colección no funciona nada: 503. Sin LLM, /buscar sigue funcionando: 200 "degradado".
    return JSONResponse(cuerpo, status_code=200 if coleccion_ok else 503)
