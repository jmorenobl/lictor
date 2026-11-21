# ADR-0012: Los impostores de stubs exigen una aserción de un Entonces, con dobles

## Estado
Aceptada (2026-11-20). Sale del punto 7 de la ADR-0011 («exigirles una aserción del driver … va en
su propio cambio, medido aparte») y de F64. Se midió antes de diseñarla, fuera de Lictor, sobre los
drivers de todas las pasadas y dos tablas sintéticas. La revisó por su cuenta un revisor
independiente, que ejecutó las medidas y un proyecto mínimo con pytest-bdd, y esta versión recoge
sus hallazgos. Trae una enmienda pequeña de la ADR-0011: el impostor de la interfaz altera fechas.
Enmendada el 2026-11-21 por F68: un stub en `__init__` devuelve None con cada constante.

## Contexto
Con un driver nuevo, cada test pendiente se ejecuta una vez por constante en lugar de los stubs
(`return None`, `return True`). Hasta esta ADR, el driver se rechazaba solo si un test **pasaba
con todas** (ADR-0004): bastaba con que fallara de cualquier forma. Eso deja dos defectos:

1. **Un falso verde admitido:** un test sin `assert` que lee `.vence` de lo que devuelve un stub
   falla con `None` y con `True` por `AttributeError`, y se acepta.
2. **Un rechazo en falso con un mensaje que empuja a lo malo (F64):** el test correcto de MUL-006,
   cuyo único stub es una orden llamada desde un Dado, pasa con las dos constantes. El mensaje
   («ignoran el valor que devuelve la interfaz») llevó al agente a poner `assert … is None` en
   cada Dado, que es de donde vino F63.

Exigir lo mismo que al impostor de la interfaz (que falle **una aserción del driver en un
Entonces**) cierra el primero. Pero solo con `None` y `True` rechaza 8 de 46 tests correctos de la
biblioteca y 29 de 43 formas correctas de la tabla sintética: el test no llega a su aserción.

## Decisión

1. **Dos dobles se añaden a las constantes,** `ONE` y `EMPTY`. Van en un módulo del perfil
   (`python_uv/lictor_doubles.py`, en `verification.files`, que no importa nada de Lictor), y un
   stub pasa a `return __import__("lictor_doubles").ONE`.

   | Doble | Verdadero | `len` | Al iterarlo | Al ordenarlo (`<`, `>`…) |
   |:--|:--|--:|:--|:--|
   | `ONE` | sí | 1 | un elemento | sí a todo |
   | `EMPTY` | no | 0 | nada | no a todo |

   Lo que tienen los dos:

   - **Lo que se hace con ellos da otro doble:** un atributo, un índice, una llamada o una
     operación aritmética (también `round`, `abs` y el signo) dan otro doble de la misma clase, un
     nivel más hondo. Cada nombre y cada clave dan su propio hijo.
   - **No son iguales a nada más que a sí mismos.** `float()` da `nan`, `int()` un entero que
     ningún escenario espera, y al formatearlos dan su `repr`.
   - **Se copian y se serializan como sí mismos** (`copy`, `deepcopy`, pickle).
   - **Son finitos:** pasado el nivel 8, o tras 10 000 operaciones en un mismo test, lanzan su
     propio error. Así no se cuelgan ni una cadena de atributos ni un bucle que no profundiza
     (`while r.pendiente:`).
   - **No tienen nombres especiales** (`__dataclass_fields__`…), que pytest pregunta al explicar
     un fallo.
   - **Su `repr` y su clase se explican solos en un error:** `<doble de Lictor>` y
     `ReturnedByLictor`.

   Cada constante del perfil lleva un nombre para los mensajes y las medidas: «None», «True», «un
   doble de Lictor» y «un doble vacío de Lictor».

2. **La regla C (dominio, `stub_proof`), por test pendiente nuevo:**

   | Con las constantes | Qué pasa |
   |:--|:--|
   | Pasa con `None` y con `True` | A la vía de la interfaz (punto 3). Los dobles no bastan: los cazaría también `r in (None, True)` |
   | Si no: alguna hace fallar una aserción del driver en un Entonces | Aceptado |
   | Si no: pasa con `None` | A la vía de la interfaz |
   | Si no | Rechazado, reintentable (punto 5) |

3. **La vía de la interfaz, para un test que no depende de lo que devuelven los stubs.** Es lo que
   pasa con una orden cuyo efecto solo verá otra regla. Se ejecuta el impostor de la interfaz
   (ADR-0011 con su enmienda B) sobre el árbol con los stubs en `return None`, y Lictor le dice
   qué líneas son stubs: **las funciones que son stubs no se envuelven**. Lo que devuelve un stub
   no es código que ya exista, así que alterarlo no prueba nada. Si se alterara, un
   `r in (None, True)` saldría probado. No se fía de la anotación `-> None`.

   | Impostor de la interfaz | Qué pasa |
   |:--|:--|
   | `probado` | Aceptado (MUL-006, por su fecha) |
   | `no_llama` | Rechazo reintentable. Aquí quiere decir que lo que comprueba no viene de código que ya exista (un test que solo mira lo que devuelve un stub), y el mensaje lo dice así: «no llama a la interfaz» negaría un Cuando que llama a un stub |
   | `sobrevive`, `no_aserta`, `fuera_del_entonces` | Rechazo reintentable, con el mensaje del impostor de la interfaz |
   | `sin_alterar`: llamó a código que ya existe, pero nada alterable | Aceptado y anotado para la persona en `driver.sealed` (`stub_unchecked`) y en `report.md`. El agente no lee nada |
   | `no_concluyente` | Rechazo reintentable, con lo que no se pudo ejecutar |

