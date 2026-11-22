"""
Tests de la API (api.py) con TestClient y dependencias falsas: la búsqueda, el grafo y el
pipeline directo se sustituyen, así que no cargan modelos ni llaman a ningún LLM.
"""
import json

import pytest
from fastapi.testclient import TestClient

import api
import config
import generate
import grafo
import retrieve
import servicio

PUBLICO = {"fuente": "14-git-stash.md", "seccion": "Stash", "url": "https://git-scm.com/x",
           "confidencialidad": "publico"}
RESTRINGIDO = {"fuente": "06-k8s-secrets.md", "seccion": "Secrets", "url": "https://k8s.io/x",
               "confidencialidad": "restringido"}
SECRETO = "Los Secrets individuales están limitados a 1MiB"
DOC = ("git stash guarda los cambios", PUBLICO, 0.91)
RUTA = ["decidir", "recuperar", "evaluar", "generar"]


@pytest.fixture
def cliente(monkeypatch):
    monkeypatch.setattr(api, "LIMITADOR", api.Limitador(1000, 1000))
    monkeypatch.setattr(servicio, "comprobar_coleccion", lambda: None)
    monkeypatch.setattr(servicio, "comprobar_llm", lambda: None)
    return TestClient(api.app, raise_server_exceptions=False)


@pytest.fixture
def falsos(monkeypatch):
    """Búsqueda, grafo y pipeline directo falsos que apuntan con qué se les llama."""
    llamadas = {"buscar": [], "grafo": [], "directo": []}
    estado = {"respuesta": "Usa git stash [1].", "fuentes": [PUBLICO], "docs": [DOC],
              "ruta": RUTA, "intentos": 0, "latencias": [{"nodo": "decidir", "s": 0.5}],
              "pregunta": "¿Cómo guardo cambios?"}

    def buscar(pregunta, k, rerank, acceso):
        llamadas["buscar"].append({"k": k, "rerank": rerank, "acceso": acceso})
        return [DOC][:k]

    def responder_grafo(pregunta, acceso=None, al_pasar=None):
        llamadas["grafo"].append(acceso)
        for nodo in estado["ruta"]:
            if al_pasar:
                al_pasar(nodo)
        return dict(estado)

    def responder_directo(pregunta, acceso=None):
        llamadas["directo"].append(acceso)
        return {"respuesta": estado["respuesta"], "fuentes": estado["fuentes"],
                "docs": estado["docs"]}

    monkeypatch.setattr(retrieve, "buscar", buscar)
    monkeypatch.setattr(grafo, "responder", responder_grafo)
    monkeypatch.setattr(generate, "responder", responder_directo)
    return llamadas, estado


def eventos_sse(texto):
    """[(tipo, datos)] de un cuerpo SSE."""
    salida = []
    for bloque in texto.strip().split("\n\n"):
        lineas = dict(linea.split(": ", 1) for linea in bloque.split("\n"))
        salida.append((lineas["event"], json.loads(lineas["data"])))
    return salida


# --- /buscar ---

def test_buscar_devuelve_fragmentos_con_documento_y_puntuacion(cliente, falsos):
    r = cliente.post("/buscar", json={"pregunta": "¿Cómo guardo cambios?", "k": 3})
    assert r.status_code == 200
    assert r.json() == {"fragmentos": [{"numero": 1, "documento": "14-git-stash.md",
                                        "seccion": "Stash", "url": "https://git-scm.com/x",
                                        "puntuacion": 0.91, "texto": "git stash guarda los cambios"}]}
    assert falsos[0]["buscar"] == [{"k": 3, "rerank": True, "acceso": ("publico",)}]


@pytest.mark.parametrize("k", [0, 11, "muchos"])
def test_k_fuera_de_rango_da_422_sin_buscar(cliente, falsos, k):
    r = cliente.post("/buscar", json={"pregunta": "x", "k": k})
    assert r.status_code == 422 and r.json()["campos"][0]["campo"] == "k"
    assert falsos[0]["buscar"] == []


@pytest.mark.parametrize("pregunta, estado", [("", 422), ("   ", 400),
                                              ("a" * (config.LONGITUD_MAX_PREGUNTA + 1), 422)])
def test_pregunta_vacia_o_larga_da_error_claro(cliente, falsos, pregunta, estado):
    r = cliente.post("/buscar", json={"pregunta": pregunta})
    assert r.status_code == estado and r.json()["tipo"] == "peticion"
    assert "input" not in r.json() and "aaaa" not in r.text   # no devuelve lo enviado


