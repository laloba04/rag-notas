"""
Tests de las métricas de recuperación con puestos inventados: sin modelos ni Chroma.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from evaluate import mrr, recall


def filas(*puestos):
    """Una fila por pregunta: el puesto del primer trozo correcto, o None si no salió."""
    return [{"puesto": p} for p in puestos]


def test_mrr_todo_en_primer_puesto_vale_1():
    assert mrr(filas(1, 1, 1)) == 1


def test_mrr_es_la_media_de_1_entre_el_puesto():
    assert mrr(filas(1, 2, 4)) == pytest.approx((1 + 1 / 2 + 1 / 4) / 3)


def test_mrr_cuenta_0_si_no_aparece():
    assert mrr(filas(1, None)) == pytest.approx(0.5)
    assert mrr(filas(None, None)) == 0


def test_mrr_distingue_lo_que_recall_no_ve():
    # El mismo recall@5, pero en un caso el bueno sale primero y en el otro quinto.
    arriba, abajo = filas(1, 1), filas(5, 5)
    assert recall(arriba, 5) == recall(abajo, 5) == 1
    assert mrr(arriba) == 1 and mrr(abajo) == pytest.approx(0.2)


def test_mrr_siempre_entre_recall_1_y_recall_10():
    # Lo que está garantizado: cada acierto en el puesto 1 suma 1 (MRR >= recall@1) y cada
    # término vale como mucho 1 y solo cuenta si está en el top 10 (MRR <= recall@10).
    azar = __import__("random").Random(0)
    for _ in range(500):
        f = filas(*[azar.choice([None, *range(1, 11)]) for _ in range(23)])
        assert recall(f, 1) <= mrr(f) <= recall(f, 10)


def test_mrr_puede_superar_recall_5_si_hay_aciertos_entre_6_y_10():
    # Por eso "MRR <= recall@5" es una comprobación útil en nuestros datos, no una ley.
    f = filas(1, 1, 1, 6)
    assert mrr(f) > recall(f, 5)
