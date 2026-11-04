# language: es
Característica: Préstamos de libros

  Un socio se lleva un ejemplar durante 21 días, con tres préstamos a la vez como máximo.

  Antecedentes:
    Dado el catálogo
      | isbn | título              | ejemplares |
      | 111  | La Regenta          | 2          |
      | 222  | Fortunata y Jacinta | 1          |
      | 333  | Misericordia        | 4          |

  @PRE-001
  Escenario: Prestar un libro disponible
    Cuando "ana" pide prestado "111" el "2026-11-02"
    Entonces el préstamo vence el "2026-11-23"
    Y quedan 1 ejemplares de "111"

  @PRE-002
  Escenario: No se presta un libro sin ejemplares
    Dado que "luis" tiene prestado "222" desde el "2026-11-01"
    Cuando "ana" pide prestado "222" el "2026-11-02"
    Entonces se rechaza por "sin ejemplares"

  @PRE-003
  Escenario: Un socio no puede tener más de tres préstamos
    Dado que "ana" tiene prestado "111" desde el "2026-11-01"
    Y que "ana" tiene prestado "222" desde el "2026-11-01"
    Y que "ana" tiene prestado "333" desde el "2026-11-01"
    Cuando "ana" pide prestado "333" el "2026-11-02"
    Entonces se rechaza por "límite de préstamos"
