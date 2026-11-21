# ADR-0013: Un control de complejidad que mide, informa al refactor y le impide empeorar

## Estado
Aceptada (2026-11-21). Sale de F60 (`.audits/romanos/fricciones.md`) y corrige el punto 6 de la
ADR-0010. Se midió antes de diseñarla (`.audits/qa/f60/medicion.md`), y la revisó dos veces por su
cuenta un revisor independiente, que ejecutó las medidas; esta versión recoge sus hallazgos (al
final).

## Contexto
La ADR-0010 puso un refactor tras cada verde con objetivos de calidad en el prompt
(`refactor_hints`), y dejó la complejidad sin control: «primero se mide si el prompt basta; si no,
un control con puntuación y `min_score`». No bastó. En la pasada (a) de la fase 2, `_primario`
quedó con CC 12 según lizard; tres refactors la tuvieron delante y la estimaron en «unas 6 ramas».
Ni el perfil ni el prompt dicen qué definición se usa, y las herramientas no coinciden: con ruff
`C901` la misma función es CC 7 (`.audits/qa/f60/medicion.md`). Las estimaciones a ojo del agente
coinciden con mccabe (CALC-022 en la 0.8.0: «`_operando`, unas 6 ramas»; ruff 6, radon 10).

Tampoco sirve como `min_score`: la complejidad no es un porcentaje que deba llegar a un mínimo,
es un máximo por función. Y nada impide hoy que un refactor empeore el código si pasa los controles
(ADR-0010, Consecuencias).

## Decisión

1. **Una definición de Lictor, fija, la que midieron las pasadas.** La complejidad de una función
   es 1 más:
   - cada `if` y `elif`, y cada ternario;
   - cada `for` y `while` (también `async for`), y cada `for` e `if` de una comprensión;
   - cada `except` (también `except*`);
   - cada `case` de un `match`, salvo el último si captura cualquier valor sin guarda (`case _`,
     `case otro`);
   - por cada `and`/`or`, uno por operando después del primero (`a and b` suma 1, `a and b and c`
     suma 2), también fuera de un `if`.

   No cuentan el `else` de un bucle o de un `try`, `finally`, `with` ni `assert`. Una función
   anidada o un método (también el de una clase definida dentro de una función) cuentan aparte;
   una lambda, en la función que la contiene. Los decoradores y los valores por defecto no son de
   la función. El código de módulo y el cuerpo de una clase no se miden.
   - Da lo mismo que radon 6.0.1 y lizard 1.25.0 en las 149 funciones de las pasadas, así que las
     metas y los datos de la fase 2 siguen valiendo (comprobado otra vez sin `assert` ni `else`: 0
     distintas en esas 149 y en las 999 de `src/lictor`). Difiere de radon en lo que esas funciones
     no tienen: radon cuenta `assert` y el `else` de bucles y `try`, no entra dentro de un
     `assert`, no conoce `except*`, no cuenta un `case _ if …` final y no lista los métodos de una
     clase definida dentro de una función; lizard cambia según la versión (`match` con tres `case`: 1 en 1.21, 4 en
     1.25). Una tabla de tests fija cada construcción.
   - No es la de ruff `C901` (mccabe), la habitual en Python: no cuenta `and`/`or`, ternarios ni
     comprensiones, que son caminos que un test tiene que recorrer, y suma cada función anidada a
     la de fuera además de darla aparte (`evaluar`, 3, sale con 30). Tampoco la complejidad
     cognitiva: rompe la comparación con la fase 2 y no se explica en una frase del prompt.

