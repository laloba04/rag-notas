"""
Tests del servidor MCP, sin modelos ni LLM: la búsqueda y el grafo se sustituyen por funciones
falsas, salvo en la prueba de los documentos trampa, que indexa los reales de extra/ en una
colección de Chroma en memoria con un embedding de juguete y un reranker falso.
"""
import subprocess
import sys
from pathlib import Path

import anyio
import chromadb
import frontmatter
import pytest
from mcp.server.mcpserver.exceptions import ToolError

import config
import grafo
import mcp_server
import retrieve
from test_permisos import EmbeddingDeLetras

RAIZ = Path(__file__).resolve().parent.parent
PUBLICO = {"fuente": "14-git-stash.md", "seccion": "Stash", "url": "https://git-scm.com/x",
           "confidencialidad": "publico"}
RESTRINGIDO = {"fuente": "06-k8s-secrets.md", "seccion": "Secrets", "url": "https://k8s.io/x",
               "confidencialidad": "restringido"}
SECRETO = "Los Secrets individuales están limitados a 1MiB"


@pytest.fixture
def busqueda_falsa(monkeypatch):
    """retrieve.buscar falso que apunta con qué argumentos se le llama."""
    llamadas = []
    devuelve = [("git stash guarda los cambios", PUBLICO, 0.91)]

    def buscar(pregunta, k, rerank, acceso):
        llamadas.append({"pregunta": pregunta, "k": k, "rerank": rerank, "acceso": acceso})
        return devuelve[:k]
    monkeypatch.setattr(retrieve, "buscar", buscar)
    monkeypatch.setattr(mcp_server, "comprobar_coleccion", lambda: None)
    return llamadas, devuelve


@pytest.fixture
def grafo_falso(monkeypatch):
    llamadas = []
    estado = {"respuesta": "Usa git stash [1].", "fuentes": [PUBLICO],
              "docs": [("git stash guarda los cambios", PUBLICO, 0.91)],
              "ruta": ["decidir", "recuperar", "evaluar", "generar"], "total_s": 1.0}

    def responder(pregunta, acceso=None):
        llamadas.append(acceso)
        return estado
    monkeypatch.setattr(grafo, "responder", responder)
    monkeypatch.setattr(mcp_server, "comprobar_coleccion", lambda: None)
    monkeypatch.setattr(mcp_server, "comprobar_llm", lambda: None)
    return llamadas, estado


class Resultado:
    def __init__(self, is_error, texto):
        self.is_error, self.texto = is_error, texto


def llamar(herramienta, argumentos):
    """Llama a la herramienta por el servidor MCP y devuelve lo que vería el cliente: con un
    error, el servidor responde is_error y el texto de la excepción (genérico si no es un
    ToolError previsto)."""
    try:
        r = anyio.run(mcp_server.servidor.call_tool, herramienta, argumentos)
        return Resultado(r.is_error, r.content[0].text)
    except ToolError as e:
        return Resultado(True, str(e))


# --- buscar_documentacion ---

def test_buscar_devuelve_fragmentos_delimitados_con_url_y_puntuacion(busqueda_falsa):
    llamadas, _ = busqueda_falsa
    salida = mcp_server.buscar_documentacion("¿Cómo guardo cambios?", k=3)
    assert llamadas == [{"pregunta": "¿Cómo guardo cambios?", "k": 3, "rerank": True,
                         "acceso": ("publico",)}]
    assert salida.startswith(mcp_server.AVISO_FRAGMENTOS)
    assert ('<documento numero="1" fuente="14-git-stash.md" seccion="Stash" '
            'url="https://git-scm.com/x" puntuacion="0.910">') in salida
    assert salida.endswith("git stash guarda los cambios\n</documento>\n</documentos_no_confiables>")


@pytest.mark.parametrize("k, usado", [(0, 1), (-3, 1), (11, 10), (500, 10)])
def test_k_fuera_de_rango_se_limita_y_se_avisa(busqueda_falsa, k, usado):
    llamadas, _ = busqueda_falsa
    salida = mcp_server.buscar_documentacion("pregunta", k=k)
    assert llamadas[0]["k"] == usado
    assert salida.startswith(f"(k={k} está fuera del rango 1-10; se usa k={usado}.)")


def test_k_dentro_de_rango_no_lleva_nota(busqueda_falsa):
    assert mcp_server.buscar_documentacion("pregunta", k=10).startswith("AVISO")


def test_k_que_no_es_un_numero_da_error(busqueda_falsa):
    r = llamar("buscar_documentacion", {"pregunta": "x", "k": "muchos"})
    assert r.is_error and busqueda_falsa[0] == []


@pytest.mark.parametrize("pregunta", ["", "   "])
def test_pregunta_vacia_da_error_claro(busqueda_falsa, pregunta):
    with pytest.raises(ToolError, match="vacía"):
        mcp_server.buscar_documentacion(pregunta)


def test_sin_resultados_lo_dice(busqueda_falsa):
    busqueda_falsa[1].clear()
    assert mcp_server.buscar_documentacion("x") == "No se ha encontrado ningún fragmento."


# --- Acceso fijo a "publico" ---

