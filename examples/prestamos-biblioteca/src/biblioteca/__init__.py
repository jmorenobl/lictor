"""Préstamos de una biblioteca: catálogo, socios y ejemplares."""

from dataclasses import dataclass, field
from datetime import date, timedelta

DIAS_DE_PRESTAMO = 21
MAXIMO_DE_PRESTAMOS = 3


class PrestamoRechazado(Exception):
    """La biblioteca no puede hacer lo que se le pide; `motivo` dice por qué."""

    def __init__(self, motivo: str) -> None:
        super().__init__(motivo)
        self.motivo = motivo


@dataclass(frozen=True)
class Prestamo:
    """Un ejemplar en manos de un socio."""

    socio: str
    isbn: str
    desde: date

    @property
    def vence(self) -> date:
        return self.desde + timedelta(days=DIAS_DE_PRESTAMO)


@dataclass
class Biblioteca:
    """El catálogo, con los ejemplares que quedan en la estantería, y los préstamos en curso."""

    ejemplares: dict[str, int] = field(default_factory=dict)
    titulos: dict[str, str] = field(default_factory=dict)
    prestamos: list[Prestamo] = field(default_factory=list)

    def alta(self, isbn: str, titulo: str, ejemplares: int) -> None:
        self.titulos[isbn] = titulo
        self.ejemplares[isbn] = ejemplares

    def disponibles(self, isbn: str) -> int:
        return self.ejemplares[isbn]

    def prestamos_de(self, socio: str) -> list[Prestamo]:
        return [p for p in self.prestamos if p.socio == socio]

    def prestar(self, socio: str, isbn: str, dia: date) -> Prestamo:
        if len(self.prestamos_de(socio)) >= MAXIMO_DE_PRESTAMOS:
            raise PrestamoRechazado("límite de préstamos")
        if self.ejemplares[isbn] == 0:
            raise PrestamoRechazado("sin ejemplares")
        self.ejemplares[isbn] -= 1
        prestamo = Prestamo(socio, isbn, dia)
        self.prestamos.append(prestamo)
        return prestamo