# --- Acceso fijo a "publico" ---

@pytest.mark.parametrize("ruta, cuerpo", [("/buscar", {"pregunta": "x"}),
                                          ("/preguntar", {"pregunta": "x"})])
def test_un_campo_acceso_se_rechaza(cliente, falsos, ruta, cuerpo):
    r = cliente.post(ruta, json={**cuerpo, "acceso": ["publico", "restringido"]})
    assert r.status_code == 422 and r.json()["campos"] == [{"campo": "acceso",
                                                            "motivo": "extra_forbidden"}]
    assert falsos[0] == {"buscar": [], "grafo": [], "directo": []}


def test_el_acceso_es_siempre_publico(cliente, falsos):
    for modo in ("grafo", "directo"):
        assert cliente.post("/preguntar", json={"pregunta": "x", "modo": modo}).status_code == 200
    cliente.post("/buscar", json={"pregunta": "x"})
    llamadas = falsos[0]
    assert llamadas["grafo"] == llamadas["directo"] == [("publico",)]
    assert llamadas["buscar"][0]["acceso"] == ("publico",)


def test_la_api_se_trata_como_destino_externo():
    assert config.es_externo(config.CLIENTE_API)


def test_un_fragmento_restringido_en_la_busqueda_bloquea_todo(cliente, falsos, monkeypatch):
    # Simula un cambio por error del acceso por defecto.
    monkeypatch.setattr(config, "ACCESO_POR_DEFECTO", ("publico", "restringido"))
    monkeypatch.setattr(retrieve, "buscar", lambda *a, **k: [DOC, (SECRETO, RESTRINGIDO, 0.99)])
    r = cliente.post("/buscar", json={"pregunta": "¿Tamaño de un Secret?"})
    assert r.status_code == 500 and r.json()["tipo"] == "bloqueado"
    assert SECRETO not in r.text and "git stash" not in r.text and "06-k8s" not in r.text


@pytest.mark.parametrize("modo", ["grafo", "directo"])
def test_un_fragmento_restringido_en_la_respuesta_bloquea_todo(cliente, falsos, modo):
    falsos[1].update(fuentes=[PUBLICO, RESTRINGIDO], docs=[DOC, (SECRETO, RESTRINGIDO, 0.9)],
                     respuesta=f"{SECRETO} [2].")
    r = cliente.post("/preguntar", json={"pregunta": "x", "modo": modo})
    assert r.status_code == 500 and r.json()["tipo"] == "bloqueado" and SECRETO not in r.text


def test_un_permissionerror_hacia_el_llm_se_ve_como_bloqueado(cliente, falsos, monkeypatch):
    def responder(*a, **k):
        raise PermissionError("No se envían fragmentos no públicos al proveedor externo 'groq': 06.md")
    monkeypatch.setattr(generate, "responder", responder)
    r = cliente.post("/preguntar", json={"pregunta": "x", "modo": "directo"})
    assert r.status_code == 500 and r.json()["tipo"] == "bloqueado" and "06.md" not in r.text


# --- /preguntar ---

def test_preguntar_en_modo_grafo_devuelve_respuesta_citas_y_traza(cliente, falsos):
    r = cliente.post("/preguntar", json={"pregunta": "¿Cómo guardo cambios?"})
    cuerpo = r.json()
    assert r.status_code == 200 and cuerpo["modo"] == "grafo"
    assert cuerpo["respuesta"] == "Usa git stash [1]." and cuerpo["no_lo_tengo"] is False
    assert cuerpo["citas"][0]["documento"] == "14-git-stash.md"
    assert cuerpo["citas"][0]["texto"] == "git stash guarda los cambios"
    assert cuerpo["traza"]["ruta"] == RUTA and cuerpo["traza"]["pregunta_reescrita"] is None


def test_preguntar_en_modo_directo_no_lleva_traza(cliente, falsos):
    cuerpo = cliente.post("/preguntar", json={"pregunta": "x", "modo": "directo"}).json()
    assert cuerpo["modo"] == "directo" and cuerpo["traza"] is None and len(cuerpo["citas"]) == 1


def test_modo_desconocido_da_422(cliente, falsos):
    assert cliente.post("/preguntar", json={"pregunta": "x", "modo": "otro"}).status_code == 422


