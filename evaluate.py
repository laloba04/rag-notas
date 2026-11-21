"""
Evaluación con el golden set.

Uso (desde la raíz del repo, después de python ingest.py):
    python evaluate.py           # búsqueda: recall@k de cada configuración, desglosado por tipo
    python evaluate.py --grafo   # respuestas: pipeline actual frente al grafo (necesita el LLM)
    python evaluate.py --permisos  # filtro por permisos: búsqueda y respuestas con acceso público y total
    python evaluate.py --juzgar [ficheros]  # juez de fidelidad y corrección sobre los resultados de --grafo
    python evaluate.py --acuerdo eval/revision/<fichero>.json  # % de acuerdo con el juez, tras revisar a mano

Una pregunta cuenta como acierto en recall@k si alguno de los k primeros trozos
es de una de sus fuentes esperadas y contiene su texto_esperado.
MRR@10 (Mean Reciprocal Rank) usa la misma definición de acierto: la media de 1/puesto del
primer trozo correcto, con 0 si no está entre los 10 primeros.

La evaluación de búsqueda y la de --grafo miden CALIDAD, así que se hacen con acceso total
(todos los niveles): así los números son comparables con los anteriores al filtro y la
pregunta del documento restringido no cuenta como fallo de la búsqueda. El filtro se mide
aparte, con --permisos.
"""
import json
import statistics
import sys
import time
from collections import Counter, defaultdict
from dataclasses import replace
from datetime import datetime
from pathlib import Path

import config
from retrieve import buscar

K_MAX = 10          # buscamos 10 trozos y miramos en qué puesto aparece el bueno
KS = [1, 3, 5, 10]  # recall@1, @3, @5 y @10 salen de la misma búsqueda

# Acceso total para medir calidad (ver la cabecera); el filtro se mide con --permisos.
ACCESO_TOTAL = config.NIVELES_ACCESO

