# language: es
Característica: Reservas

  Antecedentes:
    Dado el catálogo
      | isbn | título              | ejemplares |
      | 222  | Fortunata y Jacinta | 1          |
      | 333  | Misericordia        | 4          |
    Y que "luis" tiene prestado "222" desde el "2026-11-02"

  @RES-001
  Escenario: Reservar un libro sin ejemplares
    Cuando "ana" reserva "222"
    Entonces "ana" es la primera en la cola de "222"

  @RES-002
  Escenario: El ejemplar devuelto queda apartado para quien lo reservó
    Dado que "ana" reservó "222"
    Cuando "luis" devuelve "222" el "2026-11-10"
    Entonces quedan 0 ejemplares de "222"
    Y el ejemplar de "222" está apartado para "ana"

  @RES-003
  Escenario: No se reserva un libro con ejemplares
    Cuando "ana" reserva "333"
    Entonces se rechaza por "hay ejemplares disponibles"
