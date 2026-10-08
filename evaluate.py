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

golden = json.load(open("eval/golden_set.json", encoding="utf-8"))
# Las preguntas sin respuesta (texto_esperado = None) se miden más adelante, con el LLM.
con_respuesta = [q for q in golden if q["texto_esperado"]]


def puesto_del_acierto(q):
    """Devuelve el puesto (1, 2, ...) del primer trozo correcto, o None si no está en los K_MAX."""
    for puesto, (texto, meta, _) in enumerate(buscar(q["pregunta"], k=K_MAX), start=1):
        if meta["fuente"] in q["fuentes"] and q["texto_esperado"].lower() in texto.lower():
            return puesto
    return None


resultados = []
for q in con_respuesta:
    puesto = puesto_del_acierto(q)
    resultados.append({"id": q["id"], "tipo": q["tipo"], "pregunta": q["pregunta"], "puesto": puesto})


def recall(filas, k):
    return sum(1 for r in filas if r["puesto"] and r["puesto"] <= k) / len(filas)


print(f"{len(con_respuesta)} preguntas con respuesta\n")
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
salida = carpeta / f"{datetime.now():%Y-%m-%d_%H%M}_vectorial.json"
salida.write_text(json.dumps({
    "configuracion": "solo vectorial",
    **{f"recall@{k}": round(recall(resultados, k), 3) for k in KS},
    "preguntas": resultados,
}, ensure_ascii=False, indent=2), encoding="utf-8")
print(f"\nGuardado en {salida}")
