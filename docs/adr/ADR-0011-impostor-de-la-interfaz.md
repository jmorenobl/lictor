# ADR-0011: Un escenario nuevo que el código ya cumple se prueba con un impostor de la interfaz

## Estado
Aceptada (2026-11-19). Sale de F58 (`.audits/romanos/fricciones.md`) y de la fase 2 del roadmap. Un
revisor independiente diseñó la solución por su cuenta, sin ver la primera propuesta, y esta
versión adopta su diseño (punto 2) con una corrección (el gancho de importación, punto 3).

Enmendada en la 0.18.0 (2026-11-25) con los objetos de valor, junto a la ADR-0016: ver la última
sección. Enmendada en la 0.17.0 (2026-11-24) con las corrutinas y las librerías.

Enmendada en la 0.10.0 (2026-11-20, «B»): la prueba tiene que venir de un Entonces, y lo que
devuelve un Dado no se altera. Cambian los puntos 2, 4 y 5; lo que motiva la enmienda está en
«Enmienda B», al final. Enmendada otra vez en la 0.11.0 (las colecciones) y con la ADR-0012 (las
fechas, y el punto 7, que pasa a ella).

## Contexto
Con un driver nuevo, un escenario nuevo que pasa a la primera es `unproven` (ADR-0004, punto 3):
nada distingue un test que pasa de uno que no comprueba nada. El driver se rechaza como
`sin_prueba` y, sin intentos, el run sale con 10 pidiendo `lictor register`.

En un proyecto que crece es lo normal: el agente escribe más de lo que pide cada escenario, y lo
que la persona escribe después ya se cumple. En las pasadas de la calculadora:

| Pasada | Runs rechazados | Coste de esos runs | Registros a mano |
|:--|--:|--:|--:|
| 0.6.1 | 2 | 0,37 $ | 6 |
| 0.7.0 (a) | 2 | 0,36 $ | 5 |
| 0.7.0 (b) | 0 | 0 $ | 0 |
| 0.7.0 sin refactor | 1 | 0,14 $ | 2 |

Cada vez hacen falta dos llamadas del driver y una intervención manual, y en todas el oráculo
externo confirmó que el código ya cumplía.

Hay además tres defectos en lo que existe:

1. **El mensaje no dice la verdad.** «Pasa sin llegar a la interfaz» se escribe sin ningún dato que
   lo respalde: solo se sabe que el test pasó.
2. **El mensaje empuja a romper un test correcto.** Pide que el test «falle mientras la interfaz no
   esté implementada», y con código que ya cumple eso es pedir que lo estropee.
3. **Los impostores de stubs no prueban nada si no hay stubs.** Sustituyen cada
   `raise NotImplementedError` por una constante; en un proyecto con código no sustituyen nada, la
   aceptación da lo mismo que el rojo, y la comprobación se da por buena (tanda 4 de la fase 2).

## Decisión

1. **Un escenario nuevo que pasa con un driver nuevo se intenta probar antes de rechazar el
   driver.** Solo los `unproven`, y solo si el perfil declara `oracle.interface_impostor`. Un
   reabierto que pasa sigue como hoy (`especificacion_cambiada`, exit 10): el impostor prueba que
   el test mira el resultado, no que lo compare con el valor **nuevo**, y un driver nuevo puede
   conservar el antiguo.

2. **El impostor altera lo que la interfaz devuelve al driver, no el código.** Un plugin del perfil
   envuelve las funciones y métodos definidos en `paths.writable` fuera de `paths.driver`. Cuando
   quien llama es un fichero del driver, ejecuta la función real y altera su resultado; cuando
   llama el propio código, la deja pasar. La aceptación corre tres veces más, cada una en un clon
   limpio. Solo se altera lo que devuelve una llamada hecha desde un Cuando o un Entonces (enmienda
   B): lo que obtiene un Dado, un fixture (aunque lo pida un Cuando) o el código fuera de un paso
   es el real. Los pasos que se alteran los fija el dominio (`ALTERED_STEPS`) y van en la variable;
   el plugin no tiene valor por defecto.

   | Ejecución | Qué hace |
   |:--|:--|
   | Control | Envuelve y cuenta, sin alterar nada. Tiene que pasar como el rojo. |
   | Modo `mas` | Número + 1, fecha + 1 día (ADR-0012), `bool` al contrario, texto con un carácter de más, colección con un elemento de más (una lista o tupla, al principio y de su forma: enmienda de las colecciones), `None` por un centinela; una excepción se traga y devuelve `None`. |
   | Modo `menos` | Número − 1, fecha − 1 día, `bool` al contrario, texto o colección con uno de menos (de una lista o tupla, el primero), o el centinela si estaba vacía, `None` por un centinela; una excepción se cambia por `ImpostorError`. |

   Cualquier otro objeto no se altera. Una alteración que deja el valor igual al real (un `inf`, un
   número enorme) no cuenta como alteración.

