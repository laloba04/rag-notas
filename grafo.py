"""
Capa agéntica y correctiva con LangGraph: decide si buscar, evalúa la relevancia de lo
recuperado y, si no sirve, reescribe la pregunta una vez antes de rendirse.

Uso (desde la raíz del repo, después de python ingest.py y con .env configurado):
    python grafo.py "¿Cómo deshago el último commit?"
    python grafo.py --acceso=publico,restringido "¿Qué tamaño máximo puede tener un Secret?"

    decidir -> responder_directo                    (saludos y preguntas sobre la conversación)
    decidir -> recuperar -> evaluar -> generar      (hay chunks relevantes)
                            evaluar -> reescribir -> recuperar   (no hay, quedan reintentos)
                            evaluar -> sin_respuesta             (no hay, sin reintentos)
"""
import hashlib
import json
import operator
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Annotated, Callable, Literal, TypedDict

from langgraph.errors import GraphRecursionError
from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel, Field

import config


class Estado(TypedDict, total=False):
    pregunta: str                   # la que se usa para buscar (puede estar reescrita)
    pregunta_original: str          # la del usuario, sin tocar
    acceso: tuple                   # niveles de confidencialidad que puede ver quien pregunta
    docs: list                      # chunks como (texto, metadatos, puntuación)
    intentos: int                   # reescrituras hechas
    ruta: Annotated[list, operator.add]          # nodos recorridos, en orden
    decision: str                   # "buscar" | "directo", la toma el nodo decidir
    respuesta: str
    fuentes: list                   # metadatos de los chunks usados para responder
    # Para las trazas:
    latencias: Annotated[list, operator.add]     # [{"nodo": ..., "s": ...}, ...]
    chunks: Annotated[list, operator.add]        # [{"antes": n, "despues": m}, ...] por evaluación


@dataclass
class Dependencias:
    """Lo que el grafo necesita del exterior. En los tests se sustituye por funciones falsas."""
    decidir: Callable[[str], str]                 # pregunta -> "buscar" | "directo"
    responder_directo: Callable[[str], str]       # pregunta -> texto
    buscar: Callable[[str, tuple], list]          # (pregunta, acceso) -> [(texto, meta, puntuación)]
    juzgar: Callable[[str, str], bool]            # (pregunta, texto del chunk) -> ¿relevante?
    reescribir: Callable[[str], str]              # pregunta -> pregunta reformulada
    generar: Callable[[str, list], str]           # (pregunta, docs) -> respuesta con citas
    metodo_evaluacion: str = config.METODO_EVALUACION
    umbral_reranker: float = config.UMBRAL_RERANKER
    max_reintentos: int = config.MAX_REINTENTOS


