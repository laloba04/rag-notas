"""
Tests de la fábrica de LLM: elige bien el proveedor y falla con un mensaje claro.
Crear el objeto no hace ninguna llamada de red, así que no hace falta ni clave real ni servidor.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config


@pytest.fixture
def groq(monkeypatch):
    """Configura Groq solo durante el test, con una clave inventada."""
    monkeypatch.setattr(config, "LLM_PROVEEDOR", "groq")
    monkeypatch.setattr(config, "LLM_MODELO", "modelo-de-prueba")
    monkeypatch.setenv("GROQ_API_KEY", "clave-inventada")


def test_crea_ollama_con_la_configuracion(monkeypatch):
    monkeypatch.setattr(config, "LLM_PROVEEDOR", "ollama")
    monkeypatch.setattr(config, "LLM_MODELO", "modelo-de-prueba")
    llm = config.crear_llm()
    assert type(llm).__name__ == "ChatOllama"
    assert llm.model == "modelo-de-prueba"
    assert llm.base_url == config.OLLAMA_URL
    assert llm.temperature == config.TEMPERATURA


def test_crea_groq_con_la_configuracion(groq):
    llm = config.crear_llm()
    assert type(llm).__name__ == "ChatGroq"
    assert llm.model_name == "modelo-de-prueba"
    assert llm.temperature == config.TEMPERATURA
    assert llm.request_timeout == config.TIMEOUT_LLM_S


def test_groq_usa_la_clave_del_entorno_y_no_la_muestra(groq):
    llm = config.crear_llm()
    assert llm.groq_api_key.get_secret_value() == "clave-inventada"
    assert "clave-inventada" not in repr(llm)


def test_groq_sin_clave_falla_con_mensaje_claro(groq, monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY")
    with pytest.raises(RuntimeError, match="GROQ_API_KEY"):
        config.crear_llm()


def test_groq_con_clave_vacia_falla_con_mensaje_claro(groq, monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "")
    with pytest.raises(RuntimeError, match="GROQ_API_KEY"):
        config.crear_llm()


def test_proveedor_desconocido_falla(monkeypatch):
    monkeypatch.setattr(config, "LLM_PROVEEDOR", "otro")
    monkeypatch.setattr(config, "LLM_MODELO", "modelo-de-prueba")
    with pytest.raises(RuntimeError, match="no soportado"):
        config.crear_llm()


def test_sin_modelo_falla(monkeypatch):
    monkeypatch.setattr(config, "LLM_PROVEEDOR", "groq")
    monkeypatch.setattr(config, "LLM_MODELO", None)
    with pytest.raises(RuntimeError, match="LLM_MODELO"):
        config.crear_llm()


# --- LLM juez ---------------------------------------------------------------

@pytest.fixture
def principal_ollama(monkeypatch):
    monkeypatch.setattr(config, "LLM_PROVEEDOR", "ollama")
    monkeypatch.setattr(config, "LLM_MODELO", "modelo-principal")
    monkeypatch.setattr(config, "LLM_JUEZ_PROVEEDOR", None)
    monkeypatch.setattr(config, "LLM_JUEZ_MODELO", None)


def test_sin_configurar_el_juez_es_el_principal(principal_ollama):
    assert config.configuracion_juez() == ("ollama", "modelo-principal")
    juez = config.crear_llm_juez()
    assert type(juez).__name__ == "ChatOllama"
    assert juez.model == "modelo-principal"


def test_el_juez_puede_ser_otro_proveedor(principal_ollama, monkeypatch):
    monkeypatch.setattr(config, "LLM_JUEZ_PROVEEDOR", "groq")
    monkeypatch.setattr(config, "LLM_JUEZ_MODELO", "modelo-juez")
    monkeypatch.setenv("GROQ_API_KEY", "clave-inventada")
    juez = config.crear_llm_juez()
    assert type(juez).__name__ == "ChatGroq"
    assert juez.model_name == "modelo-juez"
    assert config.crear_llm().model == "modelo-principal"    # el principal no cambia


def test_el_juez_puede_ser_otro_modelo_del_mismo_proveedor(principal_ollama, monkeypatch):
    monkeypatch.setattr(config, "LLM_JUEZ_MODELO", "modelo-juez")
    assert config.configuracion_juez() == ("ollama", "modelo-juez")


def test_proveedor_del_juez_sin_modelo_falla(principal_ollama, monkeypatch):
    monkeypatch.setattr(config, "LLM_JUEZ_PROVEEDOR", "groq")
    with pytest.raises(RuntimeError, match="LLM_JUEZ_MODELO"):
        config.crear_llm_juez()


# --- Nada que no sea público hacia un proveedor externo ----------------------

PUBLICO = ("texto", {"fuente": "11-git.md", "confidencialidad": "publico"}, 0.9)
RESTRINGIDO = ("texto", {"fuente": "06-k8s-secrets.md", "confidencialidad": "restringido"}, 0.9)
SIN_ETIQUETA = ("texto", {"fuente": "x.md"}, 0.9)


def test_local_puede_recibir_lo_restringido():
    config.comprobar_envio([PUBLICO, RESTRINGIDO], "ollama")


def test_externo_puede_recibir_lo_publico():
    config.comprobar_envio([PUBLICO], "groq")


@pytest.mark.parametrize("trozo", [RESTRINGIDO, SIN_ETIQUETA])
def test_externo_no_recibe_nada_que_no_sea_publico(trozo):
    with pytest.raises(PermissionError, match=trozo[1]["fuente"]):
        config.comprobar_envio([PUBLICO, trozo], "groq")


def test_un_proveedor_desconocido_cuenta_como_externo():
    assert config.es_externo("otro")
    with pytest.raises(PermissionError):
        config.comprobar_envio([RESTRINGIDO], "otro")