def test_no_lo_tengo_es_un_resultado_valido(cliente, falsos):
    falsos[1].update(respuesta=config.NO_LO_TENGO, fuentes=[], docs=[],
                     ruta=["decidir", "recuperar", "evaluar", "reescribir", "recuperar",
                           "evaluar", "sin_respuesta"], pregunta="REESCRITA", intentos=1)
    r = cliente.post("/preguntar", json={"pregunta": "¿Última versión de Kubernetes?"})
    cuerpo = r.json()
    assert r.status_code == 200 and cuerpo["no_lo_tengo"] is True and cuerpo["citas"] == []
    assert cuerpo["traza"]["reintentos"] == 1 and cuerpo["traza"]["pregunta_reescrita"] == "REESCRITA"


def test_las_citas_son_solo_los_fragmentos_citados(cliente, falsos):
    otro = ("git add prepara cambios", {**PUBLICO, "fuente": "10-git.md"}, 0.7)
    falsos[1].update(docs=[DOC, otro], fuentes=[PUBLICO, otro[1]], respuesta="Usa git add [2].")
    citas = cliente.post("/preguntar", json={"pregunta": "x"}).json()["citas"]
    assert [(c["numero"], c["documento"]) for c in citas] == [(2, "10-git.md")]


def test_no_lo_tengo_con_cita_no_lleva_citas(cliente, falsos):
    # Pasa de verdad: el grafo responde "No lo tengo en la documentación. [1]" a la pregunta
    # trampa, porque el LLM cita igualmente el fragmento que tenía.
    falsos[1].update(respuesta=f"{config.NO_LO_TENGO} [1]")
    cuerpo = cliente.post("/preguntar", json={"pregunta": "x"}).json()
    assert cuerpo["no_lo_tengo"] is True and cuerpo["citas"] == []


def test_si_la_salida_se_bloqueo_no_hay_citas(cliente, falsos):
    # El grafo cambió la respuesta por "No lo tengo" (fuentes vacías): no se enseña el fragmento.
    falsos[1].update(respuesta=config.NO_LO_TENGO, fuentes=[])
    cuerpo = cliente.post("/preguntar", json={"pregunta": "x"}).json()
    assert cuerpo["citas"] == [] and "git stash" not in json.dumps(cuerpo)


# --- Streaming ---

def test_streaming_manda_los_nodos_y_al_final_la_respuesta(cliente, falsos):
    r = cliente.post("/preguntar", json={"pregunta": "x"}, headers={"Accept": "text/event-stream"})
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/event-stream")
    eventos = eventos_sse(r.text)
    assert eventos[:-1] == [("nodo", {"nodo": n}) for n in RUTA]
    tipo, datos = eventos[-1]
    assert tipo == "respuesta" and datos["respuesta"] == "Usa git stash [1]."


def test_streaming_los_nodos_no_llevan_contenido(cliente, falsos):
    r = cliente.post("/preguntar", json={"pregunta": "x"}, headers={"Accept": "text/event-stream"})
    for tipo, datos in eventos_sse(r.text)[:-1]:
        assert set(datos) == {"nodo"}


def test_streaming_en_modo_directo_solo_manda_la_respuesta(cliente, falsos):
    r = cliente.post("/preguntar", json={"pregunta": "x", "modo": "directo"},
                     headers={"Accept": "text/event-stream"})
    assert [t for t, _ in eventos_sse(r.text)] == ["respuesta"]


def test_streaming_un_bloqueo_llega_como_evento_de_error_sin_respuesta(cliente, falsos):
    falsos[1].update(fuentes=[RESTRINGIDO], docs=[(SECRETO, RESTRINGIDO, 0.9)])
    r = cliente.post("/preguntar", json={"pregunta": "x"}, headers={"Accept": "text/event-stream"})
    tipos = [t for t, _ in eventos_sse(r.text)]
    assert tipos[-1] == "error" and "respuesta" not in tipos and SECRETO not in r.text


# --- Errores claros ---

def test_ollama_apagado_da_503_sin_la_url_interna(cliente, falsos, monkeypatch):
    monkeypatch.setattr(config, "OLLAMA_URL", "http://192.168.0.12:11434")

    def responder(*a, **k):
        raise ConnectionError("Failed to connect to Ollama")
    monkeypatch.setattr(grafo, "responder", responder)
    r = cliente.post("/preguntar", json={"pregunta": "x"})
    assert r.status_code == 503 and "No se puede conectar con el LLM" in r.json()["error"]
    assert "192.168" not in r.text


