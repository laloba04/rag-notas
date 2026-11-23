"""Tests de scripts/grabar_demo.py sin API ni LLM: lectura del SSE y control de lo que se publica."""
import importlib.util
from pathlib import Path

import pytest

RUTA = Path(__file__).resolve().parent.parent / "scripts" / "grabar_demo.py"
spec = importlib.util.spec_from_file_location("grabar_demo", RUTA)
grabar_demo = importlib.util.module_from_spec(spec)
spec.loader.exec_module(grabar_demo)


def test_lee_eventos_sse():
    lineas = ['event: nodo', 'data: {"nodo": "decidir"}', '',
              'event: respuesta', 'data: {"respuesta": "Hola"}', '']
    assert list(grabar_demo.leer_eventos(lineas)) == [("nodo", {"nodo": "decidir"}),
                                                     ("respuesta", {"respuesta": "Hola"})]


def test_niveles_salen_del_frontmatter():
    niveles = grabar_demo.niveles_por_documento()
    assert len(niveles) == 20
    assert niveles["06-k8s-secrets.md"] == "restringido"


@pytest.mark.parametrize("documento", ["06-k8s-secrets.md", "99-no-existe.md"])
def test_falla_si_cita_algo_no_publico_o_desconocido(documento):
    niveles = grabar_demo.niveles_por_documento()
    with pytest.raises(RuntimeError, match="no es público"):
        grabar_demo.comprobar_publico({"citas": [{"documento": documento}]}, niveles)


def test_deja_pasar_documentos_publicos():
    niveles = grabar_demo.niveles_por_documento()
    grabar_demo.comprobar_publico({"citas": [{"documento": "14-git-stash-y-clean.md"}]}, niveles)
