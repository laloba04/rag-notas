"""
Graba la demo estática de la web: hace las preguntas de verdad a la pila en marcha (API real,
por SSE, igual que la web) y guarda lo que llega en web/demo/grabaciones.json (solo entra en el build "demo").

Por cada pregunta y modo guarda los nodos del grafo con el momento en que llegaron (ms desde que
se envió la pregunta, medido aquí) y la respuesta completa, sin tocarla. La web en modo demo
reproduce esos nodos a la misma velocidad.

Falla en cerrado: si una respuesta da error, o si cita algo que no sea público, no escribe nada.

Uso (desde la raíz del repo, con la pila arrancada y el LLM accesible):
    python scripts/grabar_demo.py                       # contra http://127.0.0.1:8088/api
    python scripts/grabar_demo.py --api http://127.0.0.1:8000
"""
import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path

import frontmatter
import httpx

RAIZ = Path(__file__).resolve().parent.parent
SALIDA = RAIZ / "web" / "demo" / "grabaciones.json"

# Las tres sugerencias de la portada (web/src/app/chat/chat.ts) y una que no está en el corpus.
PREGUNTAS = [
    "¿Para qué sirve git stash?",
    "¿Para qué sirve la cabecera Cache-Control?",
    "¿Qué es un ReplicaSet?",
    "¿Cuál es la capital de Francia?",
]
MODOS = ["grafo", "directo"]


def leer_eventos(lineas):
    """Convierte las líneas de un flujo SSE en pares (tipo, datos). Un evento acaba en línea vacía."""
    tipo, datos = "message", ""
    for linea in lineas:
        if linea == "":
            if datos:
                yield tipo, json.loads(datos)
            tipo, datos = "message", ""
        elif linea.startswith("event:"):
            tipo = linea[6:].strip()
        elif linea.startswith("data:"):
            datos += linea[5:].strip()
    if datos:
        yield tipo, json.loads(datos)


def niveles_por_documento():
    """Nivel de confidencialidad de cada documento de data/, leído de su frontmatter."""
    return {p.name: frontmatter.load(p).get("confidencialidad")
            for p in (RAIZ / "data").glob("*.md")}


def comprobar_publico(respuesta, niveles):
    """Lanza un error si la respuesta cita algún documento que no sea público (o desconocido)."""
    for cita in respuesta["citas"]:
        if niveles.get(cita["documento"]) != "publico":
            raise RuntimeError(f"La respuesta cita {cita['documento']!r}, que no es público.")


def grabar(cliente, pregunta, modo):
    inicio = time.perf_counter()
    nodos = []
    with cliente.stream("POST", "/preguntar", json={"pregunta": pregunta, "modo": modo},
                        headers={"Accept": "text/event-stream"}) as r:
        if r.status_code != 200:
            raise RuntimeError(f"La API respondió {r.status_code}: {r.read().decode()[:200]}")
        for tipo, datos in leer_eventos(r.iter_lines()):
            ms = round((time.perf_counter() - inicio) * 1000)
            if tipo == "nodo":
                nodos.append({"nodo": datos["nodo"], "ms": ms})
            elif tipo == "respuesta":
                return {"pregunta": pregunta, "modo": modo, "nodos": nodos, "ms_total": ms,
                        "respuesta": datos}
            elif tipo == "error":
                raise RuntimeError(f"Error de la API: {datos}")
    raise RuntimeError("El flujo terminó sin respuesta.")


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    parser.add_argument("--api", default="http://127.0.0.1:8088/api")
    args = parser.parse_args()

    niveles = niveles_por_documento()
    with httpx.Client(base_url=args.api, timeout=httpx.Timeout(10, read=300)) as cliente:
        salud = cliente.get("/salud").raise_for_status().json()
        if not (salud["coleccion"]["ok"] and salud["llm"]["ok"]):
            sys.exit(f"La pila no está lista: {salud}")
        grabaciones = []
        for pregunta in PREGUNTAS:
            for modo in MODOS:
                g = grabar(cliente, pregunta, modo)
                comprobar_publico(g["respuesta"], niveles)
                r = g["respuesta"]
                estado = ("bloqueada" if r["salida_bloqueada"] else
                          "no lo tengo" if r["no_lo_tengo"] else f"{len(r['citas'])} citas")
                print(f"{modo:8} {g['ms_total'] / 1000:6.1f} s  {estado:12} {pregunta}")
                grabaciones.append(g)

    SALIDA.parent.mkdir(parents=True, exist_ok=True)
    SALIDA.write_text(json.dumps({
        "grabado": datetime.now().astimezone().isoformat(timespec="seconds"),
        "llm": {"proveedor": salud["llm"]["proveedor"], "modelo": salud["llm"]["modelo"]},
        "fragmentos": salud["coleccion"]["fragmentos"],
        "grabaciones": grabaciones,
    }, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"\nGuardado en {SALIDA.relative_to(RAIZ)}")


if __name__ == "__main__":
    main()