3. **El plugin envuelve al importar.** Un driver que hace `from paquete import evaluar` se queda con
   la función original si se envuelve después de importar, así que el plugin instala un buscador
   de módulos al cargarse y envuelve cada módulo de `paths.writable` al ejecutarlo. No envuelve
   clases, métodos especiales (`__init__`, `__eq__`…), generadores ni corrutinas. Sin su variable
   no hace nada, así que va en los `plugins` de siempre.

4. **Un escenario queda probado solo si:**
   - pasa con el código real y en la ejecución de control;
   - en la de control, su test llamó a la interfaz desde un Cuando o un Entonces;
   - en cada modo se alteró al menos una llamada de su test, y no solo el `None` de una orden;
   - en **cada** modo falla con una aserción **de un Entonces** cuyo último marco dentro del
     proyecto está en `paths.driver` (`pytest.fail` y el «DID NOT RAISE» de `pytest.raises`
     incluidos), o con el `ImpostorError` del modo `menos` **en un Cuando o un Entonces**;
   - las tres ejecuciones son de fiar (`trust`) y mapean igual que el rojo, y en ningún modo se
     alteró lo que devuelve un Dado o un fixture (si el plugin lo hiciera, no se sabe nada).

   La regla es pura, del dominio (`interface_proof`). Los hechos los deja el plugin en el JUnit
   (`lictor.interface.calls`, `lictor.interface.altered`) y `lictor_outcome` (`lictor.frame`, y
   `lictor.failure` = `impostor` por la clase de la excepción).

   | Test (resultado real 6) | `mas` | `menos` | Veredicto |
   |:--|:--|:--|:--|
   | `assert x == 6`, o espera `1` o `True` | falla | falla | probado |
   | `pytest.raises(ErrorDeCalculo)` | no se lanza | `ImpostorError` | probado |
   | Sin assert, lee `.total` | pasa | pasa | sobrevive |
   | `assert x`, `is not None`, `isinstance(x, float)` | pasa | pasa | sobrevive |
   | `assert x != 5`, `assert x < 7` | uno pasa | | sobrevive |
   | `pytest.raises(Exception)` | falla | lo atrapa, pasa | sobrevive |
   | `assert evaluar(a) == evaluar(b)` | pasa | pasa | sobrevive |
   | `assert r.valor == 4` (un objeto) | sin alterar | sin alterar | sin alterar |
   | Lo llama por un subproceso | 0 llamadas | | no llama |
   | El Dado calcula el resultado y el Entonces lo compara | sin llamadas desde el Cuando | | no llama |
   | `assert x == 6` en el Cuando, y el Entonces solo lo repite | falla en el Cuando | falla en el Cuando | fuera del Entonces |
   | `pytest.raises` en el Cuando, y el Entonces lee la excepción | «DID NOT RAISE» en el Cuando | `ImpostorError` | fuera del Entonces |
   | La orden del Cuando devuelve `None` y el Entonces lee una fecha | pasa | pasa | sin alterar |
   | `assert resultado is None` en el Entonces | falla | falla | probado |

   Las aserciones de un Dado ya no prueban nada: lo que obtiene un Dado no se altera, y la
   comprobación del escenario va en el Entonces.

5. **Lo que no se prueba sigue el camino de hoy, con un mensaje cierto.** Si alguno de los
   escenarios no queda probado, el driver se rechaza como `sin_prueba` y no se registra nada: con
   el driver sellado, el siguiente rojo registraría como colateral al que no se probó. Se reintenta
   si algún escenario lo puede arreglar el agente (no llama desde el Cuando o el Entonces, sobrevive,
   no aserta o aserta fuera del Entonces); si ninguno puede (`sin_alterar`, `no_concluyente`), el
   rechazo es final a la primera, con exit 10. El mensaje nombra cada uno con lo que pasó, y
   ninguno pide que el test falle. El de `sin_alterar` lo lee el agente cuando otro escenario se
   reintenta, así que solo le dice que no cambie ese test: explicarle qué no se puede alterar lo
   empujaría a añadir comprobaciones que el impostor sí altere (F64 → F63). La explicación va a la
   persona, en el rechazo final o en el último intento, junto a cómo registrarlos con
   `lictor register`. Un impostor que no se puede ejecutar no culpa al driver: deja sus escenarios
   sin probar.