def test_coleccion_inexistente_da_503(cliente, falsos, monkeypatch):
    def no_existe():
        raise servicio.ErrorServicio("No existe la colección de documentos", "no_disponible")
    monkeypatch.setattr(servicio, "comprobar_coleccion", no_existe)
    r = cliente.post("/buscar", json={"pregunta": "x"})
    assert r.status_code == 503 and "No existe la colección" in r.json()["error"]


def test_un_fallo_inesperado_no_ensena_la_traza(cliente, falsos, monkeypatch):
    def buscar(*a, **k):
        raise ValueError("detalle interno /home/usuario/ruta")
    monkeypatch.setattr(retrieve, "buscar", buscar)
    r = cliente.post("/buscar", json={"pregunta": "x"})
    assert r.status_code == 500 and r.json() == {"error": "Error interno del servidor.",
                                                 "tipo": "interno"}
    assert "detalle interno" not in r.text and "Traceback" not in r.text


def test_timeout_da_504(cliente, falsos, monkeypatch):
    import time
    monkeypatch.setattr(config, "TIMEOUT_PETICION_S", 0.05)
    monkeypatch.setattr(retrieve, "buscar", lambda *a, **k: time.sleep(0.3) or [DOC])
    r = cliente.post("/buscar", json={"pregunta": "x"})
    assert r.status_code == 504 and r.json()["tipo"] == "timeout"


# --- Límite de peticiones ---

def test_limite_por_minuto_y_por_ip(cliente, falsos, monkeypatch):
    monkeypatch.setattr(api, "LIMITADOR", api.Limitador(2, 1000))
    estados = [cliente.post("/buscar", json={"pregunta": "x"}).status_code for _ in range(3)]
    assert estados == [200, 200, 429]
    r = cliente.post("/buscar", json={"pregunta": "x"})
    assert int(r.headers["Retry-After"]) > 0 and r.json()["tipo"] == "limite"


def test_la_ventana_del_minuto_se_libera():
    reloj = [0.0]
    lim = api.Limitador(1, 1000, reloj=lambda: reloj[0])
    assert lim.comprobar("1.1.1.1", False) is None
    assert lim.comprobar("1.1.1.1", False) is not None
    assert lim.comprobar("2.2.2.2", False) is None          # otra IP, su propio límite
    reloj[0] = 60.0
    assert lim.comprobar("1.1.1.1", False) is None


def test_tope_diario_de_preguntas_global():
    dia = ["2026-11-22"]
    lim = api.Limitador(1000, 2, hoy=lambda: dia[0])
    assert [lim.comprobar(ip, True) is None for ip in ("a", "b", "c")] == [True, True, False]
    assert lim.comprobar("d", False) is None                # /buscar no cuenta en el tope
    dia[0] = "2026-11-23"
    assert lim.comprobar("e", True) is None


def test_x_forwarded_for_se_ignora_si_no_viene_de_un_proxy_de_confianza(monkeypatch):
    class Peticion:
        def __init__(self, ip, xff):
            self.client = type("C", (), {"host": ip})()
            self.headers = {"x-forwarded-for": xff}
    monkeypatch.setattr(config, "PROXIES_DE_CONFIANZA", set())
    assert api.ip_cliente(Peticion("9.9.9.9", "1.2.3.4")) == "9.9.9.9"
    monkeypatch.setattr(config, "PROXIES_DE_CONFIANZA", {"10.0.0.1"})
    # El cliente pone una IP falsa a la izquierda; el proxy añade la real a la derecha.
    assert api.ip_cliente(Peticion("10.0.0.1", "6.6.6.6, 1.2.3.4")) == "1.2.3.4"
    assert api.ip_cliente(Peticion("9.9.9.9", "1.2.3.4")) == "9.9.9.9"


# --- CORS ---

def test_cors_solo_para_el_origen_configurado(cliente):
    ok = cliente.options("/buscar", headers={"Origin": "http://localhost:4200",
                                             "Access-Control-Request-Method": "POST"})
    otro = cliente.options("/buscar", headers={"Origin": "https://malo.example",
                                               "Access-Control-Request-Method": "POST"})
    assert ok.headers.get("access-control-allow-origin") == "http://localhost:4200"
    assert "access-control-allow-origin" not in otro.headers