4. **El impostor de la interfaz altera fechas** (enmienda de la ADR-0011, parte de F62): un `date`
   o un `datetime`, un día más o menos. Sin esto, MUL-006 sale `sin_alterar`. Con esto sale
   probado, y PRE-004 y los demás que ya se cumplían y solo comparan una fecha dejan de ir a la
   persona.

5. **Todo rechazo de C es reintentable,** y cada mensaje nombra el test. Ninguno pide
   comprobaciones en los Dados (F64 → F63) ni dice «no comprueba» de un test que sí compara:

   | Desenlace | Cuándo | Mensaje (resumido) |
   |:--|:--|:--|
   | `fuera_del_entonces` | Alguna constante hace fallar una aserción del driver, nunca en un Entonces | «…cuando los stubs devuelven un valor en lugar de lanzar NotImplementedError, falla por una aserción de un {paso real} (f.py:12, devolviendo True: …), no de un Entonces. La comprobación va en un paso Entonces, con el valor que da su escenario. Si el escenario espera un error, guárdalo en el Cuando y compáralo en el Entonces.» |
   | `sobrevive` | Pasa con alguna constante, nunca falla su aserción | «…pasa también cuando los stubs devuelven otro valor en lugar de lanzar NotImplementedError, y con ningún valor falla una aserción de un Entonces: compara con assert el resultado con el valor que da su escenario y, si el escenario dice que algo no está, comprueba también lo que sí está. Ese otro valor lo pone Lictor para comprobar el test: no lo trates aparte ni compruebes su tipo.» No nombra el valor: nombrando «un doble de Lictor», el modelo real añadió un `isinstance` a cada Entonces |
   | `no_aserta` | Falla con todas, nunca por una aserción del driver | «…falla, pero con TypeError de un Entonces, en f.py:189 (devolviendo un doble de Lictor: …), no con una aserción. Compara con assert el valor que devuelve la interfaz con el del escenario, tal cual, sin convertirlo. Esos valores los pone Lictor para comprobar el test: no los trates aparte.» Si el test espera un error: «con esos valores la interfaz no lanza nada; el Entonces tiene que fallar por su assert también entonces». |
   | Vía de la interfaz | Pasa con `None` | El del impostor de la interfaz, con la cabeza «pasa aunque los stubs no estén implementados». |

   Del fallo se nombra antes el de un doble que el de `None` o `True`: el de un doble dice qué
   forma del test no llega a la aserción. Todos se miden con el modelo real antes de la PR.

6. **Una ejecución que se cae no cuenta.** Su constante no vale para ningún test, y se juzga con
   las que quedan. Si se cae la de `None`, no hay vía de la interfaz. Si se caen todas, el driver
   se rechaza como hoy (`impostor`), reintentable. Un stub que sigue lanzando
   `NotImplementedError` también se rechaza como hoy.

7. **Cuando se agotan los intentos,** el último mensaje es para la persona: nombra cada test y su
   desenlace, y añade las formas conocidas que no llegan a la aserción con ninguna constante
   (convertir el resultado a otro tipo, `x not in r`, desempaquetar), para que el test se reescriba
   a mano.

8. **Lo que se mide.** `rule_c` de `lictor.driver.measured.v1` pasa a ser el veredicto. Incluye
   los dobles y, por test, el desenlace de la vía de la interfaz.

9. **El perfil lo declara.** `oracle.impostors.replacements` pasa a una lista de
   `{code, name, double}` (la primera no puede ser un doble: es la que queda en la vía), y
   `interface_impostor` recibe `stubs` (fichero → líneas). Un perfil sin `interface_impostor` no
   tiene vía de la interfaz: lo que iría a ella se rechaza, reintentable. Hoy solo existe
   `python-uv`, que sí la tiene; un perfil nuevo sin ella repetiría F64.

## Consecuencias

Se mantienen las tres metas: 0 rechazos en falso en el corpus real, 0 falsos verdes y ningún test
débil que hoy se caza pasa a aceptarse.

- **Corpus** (37 drivers reales, 347 tests pendientes): se aceptan los 347, 345 por su Entonces y
  los dos MUL-006 por la vía de la interfaz. 0 rechazos en falso, frente a 1 hoy y 9 con C sin
  dobles. Ninguno queda «sin comprobar».