6. **Lo probado es un verde colateral, sin persona.** Con todos probados, el driver se sella y se
   registran en un commit vacío encima del suyo, en el mismo `advance_head`: `Scenario:` por
   escenario, `Lictor-Run`, `Lictor-Collateral` con el commit del driver y `Lictor-Proof:
   interfaz`, con el asunto «ya se cumplía». `scenario.green` lleva `collateral: true` y
   `proof: "interface"`; `RunProgress`, `status` y `report.md` lo cuentan aparte, como «ya se
   cumplía». La prueba es más fuerte que la de un colateral con el driver sellado, que solo falló
   una vez contra un stub. Una reanudación tras una muerte entre el registro y el checkpoint la
   acepta el ancla como hoy (el árbol es el del driver) y repite el rojo, que los da por verdes y
   solo tienen que pasar.

7. **Los impostores de stubs dicen la verdad.** Si no hay ninguna línea que sustituir, no se
   ejecutan y `driver.sealed` lo dice (`stub_impostors: 0`), en lugar de darlos por superados.
   Exigirles una aserción del driver, como al de la interfaz, cerraría el falso verde que la
   ADR-0004 admite (un test sin assert que lee `.total` de `None`), pero rechazaría drivers que hoy
   pasan: va en su propio cambio, medido aparte. Es la ADR-0012.

8. **El perfil lo declara todo.** `python-uv` añade `oracle.interface_impostor` (variable
   `LICTOR_IMPOSTOR`, modo de control y modos), el plugin `python_uv/lictor_impostor.py` en
   `verification.files` y en `plugins`, y `LICTOR_*` a `reserved`. El núcleo solo pasa a cada
   ejecución la variable con el modo y las rutas, y aplica la regla. `lictor.json` no puede
   declararlo, como el resto de `oracle`.

## Consecuencias
- F58 deja de costar llamadas y registros a mano cuando lo que devuelve la interfaz es un número,
  un texto, una colección, un booleano o una excepción. Con objetos, estado o subprocesos sigue
  yendo a la persona, como hoy.
- Cuesta tres ejecuciones más de la aceptación, solo cuando hay escenarios nuevos que pasan, en
  cada intento del driver.
- **El falso verde nuevo:** un driver que, en lugar de leer el valor del Gherkin, ejecuta el código
  y escribe en el test lo que devuelve. Si ese valor está mal, el test pasa la prueba y el escenario
  queda en verde sin que nadie lo haya implementado; hoy lo pararía la persona al registrar. Lictor
  parte de que el agente se equivoca, pero no hace lo contrario de lo que se le pide: el prompt del
  driver ya pide los valores del Gherkin, y ahora dice también que nunca de ejecutar el código.
  Queda como `@known_limitation`, y `false_greens` lo caza en las pruebas. Ninguna regla
  determinista distingue «compara con el valor del escenario» de «compara con otro valor»: eso lo
  cubriría un juez semántico del driver, una fase propia del roadmap.
- Un test que solo exige la clase de una excepción queda probado aunque el Gherkin pida su mensaje.
- El código bajo test podría detectar el modo impostor y portarse distinto: es la misma clase de
  ataque que `c6`, que solo cierra el sandbox.
- AGENTS.md dejaba dicho que «el rojo no registra nada antes del sello»: ahora registra, después del
  sello y en el mismo avance, lo que el impostor probó.

## Enmienda B (0.10.0, 2026-11-20)

La pasada de la biblioteca con la 0.8.0 (`.audits/qa/biblioteca/analisis.md`) y su medida con la
0.9.0 (`paso-4-a.md`) mostraron tres defectos de la regla del punto 4:

| Defecto | Medido en la tanda 4 (41 escenarios) | Qué cambia |
|:--|:--|:--|
| Una aserción de un Dado probaba el escenario (F63): `assert biblioteca.reservar(...) is None` fallaba con el centinela | 13 «probados» sin mirar su Entonces; en dos, el Entonces es una fecha que el impostor no altera | Solo prueba un Entonces, o el `ImpostorError` desde el Cuando. Los Dados no se alteran |
| `sin_alterar` casi nunca salía: el `None` de un Dado contaba como alterado | 0 `sin_alterar`; PRE-004 sobrevivía con un mensaje falso y se reintentaba en balde | Se cuenta desde el Cuando, y un `None` de una orden solo no cuenta |
| El mensaje empujaba a comprobar tipos en los Dados (F64) | El origen de los 13 de F63 | Ningún mensaje lo pide, y el de `sin_alterar` no explica nada al agente |

Con la enmienda, sobre los mismos drivers: 31 probados, 7 `sin_alterar` y 3 sin probar (PRE-005,
CON-003 y PRE-007, que esperan a alterar objetos y el principio de una lista, F62). Los 11 que se
cazaban en un Dado y siguen probados lo están por su Entonces o por el `ImpostorError` del Cuando.

Lo revisó Fable antes de implementarla. Sus correcciones: un fixture que pide un Cuando se resuelve
dentro del paso, así que el plugin lo marca aparte (`pytest_fixture_setup`) y no lo altera; el
`None` de una orden en el Cuando escondía `sin_alterar` como antes el de un Dado; y «no llama»
incluye llamar solo desde un Dado, que es otro arreglo que el de un objeto que no se altera.

Las instrucciones del driver del perfil dicen ahora que los Dados solo preparan, que el resultado lo
obtienen el Cuando o el Entonces y que la comprobación va en un Entonces, también la de un error.

**Lo que no cambia:** el falso verde de las consecuencias (un test que copia el valor del código)
sigue igual, y lo que se mide de C (impostores de stubs) sigue sin aplicarse.

## Enmienda de las colecciones (0.11.0, 2026-11-20)

Medir cómo reaccionaba el modelo real a los mensajes de la 0.10.0 (`.audits/qa/biblioteca/reaccion.md`)
mostró que dos rechazos reintentables caían sobre tests correctos de listas de objetos, y que el
agente no los podía arreglar: PRE-005 en 0 de 6 llamadas y PRE-007 en 1 de 6. El diseño y sus
variantes están en `diseno-colecciones.md`.

| Defecto | Test correcto | Qué cambia |
|:--|:--|:--|
| En `mas`, el centinela entraba en la lista y el test fallaba por un error, no por su aserción (`no_aserta`) | `[p.isbn for p in prestamos_de(s)] == esperados` | El elemento nuevo es de la forma de los demás: el último alterado si es de un tipo que se sabe alterar, y si no, el propio último. En un diccionario, el valor de la clave nueva |
| Se cambiaba el final, y el primero quedaba igual (`sobrevive`) | `prestamos_de(s)[0].isbn == isbn` | Una lista o tupla cambia por el principio: `mas` antepone el elemento nuevo y `menos` quita el primero |
| El mensaje de `no_aserta` solo nombraba el fichero, y el agente lo tomó por la causa en las 6 llamadas | — | Dice la excepción, el paso, la línea (`lictor.line`, nuevo en `lictor_outcome`) y el modo, y que el valor lo pone Lictor y el test no debe tratarlo aparte |

En los 187 casos de la biblioteca (41 escenarios), los probados pasan de 136 a 162, los `no_aserta`
de 13 a 1 y los que sobreviven de 14 a 0; ninguno empeora. Una tabla de 23 tests correctos y
débiles, en `test_lictor_impostor.py`, fija lo que se prueba de cada forma de comprobar una lista.

**Límites conocidos**, que siguen rechazados:

- `x[-1] == v`: `menos` quita el primero y el último no cambia. Con números o textos, la 0.10.0
  lo probaba.
- El primero de una lista de un objeto: el elemento nuevo es una copia de ese objeto.
- El primero de una lista de un número o un texto: con uno de menos, el índice falla antes que la
  aserción. Desde la 0.21.0, si esa aserción compara un valor del escenario, la ADR-0016 lo prueba
  por su sentencia (enmienda del Entonces que no puede leer lo alterado).
- Acceso por clave en un diccionario, `in` y `max`.
- Un texto solo cambia por el final: `x.splitlines()[0] == v` y `x.startswith(v)` sobreviven. Desde
  la 0.21.0, `menos` quita el último carácter y, si era un blanco, también el último que no lo es:
  quitar solo un `\n` final no cambiaba lo que lee un test que parsea o limpia el texto (F85).

