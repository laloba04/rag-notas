"""
Tests de la fusión RRF y del tokenizador de BM25, con listas inventadas: no cargan modelos.
"""
import pytest

from retrieve import rrf, tokenizar


def test_rrf_suma_1_entre_k_mas_puesto():
    fusion = dict(rrf([["a", "b"], ["b", "c"]], k=60))
    assert fusion["a"] == pytest.approx(1 / 61)
    assert fusion["b"] == pytest.approx(1 / 62 + 1 / 61)
    assert fusion["c"] == pytest.approx(1 / 62)


def test_rrf_premia_lo_que_aparece_en_las_dos_listas():
    # "b" no es primero en ninguna lista, pero aparece en las dos: gana a los que solo están en una.
    orden = [id_ for id_, _ in rrf([["a", "b", "x"], ["c", "b", "y"]])]
    assert orden[0] == "b"


def test_rrf_con_una_sola_lista_conserva_el_orden():
    assert [id_ for id_, _ in rrf([["a", "b", "c"]])] == ["a", "b", "c"]


def test_rrf_con_listas_vacias():
    assert rrf([[], []]) == []
    assert [id_ for id_, _ in rrf([["a"], []])] == ["a"]


def test_rrf_k_mas_pequeno_da_mas_peso_a_los_primeros_puestos():
    k1 = dict(rrf([["a", "b"]], k=1))
    k60 = dict(rrf([["a", "b"]], k=60))
    assert k1["a"] / k1["b"] > k60["a"] / k60["b"]


def test_tokenizar_quita_tildes_y_mayusculas():
    assert tokenizar("Configuración del Pod") == ["configuracion", "del", "pod"]


def test_tokenizar_separa_opciones_de_comandos():
    assert tokenizar("git commit --amend") == ["git", "commit", "amend"]
    assert tokenizar("Cache-Control: no-cache") == ["cache", "control", "no", "cache"]
