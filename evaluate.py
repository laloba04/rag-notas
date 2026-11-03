"""
Evaluación con el golden set.

Uso (desde la raíz del repo, después de python ingest.py):
    python evaluate.py           # búsqueda: recall@k de cada configuración, desglosado por tipo
    python evaluate.py --grafo   # respuestas: pipeline actual frente al grafo (necesita el LLM)

Una pregunta cuenta como acierto en recall@k si alguno de los k primeros trozos
es de una de sus fuentes esperadas y contiene su texto_esperado.
"""
import json
import statistics
import sys
import time
from collections import Counter, defaultdict
from dataclasses import replace
from datetime import datetime
from pathlib import Path

from retrieve import buscar

K_MAX = 10          # buscamos 10 trozos y miramos en qué puesto aparece el bueno
KS = [1, 3, 5, 10]  # recall@1, @3, @5 y @10 salen de la misma búsqueda

# Cada configuración es un nombre y los parámetros que se pasan a buscar().
CONFIGURACIONES = {
    "vectorial": {"rerank": False, "hibrida": False},
    "vectorial+reranker": {"rerank": True, "hibrida": False},
    "hibrida": {"rerank": False, "hibrida": True},
    "hibrida+reranker": {"rerank": True, "hibrida": True},
}

golden = json.load(open("eval/golden_set.json", encoding="utf-8"))
# Las preguntas sin respuesta (texto_esperado = None) se miden más adelante, con el LLM.
con_respuesta = [q for q in golden if q["texto_esperado"]]


def puesto_del_acierto(q, parametros):
    """Devuelve (puesto del primer trozo correcto o None si no está en los K_MAX, segundos)."""
    t0 = time.perf_counter()
    resultados = buscar(q["pregunta"], k=K_MAX, **parametros)
    segundos = time.perf_counter() - t0
    for puesto, (texto, meta, _) in enumerate(resultados, start=1):
        if meta["fuente"] in q["fuentes"] and q["texto_esperado"].lower() in texto.lower():
            return puesto, segundos
    return None, segundos


def recall(filas, k):
    return sum(1 for r in filas if r["puesto"] and r["puesto"] <= k) / len(filas)


def latencia_media(filas):
    return statistics.mean(r["segundos"] for r in filas)