def construir_grafo(dep: Dependencias):
    """Monta y compila el grafo con las dependencias dadas."""

    def medido(nombre, funcion):
        """Envuelve un nodo: apunta su nombre en la ruta y cuánto ha tardado."""
        def nodo(estado):
            t0 = time.perf_counter()
            cambios = funcion(estado)
            cambios["ruta"] = [nombre]
            cambios["latencias"] = [{"nodo": nombre, "s": round(time.perf_counter() - t0, 3)}]
            return cambios
        return nodo

    def decidir(estado):
        try:
            decision = dep.decidir(estado["pregunta_original"])
        except Exception:
            decision = "buscar"     # ante un error del LLM, lo seguro es buscar
        return {"decision": "directo" if decision == "directo" else "buscar"}

    def ruta_tras_decidir(estado) -> Literal["responder_directo", "recuperar"]:
        return "responder_directo" if estado["decision"] == "directo" else "recuperar"

    def responder_directo(estado):
        return {"respuesta": dep.responder_directo(estado["pregunta_original"]), "fuentes": []}

    def recuperar(estado):
        # El acceso viaja en el estado (es de cada petición, no del grafo compilado) y se
        # aplica en la búsqueda: lo que no se recupera no llega ni a evaluar ni al LLM.
        return {"docs": dep.buscar(estado["pregunta"], estado["acceso"])}

    def evaluar(estado):
        docs = estado["docs"]
        if dep.metodo_evaluacion == "llm":
            relevantes = [d for d in docs if dep.juzgar(estado["pregunta_original"], d[0])]
        else:   # "reranker": la puntuación ya viene de buscar(), no cuesta nada extra
            relevantes = [d for d in docs if d[2] >= dep.umbral_reranker]
        return {"docs": relevantes, "chunks": [{"antes": len(docs), "despues": len(relevantes)}]}

    def ruta_tras_evaluar(estado) -> Literal["generar", "reescribir", "sin_respuesta"]:
        if estado["docs"]:
            return "generar"
        if estado.get("intentos", 0) < dep.max_reintentos:
            return "reescribir"
        return "sin_respuesta"

    def reescribir(estado):
        return {"pregunta": dep.reescribir(estado["pregunta"]),
                "intentos": estado.get("intentos", 0) + 1}

    def generar(estado):
        return {"respuesta": dep.generar(estado["pregunta_original"], estado["docs"]),
                "fuentes": [meta for _, meta, _ in estado["docs"]]}

    def sin_respuesta(estado):
        return {"respuesta": config.NO_LO_TENGO, "fuentes": []}

    g = StateGraph(Estado)
    for nombre, funcion in [("decidir", decidir), ("responder_directo", responder_directo),
                            ("recuperar", recuperar), ("evaluar", evaluar),
                            ("reescribir", reescribir), ("generar", generar),
                            ("sin_respuesta", sin_respuesta)]:
        g.add_node(nombre, medido(nombre, funcion))
    g.add_edge(START, "decidir")
    g.add_conditional_edges("decidir", ruta_tras_decidir)
    g.add_edge("recuperar", "evaluar")
    g.add_conditional_edges("evaluar", ruta_tras_evaluar)
    g.add_edge("reescribir", "recuperar")
    for final in ("responder_directo", "generar", "sin_respuesta"):
        g.add_edge(final, END)

    app = g.compile()
    app.step_timeout = config.TIMEOUT_PASO_S
    app.metodo_evaluacion = dep.metodo_evaluacion   # para que la traza apunte el método real
    return app


# ---------------------------------------------------------------------------
# Dependencias reales: LLM de .env y la búsqueda de retrieve.py
# ---------------------------------------------------------------------------

# Solo estas categorías se responden sin buscar. Todo lo demás, incluido "otra", busca.
CATEGORIAS_DIRECTAS = {"saludo", "despedida", "agradecimiento", "sobre_la_conversacion"}


class Decision(BaseModel):
    categoria: Literal["saludo", "despedida", "agradecimiento", "sobre_la_conversacion",
                       "pregunta_tecnica", "otra"] = Field(
        description="Tipo de mensaje. Cualquier pregunta sobre software, comandos, "
                    "configuración o datos es pregunta_tecnica.")


class Juicio(BaseModel):
    relevante: bool = Field(description="true si el fragmento ayuda a responder la pregunta")


PROMPT_DECIDIR = """Clasifica el mensaje que un usuario envía a un asistente de documentación
técnica (Kubernetes, git, HTTP) en una de estas categorías:
- saludo, despedida, agradecimiento: mensajes sociales sin pregunta.
- sobre_la_conversacion: preguntas sobre lo que se ha dicho en esta conversación o sobre qué
  puede hacer el asistente.
- pregunta_tecnica: cualquier pregunta técnica o factual, aunque creas saber la respuesta.
- otra: cualquier otro caso.
Ante la duda, pregunta_tecnica.

Mensaje: {pregunta}"""

PROMPT_DIRECTO = """Eres un asistente de documentación técnica. Responde brevemente y en español
a este mensaje, que es un saludo o una pregunta sobre la conversación. No des ningún dato
técnico: si el mensaje pide información técnica, invita a hacer la pregunta concreta.

Mensaje: {pregunta}"""

PROMPT_JUZGAR = """¿Este fragmento de documentación contiene información que ayude a responder
la pregunta? Responde solo con el JSON pedido.

Pregunta: {pregunta}

Fragmento:
{texto}"""

PROMPT_REESCRIBIR = """La siguiente pregunta no ha encontrado fragmentos útiles en una
documentación técnica en español (Kubernetes, Pro Git y MDN). Reformúlala para mejorar la
búsqueda: usa términos técnicos y sinónimos que podrían aparecer en la documentación,
incluidas las traducciones al español de términos que suelen escribirse en inglés.
Devuelve SOLO la pregunta reformulada, sin explicaciones.

Pregunta: {pregunta}"""


