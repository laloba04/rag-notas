"""
Tests del filtro por permisos. Usan una colección de Chroma REAL en memoria (el filtro where
lo aplica Chroma, que es lo que hay que comprobar), pero con un embedding de juguete y un
reranker falso: no cargan los modelos.

El peor caso para el filtro es preguntar con el texto literal del trozo restringido: sin
filtro saldría el primero en las tres búsquedas (vectorial, BM25 e híbrida).
"""
import chromadb
import pytest
from chromadb.utils.embedding_functions import EmbeddingFunction

import config
import retrieve

SECRETO = "Los Secrets individuales están limitados a 1MiB de tamaño"
TROZOS = [
    ("06-k8s-secrets.md#0", SECRETO, "06-k8s-secrets.md", "restringido"),
    ("06-k8s-secrets.md#1", "Un Secret guarda contraseñas, tokens o claves", "06-k8s-secrets.md",
     "restringido"),
    ("05-k8s-configmap.md#0", "Un ConfigMap guarda configuración no confidencial",
     "05-k8s-configmap.md", "publico"),
    ("14-git-stash.md#0", "git stash guarda los cambios sin confirmar", "14-git-stash.md", "publico"),
    ("16-http-cache.md#0", "Cache-Control indica cuánto tiempo guardar una respuesta",
     "16-http-cache.md", "publico"),
]
TODO = ("publico", "restringido")


class EmbeddingDeLetras(EmbeddingFunction):
    """Cuenta letras a-z: textos con palabras parecidas dan vectores parecidos. Sin modelo."""
    def __init__(self):
        pass

    def __call__(self, input):
        return [[float(t.lower().count(chr(c))) + 0.01 for c in range(97, 123)] for t in input]

    @staticmethod
    def name():
        return "letras-test"

    def get_config(self):
        return {}

    @staticmethod
    def build_from_config(config):
        return EmbeddingDeLetras()


@pytest.fixture
def coleccion_falsa(monkeypatch):
    cliente = chromadb.EphemeralClient()
    nombre = "test_permisos"
    if nombre in [c.name for c in cliente.list_collections()]:
        cliente.delete_collection(nombre)
    col = cliente.create_collection(nombre, embedding_function=EmbeddingDeLetras(),
                                    metadata={"hnsw:space": "cosine"})
    col.add(ids=[t[0] for t in TROZOS],
            documents=[f"passage: {t[1]}" for t in TROZOS],
            metadatas=[{"fuente": t[2], "seccion": "s", "confidencialidad": t[3]} for t in TROZOS])
    monkeypatch.setattr(retrieve, "_col", col)
    monkeypatch.setattr(retrieve, "_bm25", {})

    class RerankerFalso:
        def predict(self, pares):     # puntúa igual que BM25: palabras en común
            return [len(set(retrieve.tokenizar(p)) & set(retrieve.tokenizar(t))) for p, t in pares]
    monkeypatch.setattr(retrieve, "_reranker", RerankerFalso())
    return col


def fuentes(resultados):
    return {r[1]["fuente"] for r in resultados}


def test_por_defecto_solo_se_ve_lo_publico():
    assert config.ACCESO_POR_DEFECTO == ("publico",)
    assert retrieve.normalizar_acceso(None) == ("publico",)


@pytest.mark.parametrize("buscar", [
    lambda p, **kw: retrieve.buscar_vectorial(p, k=50, **kw),
    lambda p, **kw: [(t, m, 0) for _, t, m in retrieve.buscar_bm25(p, k=50, **kw)],
    lambda p, **kw: retrieve.buscar_hibrida(p, k=50, **kw),
    lambda p, **kw: retrieve.buscar(p, k=50, rerank=False, hibrida=False, **kw),
    lambda p, **kw: retrieve.buscar(p, k=50, rerank=False, hibrida=True, **kw),
    lambda p, **kw: retrieve.buscar(p, k=50, k_inicial=50, rerank=True, hibrida=True, **kw),
], ids=["vectorial", "bm25", "hibrida", "buscar", "buscar_hibrida", "buscar_rerank"])
def test_con_acceso_publico_el_documento_restringido_no_sale_nunca(coleccion_falsa, buscar):
    # k=50 es mayor que el número de trozos: si el filtro fallara, saldrían todos.
    for pregunta in (SECRETO, "¿Qué tamaño máximo puede tener un Secret?", "guarda"):
        assert "06-k8s-secrets.md" not in fuentes(buscar(pregunta))
        assert "06-k8s-secrets.md" not in fuentes(buscar(pregunta, acceso=("publico",)))
    # Y con acceso total sí sale: el test no pasa por no encontrar nada.
    assert "06-k8s-secrets.md" in fuentes(buscar(SECRETO, acceso=TODO))


def test_el_indice_bm25_publico_no_contiene_trozos_restringidos(coleccion_falsa):
    publico = retrieve.indice_bm25(("publico",))
    total = retrieve.indice_bm25(TODO)
    assert {m["confidencialidad"] for _, _, m in publico["trozos"]} == {"publico"}
    assert len(total["trozos"]) == len(TROZOS)


def test_un_trozo_sin_etiqueta_de_confidencialidad_no_se_ve(coleccion_falsa):
    coleccion_falsa.add(ids=["sin-etiqueta"], documents=["passage: trozo sin etiqueta"],
                        metadatas=[{"fuente": "x.md", "seccion": "s"}])
    assert "x.md" not in fuentes(retrieve.buscar_vectorial("trozo sin etiqueta", k=50, acceso=TODO))


@pytest.mark.parametrize("acceso", [("pubico",), (), ("publico", "secreto")])
def test_niveles_desconocidos_o_vacios_son_un_error(acceso):
    with pytest.raises(ValueError):
        retrieve.normalizar_acceso(acceso)


def test_normalizar_acceso_acepta_un_texto_y_ordena():
    assert retrieve.normalizar_acceso("publico") == ("publico",)
    assert retrieve.normalizar_acceso(["restringido", "publico", "publico"]) == TODO


def test_separar_acceso_de_los_argumentos():
    assert retrieve.separar_acceso(["--acceso=publico,restringido", "hola"]) == (TODO, ["hola"])
    assert retrieve.separar_acceso(["hola", "--sin-rerank"]) == (None, ["hola", "--sin-rerank"])