def test_ninguna_herramienta_tiene_parametro_de_acceso():
    herramientas = anyio.run(mcp_server.servidor.list_tools)
    assert {t.name: set(t.input_schema["properties"]) for t in herramientas} == {
        "buscar_documentacion": {"pregunta", "k"}, "preguntar": {"pregunta"}}


def test_el_acceso_es_publico_aunque_el_modelo_lo_pida(busqueda_falsa, grafo_falso):
    for nivel in ("restringido", ["publico", "restringido"]):
        assert not llamar("buscar_documentacion", {"pregunta": "x", "acceso": nivel}).is_error
        assert not llamar("preguntar", {"pregunta": "x", "acceso": nivel}).is_error
    assert {str(ll["acceso"]) for ll in busqueda_falsa[0]} == {"('publico',)"}
    assert set(grafo_falso[0]) == {("publico",)}


def test_la_salida_es_solo_texto_sin_copia_en_json(busqueda_falsa, grafo_falso):
    # Sin structured_output=False, el cliente recibiría {"result": "..."} y lo enseñaría al
    # modelo en vez del texto con el aviso y el delimitador.
    herramientas = anyio.run(mcp_server.servidor.list_tools)
    assert all(t.output_schema is None for t in herramientas)
    for nombre in ("buscar_documentacion", "preguntar"):
        r = anyio.run(mcp_server.servidor.call_tool, nombre, {"pregunta": "x"})
        assert r.structured_content is None
        assert [c.type for c in r.content] == ["text"] and r.content[0].text.startswith("AVISO")


def test_el_cliente_mcp_se_trata_como_externo():
    assert config.es_externo(config.CLIENTE_MCP)


@pytest.mark.parametrize("meta", [RESTRINGIDO, {"fuente": "sin-etiqueta.md", "seccion": "s"}])
def test_un_fragmento_no_publico_bloquea_la_busqueda_entera(busqueda_falsa, monkeypatch, meta):
    # Simula un cambio por error: el acceso por defecto pasa a incluir lo restringido.
    monkeypatch.setattr(config, "ACCESO_POR_DEFECTO", ("publico", "restringido"))
    busqueda_falsa[1].append((SECRETO, meta, 0.99))
    r = llamar("buscar_documentacion", {"pregunta": "¿Tamaño de un Secret?"})
    texto = r.texto
    assert r.is_error and "Bloqueado" in texto
    # No se devuelve nada: ni el fragmento restringido, ni el público, ni el nombre del documento.
    assert SECRETO not in texto and "git stash" not in texto and meta["fuente"] not in texto


def test_un_fragmento_restringido_bloquea_preguntar(grafo_falso):
    _, estado = grafo_falso
    estado["fuentes"] = [PUBLICO, RESTRINGIDO]
    estado["respuesta"] = f"{SECRETO} [2]."
    r = llamar("preguntar", {"pregunta": "¿Tamaño de un Secret?"})
    assert r.is_error and "Bloqueado" in r.texto
    assert SECRETO not in r.texto


def test_preguntar_tambien_comprueba_los_fragmentos_del_generador(grafo_falso):
    grafo_falso[1]["docs"].append((SECRETO, RESTRINGIDO, 0.9))
    assert llamar("preguntar", {"pregunta": "x"}).is_error


# --- preguntar ---

def test_preguntar_devuelve_respuesta_fuentes_y_ruta(grafo_falso):
    salida = mcp_server.preguntar("¿Cómo guardo cambios sin hacer commit?")
    assert salida.startswith(mcp_server.AVISO_RESPUESTA)
    assert "Respuesta:\nUsa git stash [1]." in salida
    assert "[1] 14-git-stash.md > Stash (https://git-scm.com/x)" in salida
    assert salida.endswith("Ruta del grafo: decidir -> recuperar -> evaluar -> generar")


def test_preguntar_sin_respuesta(grafo_falso):
    grafo_falso[1].update(respuesta=config.NO_LO_TENGO, fuentes=[], docs=[],
                          ruta=["decidir", "recuperar", "evaluar", "reescribir", "recuperar",
                                "evaluar", "sin_respuesta"])
    salida = mcp_server.preguntar("¿Cuál es la última versión estable de Kubernetes?")
    assert f"Respuesta:\n{config.NO_LO_TENGO}" in salida and "Fuentes:\n(ninguna)" in salida


# --- Errores como mensajes claros ---

def test_ollama_apagado_da_mensaje_claro(grafo_falso, monkeypatch):
    def responder(pregunta, acceso=None):
        raise ConnectionError("Failed to connect to Ollama")
    monkeypatch.setattr(grafo, "responder", responder)
    r = llamar("preguntar", {"pregunta": "x"})
    assert r.is_error and "No se puede conectar con el LLM" in r.texto
    assert "Traceback" not in r.texto


def test_comprobar_llm_con_ollama_apagado(monkeypatch):
    import httpx
    monkeypatch.setattr(config, "LLM_PROVEEDOR", "ollama")
    monkeypatch.setattr(config, "LLM_MODELO", "modelo")
    monkeypatch.setattr(config, "OLLAMA_URL", "http://127.0.0.1:9")   # puerto cerrado

    def get(*args, **kwargs):
        raise httpx.ConnectError("conexión rechazada")
    monkeypatch.setattr(httpx, "get", get)
    with pytest.raises(ToolError, match="Ollama en http://127.0.0.1:9"):
        mcp_server.comprobar_llm()


