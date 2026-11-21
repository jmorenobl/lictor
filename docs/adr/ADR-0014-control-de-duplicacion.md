# ADR-0014: Un control de duplicación que mide, informa al refactor y le impide empeorar

## Estado
Aceptada (2026-11-21). Sale de F65 (`.audits/romanos/fricciones.md`) y repite la ADR-0013 con otra
medida. Se midió antes de diseñarla (`.audits/qa/f65/medicion.md`), y la revisó por su cuenta un
revisor independiente, que ejecutó las medidas y probó casos propios
(`.audits/qa/f65/revision-fable.md`); esta versión recoge sus hallazgos y lo que salió al
implementarla (al final).

## Contexto
En la repetición de la pasada de la calculadora con F60, el implementador de CALC-025 escribe el
evaluador copiando el analizador, y la pasada acaba con 13,7 % de duplicación (jscpd) frente a la
meta del 3 %. Seis refactors lo tuvieron delante; los que lo nombran lo descartan («extraerlos no
compensa el riesgo», «es mínima»). El prompt pide «sin duplicación» y nadie lo mide: el agente lo
estima, y lo estima mal. Es F60 con otra medida.

La medición añadió dos cosas que el caso no mostraba:
- **El clon de estructura también está en las pasadas «buenas».** En la F60 y en la mixta (2,8 % y
  2,1 % con jscpd) el evaluador repite el bucle de precedencias del analizador con otros nombres, y
  jscpd no lo ve porque corta en cada nombre distinto. Con una definición exacta, además, lo más
  barato para bajar el número es renombrar una copia.
- **Ninguna herramienta de Python sirve tal cual.** jscpd es de Node; pylint `duplicate-code` no ve
  un clon dentro de un fichero (la calculadora 0.5.0, toda en `__init__.py`: 9,6 % con jscpd, 0
  con pylint), es una dependencia y lee configuración; lizard `-Eduplicate` 1.25.0 solo ve clones
  dentro de un fichero, y da 0 % en todo el corpus.

## Decisión

1. **Una definición de Lictor, fija.** Un clon es un trozo de al menos **50 tokens** de Python que
   aparece en dos sitios de `src`, en un fichero o en dos, con los nombres y los literales
   **renombrados de forma coherente**: dentro de cada función, cada nombre o literal de un sitio
   corresponde siempre al mismo del otro, y al revés (`a + b` / `x + y` casa; `a + b` / `x + x`,
   no). Además:
   - no cuentan comentarios, líneas en blanco, saltos de línea dentro de paréntesis ni sangrado: un
     cambio de formato no cambia nada;
   - no cuentan ni cortan las importaciones, las firmas (de `def` a sus dos puntos) ni los
     docstrings: dos cuerpos iguales con firmas distintas son un clon, y dos firmas iguales no;
   - la correspondencia de nombres vuelve a empezar en cada función, así que renombrar una
     variable local no rompe el clon con la función de al lado;
   - una **tabla** ni cuenta ni une lo que tiene alrededor: una unidad de menos de 50 tokens, sin
     palabras clave de sentencia (`if`, `for`, `return`…), repetida seguida durante 50 tokens o más,
     como las entradas de un `dict`, una lista de campos o de argumentos con nombre. La lógica
     desenrollada (seis `if` iguales seguidos) no es una tabla;
   - un trozo que repite una unidad de 50 tokens o más (una función copiada muchas veces) cuenta
     como las copias de esa unidad;
   - un trozo que se repite solapándose consigo se corta donde empieza a solaparse; dos sitios que
     se solapan son uno, y los clones van por **familias**, cada una con todos sus sitios;
   - un clon no empieza en el fin de la sentencia anterior; una cadena f es un token entero, su
     texto del fuente, para que nada dependa de la versión de Python, y las posiciones se comparan
     en caracteres (`ast` las da en bytes).

   Por qué 50: en los proyectos de las pasadas, 40 y 50 dan lo mismo salvo métodos de 44–48 tokens
   que caen con el recorrido; en `src/lictor`, 40 daba 210 pares, casi todo trozos de lo mismo; 70
   pierde el bucle de la F60 (53 tokens). Una tabla de tests fija cada caso
   (`test_lictor_duplication.py`).

2. **La mide un módulo del perfil, no una dependencia.** `python_uv/lictor_duplication.py`, solo
   stdlib (`tokenize`, `ast`), en `verification.files` como `lictor_complexity.py`: entra en el hash
   del entorno y en `config_fingerprint`, no importa ni ejecuta el código del proyecto, no lee
   configuración ni admite supresiones por comentario. El control es
   `uv run python -I -B -m lictor_duplication src --out {out}`, nivel L1, `measure`.
   - Corre en el intérprete del proyecto, que puede ser anterior al de Lictor: da el mismo informe
     con Python 3.9, 3.10, 3.11 y 3.14, y `pyproject.toml` fija `py39` como versión objetivo de ruff
     para los módulos del perfil.
   - Un fichero que no se analiza hace fallar el comando sin informe, como en la ADR-0013.
   - Cuesta 0,02–0,03 s en un proyecto de las pasadas y 0,3 s en `src/lictor` (91 000 tokens); el
     peor caso medido, 600 copias de una función en un fichero, 9 s.

