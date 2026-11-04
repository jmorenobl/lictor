# Préstamos de una biblioteca

Ejemplo de Lictor para **adoptarlo en un proyecto que ya tiene código**. Los préstamos
(`features/prestamos.feature`, `PRE-001` a `PRE-003`) ya están implementados en `src/biblioteca/`
y probados en `tests/unit/`. Las devoluciones (con `Regla:` para las multas) y las reservas todavía
no existen.

Copia el ejemplo a un repositorio nuevo, sin `lictor.json`, y configúralo:

```bash
git init && lictor init && git add -A && git commit -m "chore: proyecto inicial"
```

Un driver nuevo no puede distinguir un test que pasa porque el código ya cumple el escenario de uno
que no comprueba nada. Por eso, antes de la primera ejecución, registras tú los escenarios que ya
cumple el código:

```bash
lictor register PRE-001 PRE-002 PRE-003
lictor run
```

`lictor register` explica qué afirmas y te pide confirmación. Lictor no prueba esos tres escenarios:
el driver que escriba después solo tendrá que pasarlos, y el informe los marca como registrados a
mano. Los otros seis siguen el ciclo normal: rojo, implementación y verificación.