- **Tablas sintéticas** (89 formas correctas y 33 débiles, fijadas en `test_lictor_doubles.py`):
  - Se rechazan 10 formas correctas. Todas pasan cuando el resultado está vacío o lo convierten a
    un tipo de la librería estándar: `x not in r`, desempaquetar dos, recorrer una lista enlazada
    hasta el final, `Decimal()`, `asdict()`, `json.dumps`, `re.search`, `date.fromisoformat`.
    Todas tienen una forma que se acepta.
  - Ningún débil que hoy se caza pasa a aceptarse. Los 13 débiles que siguen aceptados ya se
    aceptan hoy: `is not None`, `isinstance`, `assert r` y parecidos. Ninguna constante los
    distingue de `assert r is None` sin el Gherkin: es trabajo del juez semántico (fase 2c).
- **Proyecto mínimo del revisor** (pytest-bdd, 32 correctos y 16 débiles):
  - Se rechazan 5 correctos, que hoy se aceptan: `pytest.raises` en el Cuando, el error guardado
    solo en el `except`, `asdict`, `json.dumps` y `dataclasses.replace`.
  - De los 16 débiles, 13 se rechazan. Los otros 3 se aceptan, igual que hoy.
- **Coste:** de 2 a 4 ejecuciones de la aceptación por driver con stubs. Solo si algún test va a
  la vía, 3 más.
- **Límites que siguen:**
  - Un stub en un método especial (`__str__`, `__iter__`) no tiene constante que valga. El de
    `__init__` sí, desde la enmienda de F68 (abajo).
  - El código bajo test puede ver el doble y portarse distinto, como con el centinela (`c6`).

## Revisión independiente (2026-11-20), comprobada

| Hallazgo | Comprobado | Qué cambia |
|:--|:--|:--|
| H1 · la vía sellaba tests vacíos que hoy se rechazan: el `None` del propio stub contaba como alterado | Sí, proyecto mínimo (W1, W2, W3, W7) | Los stubs no se envuelven en la vía (punto 3) |
| H2 · los dobles aceptaban `r in (None, True)` y parecidos | Sí. Con la guarda sola seguían probados por la vía (W4, W13); con los stubs fuera, `no_llama` | Guarda «pasa con None y True → vía» (punto 2) |
| H3 · 16 formas correctas con aritmética, fechas o formato sobre un atributo | Sí, en su tabla ampliada | Doble que absorbe aritmética, orden, formato, `float` e `int`: quedan las conversiones |
| H4 · los escenarios de error dependen de cómo se guarda el error | Sí (K1, K2) | La frase de los errores, en los mensajes |
| H5 · una copia del doble se rompe al usarla | Sí: `RecursionError` | `__copy__`, `__deepcopy__`, `__reduce__` |
| H6 · un bucle que no profundiza no acaba | Sí: un millón de vueltas | Tope de operaciones por test |
| H7 · stubs en métodos especiales | Razonado | Límite conocido |
| H8 · todos los hijos eran el mismo objeto | Sí | Un hijo por nombre y por clave |
| H9 · un rechazo sin arreglo agota los intentos sin salida | Razonado | Punto 7 |
| H10 · la caída de una ejecución no estaba definida | Razonado | Punto 6 |
| Alterar fechas lleva los dos MUL-006 a `probado` | Sí, con su plugin | Punto 4 |

## Enmienda F68 (2026-11-21): un stub en `__init__`

En la primera pasada de adopción (`pyverifactu`, fase 4a), el driver declaró una clase con
`__init__` a `raise NotImplementedError`, construida en un Dado o un Cuando. Con `True` o un doble
en su lugar, Python lanza `TypeError: __init__() should return None` al construir el objeto; con
`None` el objeto se construye, pero el Entonces lee un atributo de lo que devuelve un método y falla
por error. Ninguna constante llegaba al assert: los 8 tests salieron `fuera_del_entonces` y el driver
se rechazó, fuera cual fuera el test. El segundo intento renunció a las clases (funciones sobre
`object`, con `assert isinstance` en la implementación), y esa interfaz quedó sellada.

**Decisión:** cada constante salvo la primera pasa por `lictor_doubles.stub(nombre)`, que mira desde
qué función se la llama y devuelve None si es un `__init__`, el único valor que Python admite ahí.
El objeto se construye sin atributos, y sus métodos, stubs también, devuelven la constante. La
primera sigue siendo `return None`. El dominio no cambia: lo que es un constructor lo sabe el
lenguaje, y la regla vive en el perfil.

**Medido:** ninguno de los drivers de las pasadas anteriores (todos los proyectos de
`~/sandbox/lictor-pasada`, todo su historial) tenía un stub en `__init__`, así que ningún veredicto
de antes cambia. `test_lictor_doubles.py` aplica las constantes del perfil a una clase con stubs y
`test_v0_14_1_init_stub.py` reproduce el driver de la pasada: sin la enmienda se rechaza dos veces;
con ella se sella al primer intento.

**Límite:** los otros métodos especiales siguen sin constante que valga.