3. **Un informe de hechos y un veredicto del dominio** (ADR-0009). `report: "duplication"` da el
   mínimo con el que se midió, cada fichero con sus tokens y los tramos que están en un clon, ya
   unidos, y cada familia con sus sitios (fichero, primera y última línea, tramo de tokens
   `[inicio, fin)`). `DuplicationReader` da un `DuplicationReport`, `MissingReport` o
   `InvalidReport`: una ruta que sale del árbol, un fichero dos veces, una familia de menos de dos
   sitios, un sitio fuera de su fichero, más corto que el mínimo o que se solapa con otro de su
   familia, o unos tramos cubiertos que no son exactamente la unión de los sitios. Así el número
   del refactor no puede significar otra cosa que lo que listan los clones.
   - `duplication_verdict` pasa sin clones y si no falla nombrándolos, de mayor a menor. No hay
     `limit`: con `block` no se admite ninguno.
   - Los tres veredictos de un comando con informe propio (mutación, complejidad, duplicación)
     comparten ahora lo que comprueban antes del informe (`_own_report`: no cortado, informe
     legible, código 0), y los cuatro lectores JSON un solo `json_report`.

4. **En el perfil, `measure`**, por lo mismo que la ADR-0013 (punto 4): el implementador no lo ve.
   Con `block`, su prompt dice que no puede haber código repetido y la orden para medirlo; y como
   mide todo `src`, en un proyecto con clones previos ningún escenario llega a verde hasta que
   alguien los quite.

5. **El refactor no puede dejar más código repetido que el verde** (`L0-regresión`). El número es
   **tokens en clones**: los cubiertos por algún sitio de alguna familia, todas las copias. Es
   simétrico: no depende del nombre de los ficheros ni de su orden. Un `MissKind` nuevo,
   `duplicated`, en `refactor_regressions`, sin cambiar la regla.
   - No líneas: partir una llamada en tres líneas las sube sin duplicar nada, y `python-uv` no exige
     formato. No porcentaje: quitar código no duplicado lo sube sin empeorar nada (F61).

   Medido con el módulo sobre la repetición de F60 (506 tokens en clones al final):

   | Refactor | Tokens en clones | |
   |:--|:--|:--|
   | saca `_actual`, `_posicion` y `_es` a una base común | 506 → 336 | se acepta |
   | el evaluador recorre el árbol del analizador en lugar de los tokens | 506 → 0 | se acepta |
   | renombra una variable local de un método | 506 → 506 | ni mejora ni empeora |
   | renombra `analizador.py` a `sintaxis.py` | 506 → 506 | ni mejora ni empeora |
   | dos funciones nuevas con el mismo cuerpo de 50 tokens | 0 → 100 | se descarta (caso de repro) |

   Límites conocidos: no ve repeticiones de menos de 50 tokens ni las que cambian de estructura
   (un `for` en una y una comprensión en la otra); un cambio mínimo en medio de un clon (una
   sentencia de más) lo parte y lo baja sin mejorar nada; mover un método dentro de su clase
   todavía puede moverlo, porque un clon cruza funciones; y una tabla con una cabecera distinta en
   cada copia sigue sin contar, aunque sea la misma tabla dos veces.

6. **El refactor ve los clones del verde, estén en el fichero que estén.** La verificación del verde
   ya los midió (`refactor_baseline`, que guarda los 20 primeros y cuántos había); el prompt los
   nombra, primero los que tienen un sitio en un fichero que cambió el escenario y luego por
   tamaño, con el número que no puede subir:
   > Código repetido, medido en la verificación del verde (un clon es un trozo de 50 tokens o más
   > que se repite en dos sitios, con los nombres y los literales cambiados de forma coherente
   > dentro de cada función):
   > - src/calculadora/analizador.py:36-53 y src/calculadora/evaluador.py:61-78: 112 tokens
   > Quita al menos uno, empezando por los de los ficheros que cambió este escenario; no hace falta
   > quitarlos todos ahora.
   > La verificación descarta el refactor si deja más tokens en clones que el verde (506). El
   > formato no cuenta, ni cambiar el nombre de una variable dentro de una función.
   > Mídelo tú con `…/lictor_duplication.py src`, sola y no detrás de otras órdenes con `&&`: una
   > vez antes de editar y otra al terminar. Su segunda línea dice los tokens en clones.

   Con la lista vacía lo dice («Ningún clon»). La orden (`agent_run`
   `uv run python -I -B {lictor_files}/lictor_duplication.py src`) va con su regla en
   `agent_tools`, como la de complejidad.
   - **El clon de estructura** (F65): `refactor_hints` pasa de «extrae lo que se repite en una
     función con nombre» a «lo que se repite se escribe una vez; si dos partes recorren lo mismo,
     comparte el recorrido, que una use el resultado de la otra o una base común, en lugar de
     extraer trozos sueltos». La guarda no distingue cómo se quita; las pasadas miden qué hace el
     agente.

