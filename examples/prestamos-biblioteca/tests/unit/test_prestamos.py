from datetime import date

import pytest

from biblioteca import Biblioteca, PrestamoRechazado


def _biblioteca() -> Biblioteca:
    biblioteca = Biblioteca()
    biblioteca.alta("111", "La Regenta", 1)
    biblioteca.alta("222", "Misericordia", 5)
    return biblioteca


def test_un_prestamo_dura_tres_semanas() -> None:
    prestamo = _biblioteca().prestar("ana", "111", date(2026, 12, 20))

    assert prestamo.vence == date(2027, 1, 10)


def test_prestar_saca_un_ejemplar_de_la_estanteria() -> None:
    biblioteca = _biblioteca()

    biblioteca.prestar("ana", "222", date(2026, 11, 2))

    assert biblioteca.disponibles("222") == 4


def test_sin_ejemplares_no_hay_prestamo() -> None:
    biblioteca = _biblioteca()
    biblioteca.prestar("luis", "111", date(2026, 11, 2))

    with pytest.raises(PrestamoRechazado, match="sin ejemplares"):
        biblioteca.prestar("ana", "111", date(2026, 11, 2))


def test_el_limite_se_comprueba_antes_que_los_ejemplares() -> None:
    biblioteca = _biblioteca()
    for _ in range(3):
        biblioteca.prestar("ana", "222", date(2026, 11, 2))

    with pytest.raises(PrestamoRechazado) as rechazo:
        biblioteca.prestar("ana", "222", date(2026, 11, 2))

    assert rechazo.value.motivo == "límite de préstamos"
