"""
Tests del enrutado del grafo con el LLM y la búsqueda simulados: no necesitan Ollama
ni los modelos de embeddings/reranker.

Uso:  python -m pytest
"""
import json

import pytest

import config
from grafo import Dependencias, construir_grafo, responder

RELEVANTE = ("git stash guarda los cambios sin confirmar", {"fuente": "14.md", "seccion": "Stash"}, 0.9)
IRRELEVANTE = ("los hooks se ejecutan antes de confirmar", {"fuente": "15.md", "seccion": "Hooks"}, 0.1)


class Llamadas:
    """Cuenta cuántas veces se llama a cada dependencia simulada."""
    def __init__(self):
        self.buscar = 0
        self.reescribir = 0
        self.juzgar = 0
        self.acceso = None      # el último nivel de acceso con el que se buscó


def dependencias(llamadas, decision="buscar", docs=(IRRELEVANTE,), docs_tras_reescribir=None,
                 **opciones):
    """Dependencias falsas. docs_tras_reescribir: lo que devuelve la búsqueda tras reescribir."""
    def buscar(pregunta, acceso):
        llamadas.buscar += 1
        llamadas.acceso = acceso
        if docs_tras_reescribir is not None and pregunta.startswith("REESCRITA"):
            return list(docs_tras_reescribir)
        return list(docs)

    def reescribir(pregunta):
        llamadas.reescribir += 1
        return f"REESCRITA: {pregunta}"

    def juzgar(pregunta, texto):
        llamadas.juzgar += 1
        return "stash" in texto

    def decidir(pregunta):
        if isinstance(decision, Exception):
            raise decision
        return decision

    return Dependencias(
        decidir=decidir,
        responder_directo=lambda p: "¡Hola! Pregúntame sobre Kubernetes, git o HTTP.",
        buscar=buscar,
        juzgar=juzgar,
        reescribir=reescribir,
        generar=lambda p, docs: "Usa git stash [1].",
        **opciones,
    )


def ejecutar(dep, pregunta="¿Cómo guardo cambios sin hacer commit?"):
    return responder(pregunta, app=construir_grafo(dep), trazar=False)


def test_pregunta_tecnica_va_a_buscar():
    r = ejecutar(dependencias(Llamadas(), decision="buscar", docs=[RELEVANTE]))
    assert r["ruta"] == ["decidir", "recuperar", "evaluar", "generar"]
    assert "responder_directo" not in r["ruta"]


def test_saludo_va_a_responder_directo_sin_buscar():
    llamadas = Llamadas()
    r = ejecutar(dependencias(llamadas, decision="directo"), pregunta="Hola")
    assert r["ruta"] == ["decidir", "responder_directo"]
    assert llamadas.buscar == 0


def test_si_falla_la_decision_del_llm_se_busca():
    r = ejecutar(dependencias(Llamadas(), decision=ValueError("JSON inválido"), docs=[RELEVANTE]))
    assert r["ruta"][1] == "recuperar"


def test_docs_irrelevantes_van_a_reescribir():
    r = ejecutar(dependencias(Llamadas(), docs=[IRRELEVANTE]))
    assert r["ruta"][:4] == ["decidir", "recuperar", "evaluar", "reescribir"]


def test_reescritura_que_encuentra_docs_relevantes_genera():
    r = ejecutar(dependencias(Llamadas(), docs=[IRRELEVANTE], docs_tras_reescribir=[RELEVANTE]))
    assert r["ruta"] == ["decidir", "recuperar", "evaluar", "reescribir",
                         "recuperar", "evaluar", "generar"]
    assert r["respuesta"] == "Usa git stash [1]."
    assert r["pregunta_original"] == "¿Cómo guardo cambios sin hacer commit?"


def test_reintentos_agotados_van_a_sin_respuesta():
    r = ejecutar(dependencias(Llamadas(), docs=[IRRELEVANTE]))
    assert r["ruta"][-1] == "sin_respuesta"
    assert r["respuesta"] == config.NO_LO_TENGO
    assert r["fuentes"] == []


def test_se_respeta_el_limite_de_reintentos():
    llamadas = Llamadas()
    r = ejecutar(dependencias(llamadas, docs=[IRRELEVANTE], max_reintentos=1))
    assert llamadas.reescribir == 1
    assert llamadas.buscar == 2          # la búsqueda inicial + una tras reescribir
    assert r["intentos"] == 1


def test_el_limite_de_reintentos_es_configurable():
    llamadas = Llamadas()
    ejecutar(dependencias(llamadas, docs=[IRRELEVANTE], max_reintentos=2))
    assert llamadas.reescribir == 2
    assert llamadas.buscar == 3


def test_el_limite_de_pasos_corta_y_responde_no_lo_tengo():
    # Con 100 reintentos permitidos, el límite de pasos del grafo es lo que lo para.
    llamadas = Llamadas()
    r = ejecutar(dependencias(llamadas, docs=[IRRELEVANTE], max_reintentos=100))
    assert r["error"] == "GraphRecursionError"
    assert r["respuesta"] == config.NO_LO_TENGO
    assert llamadas.buscar < 100


def test_evaluacion_por_reranker_filtra_por_umbral():
    r = ejecutar(dependencias(Llamadas(), docs=[RELEVANTE, IRRELEVANTE],
                              metodo_evaluacion="reranker", umbral_reranker=0.5))
    assert r["chunks"] == [{"antes": 2, "despues": 1}]
    assert [m["fuente"] for m in r["fuentes"]] == ["14.md"]


def test_evaluacion_por_llm_juez_usa_el_juez():
    llamadas = Llamadas()
    r = ejecutar(dependencias(llamadas, docs=[RELEVANTE, IRRELEVANTE], metodo_evaluacion="llm"))
    assert llamadas.juzgar == 2
    assert r["chunks"] == [{"antes": 2, "despues": 1}]


def test_la_traza_no_guarda_el_texto_de_la_pregunta(tmp_path, monkeypatch):
    fichero = tmp_path / "trazas.jsonl"
    monkeypatch.setattr(config, "FICHERO_TRAZAS", str(fichero))
    pregunta = "¿Cómo guardo cambios sin hacer commit?"
    responder(pregunta, app=construir_grafo(dependencias(Llamadas(), docs=[RELEVANTE],
                                                         metodo_evaluacion="llm")))
    linea = fichero.read_text(encoding="utf-8")
    traza = json.loads(linea)
    assert pregunta not in linea
    assert traza["metodo_evaluacion"] == "llm"      # el método real, no el de .env
    assert traza["ruta"] == ["decidir", "recuperar", "evaluar", "generar"]
    assert traza["reintentos"] == 0
    assert traza["chunks"] == [{"antes": 1, "despues": 1}]
    assert {l["nodo"] for l in traza["latencias"]} == set(traza["ruta"])


def test_por_defecto_el_grafo_busca_solo_en_lo_publico():
    llamadas = Llamadas()
    ejecutar(dependencias(llamadas))
    assert llamadas.acceso == ("publico",)


def test_el_nivel_de_acceso_llega_hasta_la_busqueda():
    llamadas = Llamadas()
    dep = dependencias(llamadas)
    responder("¿Cuánto ocupa un Secret?", app=construir_grafo(dep), trazar=False,
              acceso=("restringido", "publico"))
    assert llamadas.acceso == ("publico", "restringido")


def test_un_nivel_de_acceso_desconocido_es_un_error_y_no_busca():
    llamadas = Llamadas()
    with pytest.raises(ValueError):
        responder("hola", app=construir_grafo(dependencias(llamadas)), trazar=False,
                  acceso=("pubico",))
    assert llamadas.buscar == 0