2. **La mide un módulo del perfil, no una dependencia.** `python_uv/lictor_complexity.py` recorre
   el `ast` de `src` y escribe cada función con su valor. Va en `verification.files`, como los
   plugins: entra en el hash del entorno y en `config_fingerprint`, y existe en todo proyecto
   `python-uv` sin que una persona añada nada. Por eso no hace falta ningún salto para un proyecto
   sin la herramienta.
   - El control es `uv run python -I -B -m lictor_complexity src --out {out}`: `uv run` ya usa el
     entorno de verificación (`UV_PROJECT_ENVIRONMENT`), sin marcadores nuevos.
   - Lee el código, no lo importa ni lo ejecuta, no lee configuración y no admite supresiones por
     comentario (lizard sí: `#lizard forgives`; radon lee `radon.cfg`, `setup.cfg`,
     `pyproject.toml`, `~/.radon.cfg` y `RADONCFG`).
   - Un fichero que no se puede analizar hace fallar el comando: no hay informe, y el control falla
     sin medida. En `measure` eso deja al verde sin línea base, y al refactor siguiente sin guarda
     ni lista; el `lint` de `python-uv` lo corta antes, y la evidencia del control lo dice.
   - radon no se publica desde 2023 y lizard cambia de definición entre versiones, que fijaría el
     `uv.lock` de cada proyecto, no el perfil.

3. **Un informe de hechos y un veredicto del dominio** (ADR-0009). El informe (`report:
   "complexity"`) lista cada función con fichero (ruta relativa, sin `..`), línea, nombre y valor.
   `ComplexityReader` da un `ComplexityReport`, `MissingReport` o `InvalidReport` (valor menor que
   1, campos que faltan, una ruta absoluta o que sale del árbol).
   - El límite es del control: `limit`, solo con este informe, una decisión del proyecto que
     `lictor.json` cambia por `id`, como `min_score`. El perfil pone 10. Pasa a `VerdictRule`.
   - `complexity_verdict` pasa si ninguna función supera el límite, y si no falla nombrándolas de
     mayor a menor.
   - Lo nuevo de cada capa: `ReportFormat`, `ReportReaders`, `limit` en los modelos y en
     `resolve_shape`, `schema/lictor.schema.json`, el códec de `verify.finished.v2` (`limit`,
     `over`, `excess`, `peak`), `report.md`, `[tool.mutmut]` y una fila de
     `characterization_reports.jsonl`.

4. **En el perfil, `measure`: mide en cada intento y no bloquea.** Bloquear al implementador
   repetiría los reintentos de F55 por algo que el paso siguiente arregla, así que el implementador
   no lo ve. Queda en `verify.finished` y en `report.md`. Un proyecto que lo quiera bloqueante
   escribe `mode: block`. No hay excepción por rol: `consequence` no conoce el rol, y con `block`
   ningún verde puede dejar una función por encima, así que el refactor siempre parte de `excess`
   0. Lo que `block` cuesta es otra cosa, y la ADR lo declara: el control mide todo `src`, así que
   en un proyecto con código previo por encima del límite ningún escenario llega a verde hasta que
   el implementador arregle funciones que no son suyas; y para no repetir F55, con `block` el
   prompt del implementador dice el límite y la definición. En `lictor check` hace lo que su modo
   diga.

5. **El refactor no puede dejar el código más complejo que el verde** (`L0-regresión`). Lo que el
   control deja por cumplir son dos números, como en F61, y ninguno puede subir:
   - `excess`: lo que superan el límite, sumado, las funciones que lo superan (`_primario` con 12 y
     límite 10 deja 2);
   - `peak`: lo que lo supera la peor (0 si ninguna).

   `_measure_of` gana una rama para `ComplexityReport`, `MissKind` dos tipos (`excess`, `peak`), y
   `refactor_regressions` los compara sin cambiar de regla. Los textos que dicen «deja sin probar»
   (`regression_reason`, `refactor_measures_text`) pasan a decir qué empeora en cada caso.

   | Refactor | `excess` | `peak` | |
   |:--|:--|:--|:--|
   | una de 14 baja a 12 | 4 → 2 | 4 → 2 | se acepta: mejora sin llegar |
   | una de 14 se parte en dos de 11 | 4 → 2 | 4 → 1 | se acepta |
   | dos de 11 pasan a una de 12 y otra de 10 | 2 → 2 | 1 → 2 | se descarta: concentra |
   | una de 9 sube a 11 | 0 → 1 | 0 → 1 | se descarta |

   Límites conocidos: no ve lo que pasa por debajo del límite (tres funciones de 4 fundidas en una
   de 10), ni ramas llevadas a código de módulo o al cuerpo de una clase, ni `all([a, b])` en lugar
   de `a and b`. Descarta quitar duplicación si deja una función por encima donde el verde no
   tenía ninguna, que es coherente con el límite. Y `peak` descarta tres funciones de 12 que pasan a
   una de 13 y el resto en 10 o menos (`excess` 6 → 3, `peak` 2 → 3), aunque el total mejora.

