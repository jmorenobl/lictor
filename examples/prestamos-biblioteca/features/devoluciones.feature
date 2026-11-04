# language: es
Característica: Devoluciones

  Antecedentes:
    Dado el catálogo
      | isbn | título     | ejemplares |
      | 111  | La Regenta | 2          |
    Y que "ana" tiene prestado "111" desde el "2026-11-02"

  Regla: Devolver a tiempo no tiene multa

    @DEV-001
    Escenario: Devolver antes del vencimiento
      Cuando "ana" devuelve "111" el "2026-11-20"
      Entonces la multa es de "0,00" euros
      Y quedan 2 ejemplares de "111"

  Regla: Cada día de retraso cuesta 0,20 euros, hasta un máximo de 5 euros

    @DEV-002
    Escenario: Devolver con tres días de retraso
      Cuando "ana" devuelve "111" el "2026-11-26"
      Entonces la multa es de "0,60" euros

    @DEV-003
    Escenario: La multa no pasa del máximo
      Cuando "ana" devuelve "111" el "2027-02-01"
      Entonces la multa es de "5,00" euros