Lo revisó Fable antes de implementarla. Descartó dos variantes que se habían medido:

- **Un centinela que absorbe** atributos, índices y llamadas: se itera sin fin, y da por probados
  un bucle sobre una lista vacía y un test condicional sobre un `None`.
- **Un trato aparte para la lista de uno:** da por probado un `isinstance` y pierde `len(x) == 1`.

También pidió alterar el elemento nuevo en vez de copiarlo, porque con una copia `set(x) == {1, 2}`
dejaba de probarse. Y pidió que el centinela tuviera un nombre que se explique solo en un error:
`AlteredByLictor`.

**Lo que no cambia:** las reglas del dominio (qué prueba un test y qué es final), los números, los
textos y los booleanos, y el `None`, que sigue cambiándose por el centinela.

## Enmienda de las fechas (ADR-0012, 2026-11-20)

El impostor altera un `date` o un `datetime`, un día más o menos. Sale de la ADR-0012: el test de
una orden llamada desde un Dado (MUL-006, F64) se juzga con este impostor, y su Entonces compara una
fecha, que antes daba `sin_alterar`. Con la fecha alterada sale probado. Los escenarios que ya se
cumplían y cuyo Entonces solo compara una fecha (PRE-004 de la biblioteca) también. El resto de
F62 (objetos) sigue abierto.

Para la ADR-0012, la variable lleva también `stubs`: las líneas de los stubs que Lictor sustituyó.
El plugin no envuelve esas funciones, porque lo que devuelven es una constante de Lictor y no
código que ya exista. Sin stubs, la lista va vacía.

## Enmienda de las corrutinas y las librerías (0.17.0, 2026-11-24)

Sale de F69 y F74 (fase 4a, `pyverifactu`). La API de `pyverifactu` es `async` y su CLI se prueba con
`typer.testing.CliRunner`. El impostor solo envolvía funciones planas, y solo contaba como del
driver la llamada cuyo marco era del driver, así que los dos casos salían `no_llama`: el rechazo era
falso, se reintentaba en balde y acababa en `lictor register`.

Medido sobre las pasadas (`.audits/qa/f69-f74/`): cada escenario que pasa se juzgó como si fuera
nuevo, con la aceptación en control, `mas` y `menos` y la regla de este ADR.

| Pasadas | Escenarios | Antes | Ahora |
|:--|--:|:--|:--|
| pyverifactu | 51 | 7 probados, 6 `sin_alterar`, 38 `no_llama` (falsos los 38) | 22 probados, 21 `sin_alterar`, 4 `a_traves`, 4 reintentables por límites conocidos |
| biblioteca, calculadora, romanos, dni, demo-iva | 254 | — | ningún veredicto ni detalle cambia |

Los 15 escenarios que pasan a probado fallan, en los dos modos, por una aserción de un Entonces con el valor
del escenario. Ninguna pasada tiene generadores.

**Decisión:**

1. **Se envuelven las funciones `async def`** (de módulo, métodos, estáticos y de clase). El
   llamador es el marco que crea la corrutina, no el bucle que la avanza; el paso, el que está en
   curso al crearla. Cuenta como llamada al esperarla: una corrutina que nunca se espera no le
   devolvió nada al driver. Al terminar el `await` se altera su resultado con el mismo `alter`, y
   su excepción como la de una función. El envoltorio sigue siendo una función de corrutina
   (`inspect.markcoroutinefunction` desde 3.12; antes, solo para `asyncio.iscoroutinefunction`).
   Los generadores siguen sin envolverse.
2. **Lo que el driver devuelve a la interfaz vuelve a ser lo real.** Un valor alterado que el
   driver pasa como argumento a otra llamada de la interfaz se cambia por el que dio la interfaz:
   la alteración es para que la vea el driver. Se reconoce por identidad, y solo si es inmutable y
   Python no lo comparte (un número fuera de los enteros pequeños, una fecha, un texto, bytes o una
   tupla de más de un elemento): una lista que el driver cambió es suya, y devolver la original
   perdería el cambio.
