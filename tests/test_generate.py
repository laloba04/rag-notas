"""
Tests de la llamada al LLM en generate.py con un LLM falso: no hace falta red ni modelos.
"""
import sys
from pathlib import Path

import pytest
from langchain_core.messages import AIMessage

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import generate


class LLMFalso:
    """Imita el invoke() de un modelo de chat de LangChain y apunta lo que recibe."""

    def __init__(self, respuesta):
        self.respuesta = respuesta
        self.recibido = None

    def invoke(self, mensajes):
        self.recibido = mensajes
        return self.respuesta


@pytest.fixture
def con_llm(monkeypatch):
    def poner(respuesta):
        falso = LLMFalso(respuesta)
        monkeypatch.setattr(generate, "_llm", falso)
        return falso
    return poner


def test_envia_system_prompt_y_pregunta(con_llm):
    falso = con_llm(AIMessage(content="Respuesta [1]"))
    generate.llamar_llm("PROMPT")
    assert falso.recibido == [("system", generate.system_prompt()), ("user", "PROMPT")]


def test_tokens_salen_de_usage_metadata(con_llm):
    con_llm(AIMessage(content="  Respuesta [1]\n",
                      usage_metadata={"input_tokens": 120, "output_tokens": 8, "total_tokens": 128}))
    texto, tokens = generate.llamar_llm("PROMPT")
    assert texto == "Respuesta [1]"
    assert tokens == {"entrada": 120, "salida": 8}


def test_sin_usage_metadata_los_tokens_quedan_vacios(con_llm):
    con_llm(AIMessage(content="Respuesta [1]"))
    _, tokens = generate.llamar_llm("PROMPT")
    assert tokens == {"entrada": None, "salida": None}


def test_el_llm_se_crea_una_vez_y_se_reutiliza(monkeypatch):
    creados = []
    monkeypatch.setattr(generate, "_llm", None)
    monkeypatch.setattr(generate.config, "crear_llm",
                        lambda: creados.append(1) or LLMFalso(AIMessage(content="x")))
    generate.llamar_llm("uno")
    generate.llamar_llm("dos")
    assert len(creados) == 1


def test_no_envia_lo_restringido_a_un_proveedor_externo(con_llm, monkeypatch):
    restringido = ("1 MiB", {"fuente": "06-k8s-secrets.md", "seccion": "Límites",
                             "confidencialidad": "restringido"}, 0.9)
    monkeypatch.setattr(generate, "buscar", lambda *a, **k: [restringido])
    monkeypatch.setattr(generate.config, "LLM_PROVEEDOR", "groq")
    falso = con_llm(AIMessage(content="1 MiB [1]"))
    with pytest.raises(PermissionError, match="06-k8s-secrets.md"):
        generate.responder("¿Tamaño máximo de un Secret?", acceso=("publico", "restringido"))
    assert falso.recibido is None       # el LLM no llegó a recibir nada
