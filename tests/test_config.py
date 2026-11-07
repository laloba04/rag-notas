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