def test_llm_sin_configurar_da_mensaje_claro(monkeypatch):
    monkeypatch.setattr(config, "LLM_MODELO", None)
    with pytest.raises(ToolError, match="LLM_MODELO"):
        mcp_server.comprobar_llm()


def test_coleccion_vacia_o_inexistente_da_mensaje_claro(monkeypatch):
    cliente = chromadb.EphemeralClient()
    if "test_mcp_vacia" in [c.name for c in cliente.list_collections()]:
        cliente.delete_collection("test_mcp_vacia")
    monkeypatch.setattr(retrieve, "_col", cliente.create_collection("test_mcp_vacia"))
    with pytest.raises(ToolError, match="vacía.*ingest.py"):
        mcp_server.comprobar_coleccion()

    def no_existe():
        raise chromadb.errors.NotFoundError("Collection [docs] does not exist")
    monkeypatch.setattr(retrieve, "coleccion", no_existe)
    with pytest.raises(ToolError, match="No existe.*ingest.py"):
        mcp_server.comprobar_coleccion()


def test_un_fallo_inesperado_no_ensena_la_traza(busqueda_falsa, monkeypatch):
    def buscar(*args, **kwargs):
        raise ValueError("detalle interno /home/usuario/ruta")
    monkeypatch.setattr(retrieve, "buscar", buscar)
    r = llamar("buscar_documentacion", {"pregunta": "x"})
    assert r.is_error and "detalle interno" not in r.texto


# --- Documentos trampa reales (extra/), en una colección en memoria ---

def indexar_trampa(monkeypatch, ruta):
    """Un documento trampa solo en una colección en memoria, como scripts/prueba_injection.py.
    Troceado por párrafos: ingest.trocear importa torch (~4 s) y aquí basta con el texto real."""
    cliente = chromadb.EphemeralClient()
    nombre = "test_mcp_trampa"
    if nombre in [c.name for c in cliente.list_collections()]:
        cliente.delete_collection(nombre)
    col = cliente.create_collection(nombre, embedding_function=EmbeddingDeLetras(),
                                    metadata={"hnsw:space": "cosine"})
    doc = frontmatter.load(RAIZ / ruta)
    parrafos = [t for t in doc.content.split("\n\n") if t.strip()]
    meta = {"fuente": Path(ruta).name, "seccion": "s", "url": doc["url"],
            "confidencialidad": doc["confidencialidad"]}
    col.add(ids=[f"{ruta}#{n}" for n in range(len(parrafos))], documents=parrafos,
            metadatas=[meta] * len(parrafos))
    monkeypatch.setattr(retrieve, "_col", col)
    monkeypatch.setattr(retrieve, "_bm25", {})

    class RerankerFalso:
        def predict(self, pares):
            return [len(set(retrieve.tokenizar(p)) & set(retrieve.tokenizar(t))) / 100
                    for p, t in pares]
    monkeypatch.setattr(retrieve, "_reranker", RerankerFalso())
    return col.count()


@pytest.mark.parametrize("ruta", ["extra/22-TRAMPA-dentro-decision.md",
                                  "extra/23-TRAMPA-falso-cierre.md"])
def test_los_documentos_trampa_salen_delimitados_y_con_aviso(monkeypatch, ruta):
    n = indexar_trampa(monkeypatch, ruta)
    assert n <= 10           # con k=10 sale el documento entero, incluida la orden
    salida = mcp_server.buscar_documentacion("¿Qué se decidió sobre la migración a Kubernetes?",
                                             k=10)
    assert salida.startswith(mcp_server.AVISO_FRAGMENTOS)
    antes, dentro = salida.split("<documentos_no_confiables>\n", 1)
    dentro, despues = dentro.rsplit("\n</documentos_no_confiables>", 1)
    assert despues == ""
    # La orden del ataque llega, pero solo dentro del delimitador.
    assert "hackeado" in dentro.lower() and "hackeado" not in antes.lower()
    # Un falso cierre no se sale: un único cierre real, y los <documento> cuadran.
    assert salida.lower().count("</documentos_no_confiables>") == 1
    assert salida.count("<documento ") == salida.count("</documento>") == n
    if "falso-cierre" in ruta:
        assert "&lt;/documentos_no_confiables>" in dentro and "&lt;/documento>" in dentro


# --- Arranque ---

def test_importar_el_servidor_no_carga_modelos_ni_coleccion():
    # En un proceso aparte: en este, otros tests ya han importado chromadb.
    codigo = ("import sys, mcp_server, retrieve; "
              "print(any(m in sys.modules for m in ('torch', 'sentence_transformers', 'chromadb')),"
              " retrieve._col is None)")
    salida = subprocess.run([sys.executable, "-c", codigo], cwd=RAIZ, capture_output=True,
                            text=True, check=True)
    assert salida.stdout.split() == ["False", "True"]
