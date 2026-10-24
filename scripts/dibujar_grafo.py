"""
Genera docs/grafo.md con el diagrama Mermaid del grafo de grafo.py.

Uso (desde la raíz del repo):  python scripts/dibujar_grafo.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from grafo import Dependencias, construir_grafo  # noqa: E402

# Para dibujar solo hace falta la estructura: las dependencias no se llegan a llamar.
nada = lambda *args: None  # noqa: E731
app = construir_grafo(Dependencias(decidir=nada, responder_directo=nada, buscar=nada,
                                   juzgar=nada, reescribir=nada, generar=nada))

salida = Path("docs/grafo.md")
salida.write_text(
    "# Grafo de la capa agéntica y correctiva\n\n"
    "Generado con `python scripts/dibujar_grafo.py` a partir de `grafo.py`. "
    "Las flechas discontinuas son aristas condicionales.\n\n"
    f"```mermaid\n{app.get_graph().draw_mermaid().strip()}\n```\n",
    encoding="utf-8",
)
print(f"Guardado en {salida}")
