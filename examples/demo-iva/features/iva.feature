# language: es
Característica: IVA de una factura

  @IVA-001
  Escenario: IVA general
    Dado una factura con importe 100
    Cuando calculo el IVA general
    Entonces el total es 121

  @IVA-002
  Escenario: IVA reducido
    Dado una factura con importe 100
    Cuando calculo el IVA reducido
    Entonces el total es 110

  @IVA-003
  Escenario: Importe negativo
    Dado una factura con importe -5
    Cuando calculo el IVA general
    Entonces se rechaza por importe no válido