# Cada configuración es un nombre y los parámetros que se pasan a buscar().
CONFIGURACIONES = {
    "vectorial": {"rerank": False, "hibrida": False, "acceso": ACCESO_TOTAL},
    "vectorial+reranker": {"rerank": True, "hibrida": False, "acceso": ACCESO_TOTAL},
    "hibrida": {"rerank": False, "hibrida": True, "acceso": ACCESO_TOTAL},
    "hibrida+reranker": {"rerank": True, "hibrida": True, "acceso": ACCESO_TOTAL},
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


def mrr(filas):
    """Mean Reciprocal Rank: premia que el trozo correcto salga arriba, no solo que salga.
    Puesto 1 vale 1; puesto 2, 0,5; puesto 5, 0,2; fuera del top K_MAX, 0."""
    return sum(1 / r["puesto"] for r in filas if r["puesto"]) / len(filas)


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
    print(f"MRR@{K_MAX}     = {mrr(resultados):.2f}")
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
        f"mrr@{K_MAX}": round(mrr(resultados), 3),
        "latencia_media_s": round(latencia_media(resultados), 3),
        "preguntas": resultados,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nGuardado en {salida}\n")
    return resultados


def evaluar_busqueda():
    # Calentamiento: la primera búsqueda carga los modelos y el índice BM25 (>10 s);
    # sin esto, esa carga inflaría la latencia de la primera configuración.
    buscar("calentamiento", rerank=True, hibrida=True, acceso=ACCESO_TOTAL)
    todos ={nombre: evaluar(nombre, parametros) for nombre, parametros in CONFIGURACIONES.items()}

    # Tabla resumen para copiar al README.
    print("| Configuración | " + " | ".join(f"Recall@{k}" for k in KS)
          + f" | MRR@{K_MAX} | Latencia media |")
    print("|---" * (len(KS) + 3) + "|")
    for nombre, resultados in todos.items():
        print(f"| {nombre} | " + " | ".join(f"{recall(resultados, k):.2f}".replace(".", ",") for k in KS)
              + f" | {mrr(resultados):.2f}".replace(".", ",")
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


def ejecutar_actual(pregunta, acceso=ACCESO_TOTAL):
    import generate
    r = generate.responder(pregunta, acceso=acceso)
    return {"respuesta": r["respuesta"], "cruda": r["respuesta_cruda"],
            "bloqueada": r["salida_bloqueada"], "docs": r["docs"], "ruta": None, "intentos": 0,
            "segundos": r["segundos"]["busqueda"] + r["segundos"]["generacion"]}


def ejecutor_grafo(metodo):
    import grafo
    app = grafo.construir_grafo(replace(grafo.dependencias_reales(), metodo_evaluacion=metodo))

    def ejecutar(pregunta, acceso=ACCESO_TOTAL):
        r = grafo.responder(pregunta, app=app, acceso=acceso)
        return {"respuesta": r["respuesta"], "cruda": r.get("respuesta_cruda"),
                "bloqueada": r.get("salida_bloqueada", False),
                "docs": r.get("docs", []), "ruta": r["ruta"],
                "intentos": r.get("intentos", 0), "segundos": r["total_s"], "error": r["error"]}
    return ejecutar


def evaluar_configuracion(nombre, ejecutar):
    print(f"=== {nombre} ===", flush=True)
    filas = []
    for q in golden:
        r = ejecutar(q["pregunta"])
        filas.append({
            "id": q["id"], "tipo": q["tipo"], "pregunta": q["pregunta"],
            "con_respuesta": bool(q["texto_esperado"]), "referencia": q.get("respuesta"),
            "recall5": acierta_recall(q, r["docs"]) if q["texto_esperado"] else None,
            "no_lo_tengo": dice_no_lo_tengo(r["respuesta"]),
            "ruta": r["ruta"], "intentos": r["intentos"], "error": r.get("error"),
            "segundos": round(r["segundos"], 2), "respuesta": r["respuesta"],
            "respuesta_cruda": r["cruda"], "salida_bloqueada": r["bloqueada"],
            # Lo que recibió el LLM, para que el juez pueda comprobar la fidelidad después.
            "fragmentos": [{"texto": texto, "fuente": meta.get("fuente"),
                            "seccion": meta.get("seccion"),
                            "confidencialidad": meta.get("confidencialidad"),
                            "puntuacion": round(float(punt), 4)}
                           for texto, meta, punt in r["docs"]],
        })
        print(f"  {q['id']:>2} {filas[-1]['segundos']:6.1f} s  "
              f"{' -> '.join(r['ruta']) if r['ruta'] else 'pipeline actual'}", flush=True)

    con = [f for f in filas if f["con_respuesta"]]
    sin = [f for f in filas if not f["con_respuesta"]]
    trampa = next(f for f in filas if f["pregunta"] == PREGUNTA_TRAMPA)
    resumen = {
        "configuracion": nombre,
        "proveedor": config.LLM_PROVEEDOR, "modelo": config.LLM_MODELO,
        "recall@5": round(sum(f["recall5"] for f in con) / len(con), 3),
        "no_lo_tengo_en_sin_respuesta": f"{sum(f['no_lo_tengo'] for f in sin)}/{len(sin)}",
        "no_lo_tengo_en_con_respuesta": f"{sum(f['no_lo_tengo'] for f in con)}/{len(con)}",
        "salidas_bloqueadas": sum(f["salida_bloqueada"] for f in filas),
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


# ---------------------------------------------------------------------------
# Permisos: el documento restringido no debe salir con acceso público
# ---------------------------------------------------------------------------

def fuentes_restringidas():
    """Ficheros con confidencialidad distinta de "publico", leídos del frontmatter de data/."""
    import frontmatter
    return sorted(r.name for r in Path("data").glob("*.md")
                  if frontmatter.load(r).get("confidencialidad") != "publico")


def evaluar_permisos():
    restringidas = fuentes_restringidas()
    accesos = {"publico": ("publico",), "total": ACCESO_TOTAL}
    print(f"Documentos no públicos: {', '.join(restringidas)}\n")

    # 1) Búsqueda, sin LLM: las 28 preguntas, mirando los K_INICIAL candidatos (lo que recibe
    #    el reranker; el reranker solo reordena, no añade nada). Con acceso público tiene que
    #    salir 0; con acceso total se cuenta también, para ver que la prueba no es vacía.
    busqueda = {}
    print("=== Búsqueda: trozos restringidos entre los candidatos de las 28 preguntas ===")
    for modo in ("vectorial", "hibrida"):
        for nombre_acceso, acceso in accesos.items():
            trozos = preguntas = 0
            for q in golden:
                docs = buscar(q["pregunta"], k=config.K_INICIAL, rerank=False,
                              hibrida=modo == "hibrida", acceso=acceso)
                n = sum(meta["fuente"] in restringidas for _, meta, _ in docs)
                trozos += n
                preguntas += n > 0
            busqueda[f"{modo}_{nombre_acceso}"] = {"trozos": trozos, "preguntas": preguntas}
            print(f"  {modo:9s} acceso {nombre_acceso:7s}: {trozos} trozos restringidos "
                  f"en {preguntas} de {len(golden)} preguntas", flush=True)

    # 2) Respuestas, con el LLM: las preguntas de tipo "restringido", por los dos pipelines.
    print("\n=== Respuestas a las preguntas sobre el documento restringido ===")
    respuestas = []
    ejecutores = {"actual": ejecutar_actual, "grafo_reranker": ejecutor_grafo("reranker")}
    for q in (q for q in golden if q["tipo"] == "restringido"):
        for nombre, ejecutar in ejecutores.items():
            for nombre_acceso, acceso in accesos.items():
                r = ejecutar(q["pregunta"], acceso=acceso)
                fila = {
                    "id": q["id"], "pregunta": q["pregunta"], "pipeline": nombre,
                    "acceso": nombre_acceso,
                    "trozos_restringidos": sum(m["fuente"] in restringidas for _, m, _ in r["docs"]),
                    "recall5": acierta_recall(q, r["docs"]),
                    "no_lo_tengo": dice_no_lo_tengo(r["respuesta"]),
                    "segundos": round(r["segundos"], 2), "respuesta": r["respuesta"],
                }
                respuestas.append(fila)
                print(f"  {q['id']:>2} {nombre:14s} acceso {nombre_acceso:7s}: "
                      f"{fila['trozos_restringidos']} trozos restringidos, "
                      f"{'dice' if fila['no_lo_tengo'] else 'NO dice'} \"No lo tengo\"", flush=True)
                print(f"     -> {r['respuesta'][:200]!r}", flush=True)

    salida = Path("eval/resultados") / f"{datetime.now():%Y-%m-%d_%H%M}_permisos.json"
    salida.parent.mkdir(exist_ok=True)
    salida.write_text(json.dumps({"fuentes_restringidas": restringidas, "busqueda": busqueda,
                                  "respuestas": respuestas}, ensure_ascii=False, indent=2),
                      encoding="utf-8")
    print(f"\nGuardado en {salida}")


# ---------------------------------------------------------------------------
# Juez de fidelidad y corrección, sobre los resultados guardados por --grafo
# ---------------------------------------------------------------------------

CONFIGURACIONES_RESPUESTAS = ("actual", "grafo_reranker", "grafo_llm")


def ultimos_resultados():
    """El fichero más reciente de cada configuración de --grafo."""
    carpeta = Path("eval/resultados")
    ficheros = []
    for nombre in CONFIGURACIONES_RESPUESTAS:
        candidatos = sorted(carpeta.glob(f"*_respuestas_{nombre}.json"))
        if candidatos:
            ficheros.append(candidatos[-1])
    return ficheros


def docs_de(fila):
    """Los fragmentos guardados, con la forma que devuelve retrieve.buscar."""
    return [(f["texto"], {"fuente": f["fuente"], "seccion": f["seccion"],
                          "confidencialidad": f["confidencialidad"]}, f["puntuacion"])
            for f in fila["fragmentos"]]


def juzgar_fila(fila, juez, proveedor_juez):
    import juez as modulo_juez
    if not fila["con_respuesta"]:
        return {"estado": "no_aplica", "motivo": "sin_respuesta"}   # se miden con "No lo tengo"
    if fila["no_lo_tengo"] or not fila["fragmentos"]:
        return {"estado": "no_aplica", "motivo": "no_lo_tengo"}     # no hay nada que juzgar
    try:
        return modulo_juez.juzgar(fila["pregunta"], fila["referencia"], fila["respuesta"],
                                  docs_de(fila), juez, proveedor_juez)
    except PermissionError:
        # Fragmentos no públicos y juez externo: no se envían. No es un veredicto.
        return modulo_juez.sin_juzgar("restringido")


def evaluar_juez(ficheros=None):
    import juez as modulo_juez
    ficheros = [Path(f) for f in ficheros] if ficheros else ultimos_resultados()
    if not ficheros:
        sys.exit("No hay resultados de --grafo que juzgar. Ejecuta antes: python evaluate.py --grafo")
    proveedor_juez, modelo_juez = config.configuracion_juez()
    llm_juez = config.crear_llm_juez().with_structured_output(modulo_juez.Veredicto)

    resumenes, por_configuracion = [], {}
    for fichero in ficheros:
        datos = json.loads(fichero.read_text(encoding="utf-8"))
        nombre = datos["configuracion"]
        if any("fragmentos" not in f for f in datos["preguntas"]):
            sys.exit(f"{fichero} no guarda los fragmentos que recibió el LLM (es anterior al juez): "
                     "repite python evaluate.py --grafo")
        generador = (datos.get("proveedor"), datos.get("modelo"))
        igual = generador == (proveedor_juez, modelo_juez)
        print(f"=== Juez {proveedor_juez}/{modelo_juez} sobre {nombre} "
              f"(generó {generador[0]}/{generador[1]}) ===", flush=True)
        if igual:
            print("⚠️  AVISO: el juez es el mismo modelo que generó las respuestas: se evalúa a sí "
                  "mismo y tiende a darse por bueno. Define LLM_JUEZ_PROVEEDOR y LLM_JUEZ_MODELO.",
                  flush=True)

        filas = []
        for f in datos["preguntas"]:
            v = juzgar_fila(f, llm_juez, proveedor_juez)
            filas.append({**f, "juez": v})
            detalle = (f"fidelidad {v['fidelidad']:.2f}, {v['correccion']}" if v["estado"] == "juzgada"
                       else f"{v['estado']} ({v['motivo']})")
            print(f"  {f['id']:>2} {detalle}", flush=True)

        resumen = {"configuracion": nombre, "fichero_respuestas": str(fichero),
                   "generador": {"proveedor": generador[0], "modelo": generador[1]},
                   "juez": {"proveedor": proveedor_juez, "modelo": modelo_juez},
                   "juez_igual_a_generador": igual, **modulo_juez.resumir(filas)}
        print(json.dumps(resumen, ensure_ascii=False, indent=2), flush=True)
        salida = Path("eval/resultados") / f"{datetime.now():%Y-%m-%d_%H%M}_juez_{nombre}.json"
        salida.write_text(json.dumps({**resumen, "preguntas": filas}, ensure_ascii=False, indent=2),
                          encoding="utf-8")
        print(f"Guardado en {salida}\n", flush=True)
        resumenes.append(resumen)
        por_configuracion[nombre] = filas

    muestra = modulo_juez.muestra_para_revisar(por_configuracion)
    revision = Path("eval/revision") / f"{datetime.now():%Y-%m-%d_%H%M}_muestra_juez.json"
    revision.parent.mkdir(exist_ok=True)
    revision.write_text(json.dumps(muestra, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Muestra de {len(muestra)} veredictos para revisar a mano: {revision}")
    print("Rellena \"de_acuerdo\" (true/false) en cada uno y luego: "
          f"python evaluate.py --acuerdo {revision}\n")

    print("| Configuración | Juzgadas | Fidelidad media | Todo respaldado | Correctas | Parciales "
          "| Incorrectas | No juzgadas |")
    print("|---|---|---|---|---|---|---|---|")
    for r in resumenes:
        c = r["correccion"]
        fid = f"{r['fidelidad_media']:.2f}".replace(".", ",") if r["fidelidad_media"] is not None else "-"
        print(f"| {r['configuracion']} | {r['juzgadas']} | {fid} | {r['todo_respaldado']} "
              f"| {c['correcta']} | {c['parcial']} | {c['incorrecta']} | {r['no_juzgadas']} |")


def mostrar_acuerdo(fichero):
    import juez as modulo_juez
    revision = json.loads(Path(fichero).read_text(encoding="utf-8"))
    resultado = modulo_juez.porcentaje_de_acuerdo(revision)
    if resultado is None:
        print("Todavía no hay ningún veredicto revisado (de_acuerdo true/false).")
    else:
        print(f"Acuerdo con el juez: {resultado[0]} % en {resultado[1]} de {len(revision)} revisados")


if __name__ == "__main__":
    if "--juzgar" in sys.argv:
        evaluar_juez([a for a in sys.argv[1:] if not a.startswith("--")])
    elif "--acuerdo" in sys.argv:
        mostrar_acuerdo(sys.argv[sys.argv.index("--acuerdo") + 1])
    elif "--permisos" in sys.argv:
        evaluar_permisos()
    elif "--grafo" in sys.argv:
        evaluar_respuestas()
    else:
        evaluar_busqueda()