7. **Un mecanismo con la complejidad; dos informes.** Lo medido es distinto (funciones con un valor;
   familias de tramos), así que informe, lector y veredicto van aparte. Lo que el refactor hace con
   ellos se generaliza en lugar de copiarse:
   - `Measure.over` pasa a `Measure.findings: OverLimit | Duplication`, con un `kind` que los
     distingue en el checkpoint;
   - `{complejidad}` pasa a `{medidas_del_codigo}`: un bloque por control que mide el código, en el
     orden declarado, con la misma forma (`RefactorBlock`, `domain/code_measures.py`);
   - `complexity_controls` pasa a `code_controls` (`complexity`, `duplication`), que
     `refactor_measures` excluye para no dar su orden dos veces;
   - `verify.finished` guarda `duplication: {clones, in_clones, code_tokens}`, y `report.md` y
     `lictor check` lo dicen en una frase («2 clones, 250 de 1800 tokens en clones»).

8. **Lictor también lo hereda.** Su `lictor.json` lo nombra (`{"id": "duplication"}`). `src/lictor`
   tenía 23 clones y 3557 tokens en clones en `master`; la primera versión de este cambio lo subía
   a 3977 (el `main` del módulo calcaba el de complejidad, la unión de tramos estaba dos veces y los
   veredictos repetían su esqueleto) y el control lo vio. Se quitó antes de integrar: quedan 22
   clones y 3304 tokens. El único nuevo es la unión de tramos del módulo frente a la del dominio,
   que valida el informe: el módulo no puede importar Lictor.

## Consecuencias
- Un control más, sin dependencias; cambia el perfil (control, `verification.files`,
  `agent_tools`, `refactor_hints`): va al CHANGELOG, y un run a medias no se reanuda.
- El refactor recibe dos listas. La hipótesis de F65 (lo medido sustituye a todos los objetivos)
  se vigila en las pasadas: con dos medidas, si las funciones largas, que no se miden, empeoran.
- El arnés de repro no lo quita del proyecto ligero, como la complejidad: cuesta milésimas.

## Cómo se comprueba
Se repite la pasada de la calculadora dos veces (`fase-2/pasada.sh`) y una mixta
(`f60/pasada-mixta.sh`). Metas: **ningún clon al final** con esta definición, en todas (el 3 %
eran 45–57 tokens en estos proyectos, y el clon mínimo son 50: ya era «ninguno»), con jscpd al lado
solo para comparar; CC máxima ≤ 10 y ninguna función por encima; 0 falsos verdes; ningún refactor
descartado por este control sin haber empeorado; el coste del refactor no más de un 20 % sobre el
de F60 (~0,040 $ por refactor). Por clon listado: si lo quita y cómo (extraer o rediseñar); cuántos
«sin cambios» citan el riesgo con la lista delante; si se ejecutan las dos medidas.

**Resultado (2026-11-21, `.audits/qa/f65/analisis.md`):** en las tres pasadas, ningún clon al
final (0,0 % también con jscpd), CC máxima 9–10 sin ninguna por encima, 0 falsos verdes y 0
descartes en 46 refactors. Solo dos verdes dejaron un clon, los dos el del recorrido paralelo, y sus
refactors lo quitaron a la primera compartiendo el recorrido (una función común en un caso, un
árbol que el evaluador recorre en el otro). El coste por refactor sube a 0,049–0,055 $: un «sin
cambios» cuesta lo mismo que antes, y lo que sube es que hay más aplicados y más grandes. El miedo
al descarte con la lista vacía sigue, como fricción aparte.

## Revisión independiente (Fable, 2026-11-21)
Comprobado aquí ejecutándolo, e incorporado arriba:

| Hallazgo | Comprobación | Cambio |
|:--|:--|:--|
| Renombrar una local movía el número | `actual` → `token` en `_posicion`: 249 → 170 | correspondencia por función |
| El número dependía del nombre del fichero | `analizador.py` → `sintaxis.py`: 249 → 250 | tokens cubiertos por todas las copias |
| Las listas regulares contaban | un `dict` de 40 entradas: 15 pares consigo mismo | tablas fuera |
| Pares en lugar de familias, solapes sin definir | `calc-050` y `0.7.0 b` listaban dos veces lo mismo | informe por familias |
| El agente no podía calcular el número | la unión de tramos no sale de una lista de pares | el módulo imprime el número; el dominio exige que sea la unión |
| 3 % = ningún clon | 45–57 tokens frente a un mínimo de 50 | meta: ningún clon |
| Posiciones en bytes y cadenas f con `{{` | sus casos | posiciones en caracteres; cadena f por su texto |
| lizard sí ve clones dentro de un fichero | su caso | corregido en el contexto |

Y con sus casos, al implementarla:

| Caso | Con el borrador 2 | Cambio |
|:--|:--|:--|
| 100 copias de una función en un fichero | 0 clones: los pares solapados se fundían en un sitio | un trozo que repite una unidad larga cuenta como sus copias |
| dos tablas o dos `dataclass` sin relación | contaban: la cabecera rompía la periodicidad | una tabla ni cuenta ni une |
| seis `if` iguales seguidos | según dónde acabara el clon, contaba o no | la tabla exige no tener palabras clave de sentencia |