def evaluar(nombre, parametros):
    print(f"=== {nombre} ===")
    resultados = []
    for q in con_respuesta:
        puesto, segundos = puesto_del_acierto(q, parametros)
        resultados.append({"id": q["id"], "tipo": q["tipo"], "pregunta": q["pregunta"],
                           "puesto": puesto, "segundos": round(segundos, 3)})

    for k in KS:
        print(f"recall@{k:<2} = {recall(resultados, k):.2f}")
    print(f"latencia media = {latencia_media(resultados):.2f} s")

    print("\nrecall@5 por tipo:")
    por_tipo = defaultdict(list)
    for r in resultados:
        por_tipo[r["tipo"]].append(r)
    for tipo, filas in sorted(por_tipo.items()):
        aciertos = sum(1 for r in filas if r["puesto"] and r["puesto"] <= 5)
        print(f"  {tipo:15s} {aciertos}/{len(filas)}")

    print("\nFallos en recall@5 (puesto en el que apareció, si salió en el top 10):")
    for r in resultados:
        if not r["puesto"] or r["puesto"] > 5:
            donde = f"puesto {r['puesto']}" if r["puesto"] else "fuera del top 10"
            print(f"  {r['id']:>2} ({r['tipo']}, {donde}): {r['pregunta']}")

    # Guardamos cada ejecución con fecha, para que las tablas del README sean reproducibles.
    carpeta = Path("eval/resultados")
    carpeta.mkdir(exist_ok=True)
    salida = carpeta / f"{datetime.now():%Y-%m-%d_%H%M}_{nombre.replace('+', '_')}.json"
    salida.write_text(json.dumps({
        "configuracion": nombre,
        **{f"recall@{k}": round(recall(resultados, k), 3) for k in KS},
        "latencia_media_s": round(latencia_media(resultados), 3),
        "preguntas": resultados,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nGuardado en {salida}\n")
    return resultados


def evaluar_busqueda():
    # Calentamiento: la primera búsqueda carga los modelos y el índice BM25 (>10 s);
    # sin esto, esa carga inflaría la latencia de la primera configuración.
    buscar("calentamiento", rerank=True, hibrida=True)
    todos ={nombre: evaluar(nombre, parametros) for nombre, parametros in CONFIGURACIONES.items()}

    # Tabla resumen para copiar al README.
    print("| Configuración | " + " | ".join(f"Recall@{k}" for k in KS) + " | Latencia media |")
    print("|---" * (len(KS) + 2) + "|")
    for nombre, resultados in todos.items():
        print(f"| {nombre} | " + " | ".join(f"{recall(resultados, k):.2f}".replace(".", ",") for k in KS)
              + f" | {latencia_media(resultados):.2f} s |".replace(".", ","))


# ---------------------------------------------------------------------------
# Respuestas: pipeline actual (generate.py) frente al grafo (grafo.py)
# ---------------------------------------------------------------------------

PREGUNTA_TRAMPA = "¿Cuál es la última versión estable de Kubernetes?"


def dice_no_lo_tengo(respuesta):
    from config import NO_LO_TENGO
    return NO_LO_TENGO.lower().rstrip(".") in respuesta.lower()


def acierta_recall(q, docs):
    """Recall@5 sobre los chunks que de verdad se le pasan al LLM para responder."""
    return any(meta["fuente"] in q["fuentes"] and q["texto_esperado"].lower() in texto.lower()
               for texto, meta, _ in docs[:5])


def ejecutar_actual(pregunta):
    import generate
    r = generate.responder(pregunta)
    return {"respuesta": r["respuesta"], "docs": r["docs"], "ruta": None, "intentos": 0,
            "segundos": r["segundos"]["busqueda"] + r["segundos"]["generacion"]}


def ejecutor_grafo(metodo):
    import grafo
    app = grafo.construir_grafo(replace(grafo.dependencias_reales(), metodo_evaluacion=metodo))

    def ejecutar(pregunta):
        r = grafo.responder(pregunta, app=app)
        return {"respuesta": r["respuesta"], "docs": r.get("docs", []), "ruta": r["ruta"],
                "intentos": r.get("intentos", 0), "segundos": r["total_s"], "error": r["error"]}
    return ejecutar


def evaluar_configuracion(nombre, ejecutar):
    print(f"=== {nombre} ===", flush=True)
    filas = []
    for q in golden:
        r = ejecutar(q["pregunta"])
        filas.append({
            "id": q["id"], "tipo": q["tipo"], "pregunta": q["pregunta"],
            "con_respuesta": bool(q["texto_esperado"]),
            "recall5": acierta_recall(q, r["docs"]) if q["texto_esperado"] else None,
            "no_lo_tengo": dice_no_lo_tengo(r["respuesta"]),
            "ruta": r["ruta"], "intentos": r["intentos"], "error": r.get("error"),
            "segundos": round(r["segundos"], 2), "respuesta": r["respuesta"],
        })
        print(f"  {q['id']:>2} {filas[-1]['segundos']:6.1f} s  "
              f"{' -> '.join(r['ruta']) if r['ruta'] else 'pipeline actual'}", flush=True)

    con = [f for f in filas if f["con_respuesta"]]
    sin = [f for f in filas if not f["con_respuesta"]]
    trampa = next(f for f in filas if f["pregunta"] == PREGUNTA_TRAMPA)
    resumen = {
        "configuracion": nombre,
        "recall@5": round(sum(f["recall5"] for f in con) / len(con), 3),
        "no_lo_tengo_en_sin_respuesta": f"{sum(f['no_lo_tengo'] for f in sin)}/{len(sin)}",
        "no_lo_tengo_en_con_respuesta": f"{sum(f['no_lo_tengo'] for f in con)}/{len(con)}",
        "latencia_media_s": round(statistics.mean(f["segundos"] for f in filas), 2),
        "latencia_mediana_s": round(statistics.median(f["segundos"] for f in filas), 2),
        "trampa_kubernetes": {
            "ruta": trampa["ruta"],
            "paso_por_responder_directo": bool(trampa["ruta"]) and "responder_directo" in trampa["ruta"],
            "respondio_no_lo_tengo": trampa["no_lo_tengo"],
        },
    }
    if filas[0]["ruta"] is not None:   # solo el grafo tiene ramas
        resumen["ramas"] = {
            "final": dict(Counter(f["ruta"][-1] for f in filas)),
            "pasan_por_reescribir": sum("reescribir" in f["ruta"] for f in filas),
            "errores": sum(bool(f["error"]) for f in filas),
        }
    print(json.dumps(resumen, ensure_ascii=False, indent=2), flush=True)

    salida = Path("eval/resultados") / f"{datetime.now():%Y-%m-%d_%H%M}_respuestas_{nombre}.json"
    salida.parent.mkdir(exist_ok=True)
    salida.write_text(json.dumps({**resumen, "preguntas": filas}, ensure_ascii=False, indent=2),
                      encoding="utf-8")
    print(f"Guardado en {salida}\n", flush=True)
    return resumen


def evaluar_respuestas():
    resumenes = [
        evaluar_configuracion("actual", ejecutar_actual),
        evaluar_configuracion("grafo_reranker", ejecutor_grafo("reranker")),
        evaluar_configuracion("grafo_llm", ejecutor_grafo("llm")),
    ]
    # Tabla resumen para copiar al README.
    print("| Configuración | Recall@5 | \"No lo tengo\" en las 5 sin respuesta "
          "| \"No lo tengo\" en las 23 con respuesta | Latencia media |")
    print("|---|---|---|---|---|")
    for r in resumenes:
        print(f"| {r['configuracion']} | {r['recall@5']:.2f} | {r['no_lo_tengo_en_sin_respuesta']} "
              f"| {r['no_lo_tengo_en_con_respuesta']} | {r['latencia_media_s']} s |".replace(".", ","))


if __name__ == "__main__":
    if "--grafo" in sys.argv:
        evaluar_respuestas()
    else:
        evaluar_busqueda()