def test_cors_nunca_acepta_el_comodin(monkeypatch):
    import importlib
    monkeypatch.setenv("CORS_ORIGENES", "*, https://web.example")
    try:
        assert importlib.reload(config).CORS_ORIGENES == ["https://web.example"]
    finally:
        monkeypatch.delenv("CORS_ORIGENES")
        importlib.reload(config)


# --- /salud ---

def test_salud_sin_datos_internos(cliente, monkeypatch):
    monkeypatch.setattr(config, "OLLAMA_URL", "http://192.168.0.12:11434")
    monkeypatch.setattr(servicio, "contar_fragmentos", lambda: 610)
    monkeypatch.setattr(servicio, "llm_responde", lambda: (True, True))
    r = cliente.get("/salud")
    cuerpo = r.json()
    assert r.status_code == 200 and cuerpo["estado"] == "ok"
    assert cuerpo["coleccion"] == {"ok": True, "fragmentos": 610}
    assert cuerpo["version"] == config.VERSION and "192.168" not in r.text


def test_salud_degradada_sin_llm_y_503_sin_coleccion(cliente, monkeypatch):
    monkeypatch.setattr(servicio, "llm_responde", lambda: (False, True))
    monkeypatch.setattr(servicio, "contar_fragmentos", lambda: 610)
    assert cliente.get("/salud").json()["estado"] == "degradado"
    monkeypatch.setattr(servicio, "contar_fragmentos", lambda: None)
    assert cliente.get("/salud").status_code == 503


# --- Cliente que se va a mitad de la consulta ---

@pytest.fixture
def grafo_lento(monkeypatch):
    """Grafo falso de 7 nodos, 0,05 s cada uno, que apunta los nodos que llega a ejecutar."""
    import threading
    hechos, terminado = [], threading.Event()
    nodos = ["decidir", "recuperar", "evaluar", "reescribir", "recuperar", "evaluar", "generar"]

    def responder(pregunta, acceso=None, al_pasar=None):
        try:
            for nodo in nodos:
                import time
                time.sleep(0.05)
                hechos.append(nodo)
                al_pasar(nodo)
            return {"respuesta": "Usa git stash [1].", "fuentes": [PUBLICO], "docs": [DOC],
                    "ruta": nodos, "intentos": 1, "latencias": [], "pregunta": pregunta}
        finally:
            terminado.set()
    monkeypatch.setattr(grafo, "responder", responder)
    monkeypatch.setattr(servicio, "comprobar_coleccion", lambda: None)
    monkeypatch.setattr(servicio, "comprobar_llm", lambda: None)
    return hechos, terminado, nodos


def test_si_el_cliente_cierra_el_sse_el_grafo_para_en_el_siguiente_nodo(grafo_lento):
    import asyncio
    hechos, terminado, nodos = grafo_lento

    async def cliente_que_se_va():
        flujo = api.eventos("x", "grafo")
        primero = await flujo.__anext__()             # lee el primer nodo...
        await flujo.aclose()                          # ...y cierra la conexión
        return primero
    primero = asyncio.run(cliente_que_se_va())
    assert '"nodo": "decidir"' in primero
    assert terminado.wait(2)
    assert len(hechos) < len(nodos)                   # no llegó al final
    assert servicio.CERROJO.acquire(blocking=False)   # y soltó el turno
    servicio.CERROJO.release()


def test_una_consulta_cancelada_mientras_espera_turno_no_se_ejecuta():
    import asyncio
    import threading
    ejecutada, cancelado = [], threading.Event()
    cancelado.set()
    with pytest.raises(api.Cancelado):
        asyncio.run(api.en_hilo(lambda: ejecutada.append(1), cancelado=cancelado))
    assert ejecutada == []


def test_sin_streaming_tambien_se_cancela_si_el_cliente_se_va(grafo_lento, monkeypatch):
    import asyncio
    monkeypatch.setattr(api, "VIGILAR_CADA_S", 0.01)
    hechos, terminado, nodos = grafo_lento

    class PeticionQueSeVa:
        async def is_disconnected(self):
            return True
    with pytest.raises(api.Cancelado):
        asyncio.run(api.vigilando(PeticionQueSeVa(), api.preguntar_sync, "x", "grafo"))
    assert terminado.wait(2) and len(hechos) < len(nodos)


def test_un_timeout_tambien_para_el_grafo(grafo_lento, monkeypatch):
    hechos, terminado, nodos = grafo_lento
    monkeypatch.setattr(api, "LIMITADOR", api.Limitador(1000, 1000))
    monkeypatch.setattr(config, "TIMEOUT_PETICION_S", 0.12)
    r = TestClient(api.app).post("/preguntar", json={"pregunta": "x"})
    assert r.status_code == 504
    assert terminado.wait(2) and len(hechos) < len(nodos)