3. **Lo que llega a la interfaz por una librería se cuenta, y nunca se altera.** Subiendo por la
   pila hasta el primer marco del driver, sin pasar por uno del impostor (dentro de una llamada ya
   contada), la llamada es de una librería si el marco justo debajo del driver no es del proyecto.
   Lo que Python genera para un módulo del proyecto (el `__init__` de una dataclass) cuenta como del
   proyecto, y una corrutina que creó el proyecto sigue la pila de quien la creó, no la del bucle.
   Hay dos casos:
   - `via`: llama el propio proyecto, después de que el driver entrara en él por la librería (typer
     guarda la función de la orden antes de que el impostor la envuelva, y click la llama).
   - `handed`: llama la librería, a una función que el driver le pasó (`pytest.raises(E, f, x)`,
     `anyio.run(f)`).

   El plugin nombra, por paso, la primera librería desde el driver que no sea de la biblioteca
   estándar (`lictor.interface.entry.<paso>`). No nombra ninguna herramienta: el nombre sale de la
   pila.
4. **La regla.** Si un test no llamó a la interfaz desde el Cuando o el Entonces:

   | Lo que pasó desde el Cuando | Desenlace | Final |
   |:--|:--|:--|
   | Una librería llamó a lo que el test le entregó | `no_llama`, detalle `entrega`, con la librería: «llámala desde el paso» | no |
   | Solo llegó por una librería que entró en el proyecto | `a_traves` (nuevo), con la librería | sí |
   | Solo llamó desde un Dado | `no_llama`, `dado` | no |
   | Nada | `no_llama` | no |

   `a_traves` es final como `sin_alterar`: el agente solo lee que no cambie ese test, y la persona
   lee por qué librería pasó (`through_note`), con `lictor register`. Con un driver en que ningún
   escenario se puede arreglar, el rechazo es final al primer intento.
5. **En los stubs (ADR-0012)**, `a_traves` se rechaza con el mensaje de `sin_codigo`: la orden pasa
   aunque los stubs no estén implementados. Solo `sin_alterar` se acepta sin comprobar.
6. `lictor.driver.measured.v1` guarda, por escenario, `via`, `handed` y `libraries` del control, y
   «Medidas del driver» de `report.md` tiene una columna con lo que llegó por una librería.

**Lo revisó Fable antes de implementarlo**, con 20 casos ejecutables. Cada hallazgo se reprodujo ejecutándolo,
y por ellos cambiaron cosas:

- Se descartaron dos reglas que convertían un `sobrevive` en final. Una cuando lo alterado se había
  devuelto a la interfaz; otra cuando el Entonces leía además un objeto. Escondían tests débiles.
- Lo devuelto solo es inmutable.
- Una corrutina cuenta al esperarla.
- `handed` se separó de `a_traves`.
- La dataclass y las tareas que crea el proyecto ya no cuentan como librerías.
- En los stubs, `a_traves` se rechaza.

**Límites conocidos:**

- ORD-003 (F74) sigue sin probarse: lee un objeto (F62) y un diccionario por clave. Sale `sobrevive`,
  reintentable. Alterar los valores de un diccionario movería los veredictos de las colecciones y va
  aparte.
- Los hilos (`asyncio.to_thread`, un `TestClient` que corre la orden en otro hilo), los objetos con
  `__call__` y las funciones con `lru_cache` no se envuelven, y siguen en `no_llama`.
- Un entero pequeño o un texto de un carácter que el driver devuelve no se reconoce: siguen
  alterados.
- Antes de 3.12, `inspect.iscoroutinefunction` dice que el envoltorio no es una corrutina.
- El texto o los bytes que el test parsea (un XML con un byte de más) fallan antes que la aserción:
  `no_aserta`.

**Lo que no cambia:** las funciones planas y lo que se altera de cada tipo, los desenlaces de la
regla B y las colecciones, y lo que se dice al agente de cada uno.

## Enmienda de los objetos de valor (0.18.0, 2026-11-25)

Sale de F62. Un Entonces que compara un objeto (una dataclass, un modelo de pydantic, un miembro de
un `Enum`) salía `sin_alterar`: el impostor no sabía darle otro valor, y la persona tenía que
registrar a mano un test que sí comparaba. Va con la ADR-0016, que pesa además el valor del
escenario: sola, esta enmienda empeoraba un caso real (ALM-001 pasaba de `sin_alterar`, final, a
`sobrevive`, con reintento).

Medido sobre los 317 escenarios que pasan en las pasadas (`.audits/qa/objetos/diseno.md`), cada
uno juzgado como si fuera nuevo:

| Regla | Probados | Lo demás |
|:--|--:|:--|
| La 0.17.0 | 284 | 23 `sin_alterar`, 4 `no_aserta`, 4 `a_traves`, 2 `sobrevive` |
| Con los objetos de valor | 307 | 4 `no_aserta`, 4 `a_traves`, 2 `sobrevive` |

Los 23 nuevos (21 de `pyverifactu`) fallan, en los dos modos, por una aserción de un Entonces con
el valor del escenario. ORD-003 (F74), que leía un objeto, queda probado.

**Decisión:**

1. **Se altera un objeto de valor**: una dataclass, un `NamedTuple`, un modelo de pydantic v2, una
   clase de attrs, y un miembro de un `Enum` que no sea `Flag`. Del objeto se alteran **todos los
   campos a la vez**, cada uno con el mismo `alter`, hasta 4 niveles de profundidad. Se construye
   una copia de la misma clase con `copy.copy` y `object.__setattr__` (`_replace` en una tupla con
   nombre), sin ejecutar `__init__`, `__post_init__` ni validadores. La copia es de la misma clase,
   así que `isinstance` y la igualdad de la clase siguen funcionando, y no es igual ni al objeto
   real ni a uno construido con los valores del escenario. De un `Enum` se da el miembro siguiente
   en `mas` y el anterior en `menos`. Un enum de un solo miembro no se altera, y un `IntFlag` se
   altera como el entero que es.
2. **Lo que no es de valor se deja como está**: una clase cualquiera (un servicio, un almacén),
   `__slots__` sin campos declarados, o un `Flag` combinado. Sus atributos son estado, o no tienen
   otro miembro que dar.
3. **Lo que vuelve a la interfaz vuelve real.** Vuelven por identidad:
   - un objeto congelado;
   - cada campo alterado que ya podía volver;
   - **un objeto mutable que el driver no ha tocado**, es decir, cuyos campos son los mismos
     objetos que cuando se alteró.

   Uno que el driver cambió es suyo. Así, un método llamado sobre la copia corre sobre lo real, y
   su resultado se altera una vez, no dos.
4. **Una excepción provocada no prueba nada** (`provocada`, final). El impostor numera las
   llamadas del driver en cada test. En el control dice cuáles lanzaron, y en cada modo cuáles tuvo
   que tragar o sustituir (`lictor.interface.raised_at` y `.swallowed_at`, esta con el paso). Si un
   modo tragó o sustituyó la excepción de una llamada que en el control no lanzó, la causó un valor
   alterado que volvió a la interfaz: un entero pequeño, que Python comparte y no se reconoce por
   identidad. Ese modo no dice nada del test. Un `probado`, un `no_aserta` o un `sobrevive` con una
   excepción así es `provocada`. Al agente se le dice que no cambie el test, como con `sin_alterar`.
   La persona lee en qué paso y en qué modo pasó (`provoked_note`).
5. **La traza** (para la ADR-0016). Con `trace`, el impostor anota cada llamada del driver con su
   paso, la función y un hash de cada argumento. Un objeto de valor se resume por sus campos, y el
   texto, sin la raíz del clon. Solo lo pide el control.
6. **En los stubs (ADR-0012)**, `provocada` se rechaza con su propio mensaje: el valor alterado
   volvió a la interfaz, y el test se puede escribir de modo que se pueda juzgar.

**Lo revisó Fable antes de implementarlo**, con 48 casos ejecutables (`.audits/qa/objetos/fable/`).
Cada hallazgo se reprodujo ejecutándolo. Por ellos, lo mutable no tocado vuelve real (antes, un
débil `facturar(p) is not None` salía probado por la excepción que tragaba el impostor), la
excepción provocada es su propio desenlace y `Flag` queda fuera de los enums.

**Límites conocidos:**

- `copy.copy` ejecuta `__copy__` o `__getstate__` si la clase los define.
- `attrs.frozen` no vuelve a lo real. En 3.9, una dataclass congelada con `__slots__` escritos a
  mano no se altera.
- `cached_property`, los campos que no se pueden alterar (un `UUID`) y los de `compare=False` dan
  `sin_alterar`, `sobrevive` o, con la ADR-0016, `solo_el_valor`.
- `[0]` de una lista de un elemento dentro de un objeto sigue siendo el límite de las colecciones.

**Lo que no cambia:** lo que se altera de los demás tipos, la regla B, las colecciones y las
corrutinas. Las tablas `_CHECKS` de `test_lictor_impostor.py` y las de `test_lictor_doubles.py` dan
lo mismo.