6. **El refactor ve lo que incumple, esté en el fichero que esté.** La verificación del verde ya lo
   midió: la medida de `refactor_baseline` guarda también las funciones por encima del límite
   (`Measure.over`), y el prompt las nombra con su valor y con el límite, que escribe Lictor desde el control. El perfil
   deja en `refactor_hints` la definición, sin cifra: con un `limit: 15` en el proyecto, el prompt
   no puede decir 10. Con la lista vacía, el prompt lo dice («ninguna función supera el límite»):
   si calla, el agente vuelve a estimar a ojo.
   - **El agente lo mide él (F55).** Sin esa medida, tras editar estima a ojo con la intuición de
     mccabe, que no cuenta `and`/`or`, y ahora ese error es un descarte que no pudo ver. No se le da
     el intérprete del entorno de verificación: sería la primera vez que el agente ejecuta algo de
     él, y el prompt le daría su ruta. Lictor deja una copia del módulo fuera del proyecto y de la
     copia de trabajo, durante la llamada (`agent_files_dir`, en el directorio temporal privado), y
     el marcador `{lictor_files}` se sustituye en el `agent_run` y en la regla de `agent_tools`
     (`agent_tools_with_files`); una llamada sin la copia, como la del driver, pierde la regla.
     `agent_run_problems` compara las dos con el marcador escrito. Se probó con el CLI real (2.1.330)
     que la regla Bash con ruta absoluta se admite con `dontAsk` y que el resto se deniega. Con
     `mode: block`, el implementador recibe la misma copia y la orden.

7. **Un intento, y la frase dice qué descarta.** Los «sin cambios» por miedo citan la regla de
   descarte, no el número de intentos: de los 7 de 18 de la pasada (a), solo CALC-013 nombra el
   intento único; los demás, la cobertura, el comportamiento o el descarte. Tras F61, en la 0.8.0,
   3 de 13 siguen citando el riesgo, con 7 aplicados y 0 descartados de 20: no hay datos para un
   segundo intento. El prompt pasa a decir:
   > Si la verificación no pasa, Lictor descarta tu cambio y el escenario sigue en verde: un
   > descarte no le cuesta nada al escenario. Se descarta un refactor que cambie el comportamiento,
   > toque lo que no puedes tocar, deje más sin probar que el verde, o deje por encima del límite
   > de complejidad más que el verde (en total o en la peor función). Por debajo del límite, la
   > complejidad no cuenta; mejorar una función de la lista sin llegar al límite no se descarta, y
   > quitar código probado tampoco. Si la lista de arriba tiene funciones, baja al menos una,
   > empezando por las de los ficheros que cambió este escenario; no hace falta arreglarlas todas
   > ahora. Si está vacía y el código cumple los demás objetivos, dejarlo como está es un resultado
   > válido.

   Es la de Fable en la segunda revisión: la anterior («si hay funciones en la lista, no los
   cumple») obligaba a tocar en cada llamada mientras quedase una, también en código adoptado, y
   «más complejidad que el verde» disuadía de fundir funciones pequeñas, que la guarda no mira.

   La pasada repetida mide aplicados, descartes y coste frente a 7 / 0 / 13 de la 0.8.0.

