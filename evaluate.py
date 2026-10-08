"""
Evaluación de la búsqueda con el golden set: recall@k, desglosado por tipo de pregunta.

Uso (desde la raíz del repo, después de python ingest.py):  python evaluate.py

Una pregunta cuenta como acierto en recall@k si alguno de los k primeros trozos
es de una de sus fuentes esperadas y contiene su texto_esperado.
"""
import json
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from retrieve import buscar

K_MAX = 10          # buscamos 10 trozos y miramos en qué puesto aparece el bueno
KS = [1, 3, 5, 10]  # recall@1, @3, @5 y @10 salen de la misma búsqueda

# Cada configuración es un nombre y los parámetros que se pasan a buscar().
CONFIGURACIONES = {
    "vectorial": {"rerank": False},
    "vectorial+reranker": {"rerank": True},
}

golden = json.load(open("eval/golden_set.json", encoding="utf-8"))
# Las preguntas sin respuesta (texto_esperado = None) se miden más adelante, con el LLM.
con_respuesta = [q for q in golden if q["texto_esperado"]]


def puesto_del_acierto(q, parametros):
    """Devuelve el puesto (1, 2, ...) del primer trozo correcto, o None si no está en los K_MAX."""
    for puesto, (texto, meta, _) in enumerate(buscar(q["pregunta"], k=K_MAX, **parametros), start=1):
        if meta["fuente"] in q["fuentes"] and q["texto_esperado"].lower() in texto.lower():
            return puesto
    return None


def recall(filas, k):
    return sum(1 for r in filas if r["puesto"] and r["puesto"] <= k) / len(filas)


def evaluar(nombre, parametros):
    print(f"=== {nombre} ===")
    resultados = [
        {"id": q["id"], "tipo": q["tipo"], "pregunta": q["pregunta"],
         "puesto": puesto_del_acierto(q, parametros)}
        for q in con_respuesta
    ]

    for k in KS:
        print(f"recall@{k:<2} = {recall(resultados, k):.2f}")

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
        "preguntas": resultados,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nGuardado en {salida}\n")
    return resultados


todos = {nombre: evaluar(nombre, parametros) for nombre, parametros in CONFIGURACIONES.items()}

# Tabla resumen para copiar al README.
print("| Configuración | " + " | ".join(f"Recall@{k}" for k in KS) + " |")
print("|---" * (len(KS) + 1) + "|")
for nombre, resultados in todos.items():
    print(f"| {nombre} | " + " | ".join(f"{recall(resultados, k):.2f}".replace(".", ",") for k in KS) + " |")