# --- /salud no hace cola ---

def test_salud_responde_aunque_haya_una_consulta_en_curso(cliente, monkeypatch):
    import time
    monkeypatch.setattr(servicio, "contar_fragmentos", lambda: 610)
    monkeypatch.setattr(servicio, "llm_responde", lambda: (True, True))
    assert servicio.CERROJO.acquire(timeout=1)        # una consulta tiene el turno
    try:
        t0 = time.perf_counter()
        r = cliente.get("/salud")
        assert r.status_code == 200 and time.perf_counter() - t0 < 1
    finally:
        servicio.CERROJO.release()


# --- Carga de modelos al arrancar ---

@pytest.mark.parametrize("activada", [True, False])
def test_carga_de_modelos_al_arrancar_segun_config(monkeypatch, activada):
    import threading
    cargado = threading.Event()
    monkeypatch.setattr(config, "CARGAR_MODELOS_AL_ARRANCAR", activada)
    monkeypatch.setattr(servicio, "cargar_modelos", cargado.set)
    with TestClient(api.app):                         # el with ejecuta el arranque (lifespan)
        assert cargado.wait(1 if activada else 0.2) is activada


def test_un_fallo_al_precargar_no_tumba_el_servidor(monkeypatch):
    def falla():
        raise RuntimeError("sin colección")
    monkeypatch.setattr(config, "CARGAR_MODELOS_AL_ARRANCAR", True)
    monkeypatch.setattr(servicio, "cargar_modelos", falla)
    monkeypatch.setattr(servicio, "contar_fragmentos", lambda: 610)
    monkeypatch.setattr(servicio, "llm_responde", lambda: (True, True))
    with TestClient(api.app) as c:
        assert c.get("/salud").status_code == 200


def test_modelos_cargados_solo_tras_una_busqueda_completa(cliente, falsos, monkeypatch):
    monkeypatch.setattr(servicio, "modelos_cargados", False)
    monkeypatch.setattr(servicio, "contar_fragmentos", lambda: 610)
    monkeypatch.setattr(servicio, "llm_responde", lambda: (True, True))
    assert cliente.get("/salud").json()["modelos_cargados"] is False
    cliente.post("/buscar", json={"pregunta": "x"})
    assert cliente.get("/salud").json()["modelos_cargados"] is True


@pytest.mark.parametrize("respuesta, esperado", [
    (config.NO_LO_TENGO, True),
    (f"{config.NO_LO_TENGO} [1]", True),
    ("no lo tengo en la documentación", True),
    (f"Cache-Control controla la caché [1]. Sobre HTTP/3: {config.NO_LO_TENGO}", False),
    ("Usa git stash [1].", False),
])
def test_no_lo_tengo_solo_si_es_la_respuesta_entera(respuesta, esperado):
    assert api.es_no_lo_tengo(respuesta) is esperado


def test_la_respuesta_dice_si_la_salida_se_bloqueo(cliente, falsos):
    falsos[1].update(respuesta=config.NO_LO_TENGO, fuentes=[], salida_bloqueada=True)
    cuerpo = cliente.post("/preguntar", json={"pregunta": "x"}).json()
    assert cuerpo["salida_bloqueada"] is True and cuerpo["no_lo_tengo"] is True
    falsos[1].update(respuesta="Usa git stash [1].", fuentes=[PUBLICO], salida_bloqueada=False)
    assert cliente.post("/preguntar", json={"pregunta": "x"}).json()["salida_bloqueada"] is False


def test_proxies_de_confianza_admite_redes(monkeypatch):
    class Peticion:
        def __init__(self, ip, xff):
            self.client = type("C", (), {"host": ip})()
            self.headers = {"x-forwarded-for": xff}
    monkeypatch.setattr(config, "PROXIES_DE_CONFIANZA", {"172.30.0.0/24", "no-es-una-ip"})
    assert api.ip_cliente(Peticion("172.30.0.7", "6.6.6.6, 1.2.3.4")) == "1.2.3.4"
    assert api.ip_cliente(Peticion("172.31.0.7", "1.2.3.4")) == "172.31.0.7"      # fuera de la red
    assert api.ip_cliente(Peticion("testclient", "1.2.3.4")) == "testclient"     # no es una IP