8. **Lictor también lo hereda.** Su `lictor.json` usa `python-uv`, y con esta definición
   `src/lictor` tenía 41 funciones por encima de 10 (su `lint`, con `C901`, da 0). En `measure` no
   bloquea; se queda medido, y bajarlas es trabajo aparte. El cambio de esta ADR subía cinco, y el
   control lo vio en su propio `check`: se bajaron antes de integrarlo, y quedan 39. Su `lictor.json`
   lo nombra (`{"id": "complexity"}`) para que se vea que lo mide.

## Consecuencias
- Un control más por verificación, sin dependencias: recorrer el `ast` de `src` cuesta milésimas.
- El refactor recibe una lista concreta y dos números que no pueden subir; lo demás de
  `refactor_hints` sigue siendo juicio del agente.
- Cambia el perfil (control, `verification.files`, `refactor_hints`): va al CHANGELOG. Cambia
  `config_fingerprint`, así que un run a medias no se reanuda con la versión nueva.
- Un proyecto con `C901` en su `lint` verá dos números distintos; el que cuenta para el refactor es
  el del control.
- El arnés de repro no lo quita del proyecto ligero: cuesta milésimas, y así cada caso comprueba
  que medir no ejecuta código del proyecto (`test_project_code_only_runs_where_the_tests_import_it`).
- El módulo busca `match` con `getattr`, así que vale en un entorno anterior a Python 3.10.

## Cómo se comprueba
Se repite la pasada de la calculadora (`fase-2/pasada.sh`). Metas: CC máxima ≤ 10 y ninguna
función por encima, con esta definición; 0 falsos verdes; ningún refactor descartado por este
control sin haber empeorado el código; y el coste del refactor no más de un 20 % sobre los 0,96 $
de la fase 2.

## Revisión independiente (Fable, 2026-11-21)
Comprobado al ejecutarlo, e incorporado arriba:

| Hallazgo | Comprobación | Cambio |
|:--|:--|:--|
| `{env_dir}` no se sustituye en `run` ni en `agent_run` | `fill_report_paths` solo cambia `{out}` (`run_files.py:231`) | control con `uv run`; el agente mide con una copia del módulo (punto 6) |
| La complejidad no es un `Score` | `score_misses(score: Score)`, `report_score` | rama propia en `_measure_of`, `limit` en `VerdictRule` |
| El límite, en dos sitios | `refactor_hints` dice 10 en texto fijo | la cifra la escribe Lictor desde el control |
| El miedo es a la regla de descarte, no al intento | 1 de 7 nombra el intento (CALC-013) | frase causal corregida |
| El prototipo no es radon | sonda: `except*`, `case _ if`, `assert`, clase en función | definición propia, con divergencias listadas |
| La suma acepta concentrar | dos de 11 → 12 + 10 | `peak` además de `excess` |

Segunda revisión, sobre el borrador 2:

| Hallazgo | Comprobación | Cambio |
|:--|:--|:--|
| El 14 → 12 con `block` no puede darse | `consequence` no conoce el rol (`results.py:289`); un `block` que falla corta antes de `refactor_regressions` (`verify_attempt.py:376-394`) | sin excepción por rol; la ADR declara el coste real de `block` |
| La frase obliga a tocar con lista larga y disuade de fundir | razonado sobre la guarda; los datos de la 0.8.0 no lo deciden | frase de Fable |
| Que el agente mida no puede quedar abierto | sin medida, su error es un descarte que no ve | decidido, condicionado a la prueba del CLI |
| Límites y divergencias sin listar | `peak` 6 → 3 / 2 → 3; `case _ if`; clase dentro de función | añadidos |
| Definición sin `assert` ni `else` | Fable: 0 distintas en 149 y 999 | anotado |
| `uv run python -I -B -m …` usa el entorno | probado por Fable con un entorno aparte, sin `.pyc` ni `.venv` | — |