def dependencias_reales():
    """Construye las dependencias con el LLM configurado en .env y la búsqueda actual."""
    from generate import SYSTEM, construir_prompt     # reutiliza el prompt del pipeline actual
    from retrieve import buscar

    llm = config.crear_llm()
    clasificador = llm.with_structured_output(Decision)
    juez = llm.with_structured_output(Juicio)

    def texto(mensaje):
        return mensaje.content.strip()

    return Dependencias(
        decidir=lambda p: ("directo" if clasificador.invoke(PROMPT_DECIDIR.format(pregunta=p))
                           .categoria in CATEGORIAS_DIRECTAS else "buscar"),
        responder_directo=lambda p: texto(llm.invoke(PROMPT_DIRECTO.format(pregunta=p))),
        buscar=lambda p, acceso: buscar(p, k=config.K, rerank=True, acceso=acceso),
        juzgar=lambda p, t: juez.invoke(PROMPT_JUZGAR.format(pregunta=p, texto=t)).relevante,
        reescribir=lambda p: texto(llm.invoke(PROMPT_REESCRIBIR.format(pregunta=p))),
        generar=lambda p, docs: texto(llm.invoke(
            [("system", SYSTEM), ("user", construir_prompt(p, docs))])),
    )


# ---------------------------------------------------------------------------
# Ejecución con límites y trazas
# ---------------------------------------------------------------------------

_app = None


def app_por_defecto():
    global _app
    if _app is None:
        _app = construir_grafo(dependencias_reales())
    return _app


def guardar_traza(pregunta, estado, total_s, metodo_evaluacion, error=None):
    """Una línea JSON por respuesta. La pregunta NO se guarda: solo un hash y su longitud."""
    traza = {
        "fecha": datetime.now().isoformat(timespec="seconds"),
        "pregunta_hash": hashlib.sha256(pregunta.encode("utf-8")).hexdigest()[:12],
        "pregunta_longitud": len(pregunta),
        "modelo": config.LLM_MODELO,
        "metodo_evaluacion": metodo_evaluacion,
        "acceso": list(estado.get("acceso", [])),
        "ruta": estado.get("ruta", []),
        "reintentos": estado.get("intentos", 0),
        "latencias": estado.get("latencias", []),
        "chunks": estado.get("chunks", []),
        "total_s": round(total_s, 3),
        "error": error,
    }
    fichero = Path(config.FICHERO_TRAZAS)
    fichero.parent.mkdir(parents=True, exist_ok=True)
    with fichero.open("a", encoding="utf-8") as f:
        f.write(json.dumps(traza, ensure_ascii=False) + "\n")


def responder(pregunta, app=None, trazar=True, acceso=None):
    """Ejecuta el grafo con el límite de pasos y devuelve el estado final.
    acceso: niveles que puede ver quien pregunta (None = config.ACCESO_POR_DEFECTO)."""
    from retrieve import normalizar_acceso     # no carga modelos: solo valida los niveles
    app = app or app_por_defecto()
    inicial = {"pregunta": pregunta, "pregunta_original": pregunta, "intentos": 0,
               "acceso": normalizar_acceso(acceso)}
    t0 = time.perf_counter()
    error = None
    try:
        estado = app.invoke(inicial, config={"recursion_limit": config.MAX_PASOS})
    except (GraphRecursionError, TimeoutError) as e:
        # Si algo se sale de los límites, la salida segura es no responder.
        error = type(e).__name__
        estado = {**inicial, "ruta": ["error"], "respuesta": config.NO_LO_TENGO, "fuentes": []}
    total = time.perf_counter() - t0
    if trazar:
        guardar_traza(pregunta, estado, total, app.metodo_evaluacion, error)
    return {**estado, "total_s": round(total, 3), "error": error}


if __name__ == "__main__":
    from retrieve import separar_acceso
    acceso, args = separar_acceso(sys.argv[1:])
    pregunta = " ".join(args) or "¿Cómo deshago el último commit?"
    r = responder(pregunta, acceso=acceso)
    print(f"Pregunta: {pregunta}\n")
    print(r["respuesta"])
    if r.get("fuentes"):
        print("\nFuentes:")
        for n, meta in enumerate(r["fuentes"], start=1):
            print(f"  [{n}] {meta['fuente']} > {meta['seccion']}")
    print(f"\nRuta: {' -> '.join(r['ruta'])} · reintentos: {r.get('intentos', 0)} · {r['total_s']} s")
