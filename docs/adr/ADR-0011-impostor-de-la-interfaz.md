# ADR-0011: Un escenario nuevo que el código ya cumple se prueba con un impostor de la interfaz

## Estado
Aceptada (2026-11-19). Sale de F58 (`.audits/romanos/fricciones.md`) y de la fase 2 del roadmap. Un
revisor independiente diseñó la solución por su cuenta, sin ver la primera propuesta, y esta
versión adopta su diseño (punto 2) con una corrección (el gancho de importación, punto 3).

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
   limpio:

   | Ejecución | Qué hace |
   |:--|:--|
   | Control | Envuelve y cuenta, sin alterar nada. Tiene que pasar como el rojo. |
   | Modo `mas` | Número + 1, `bool` al contrario, texto o colección con un elemento de más, `None` por un centinela; una excepción se traga y devuelve `None`. |
   | Modo `menos` | Número − 1, `bool` al contrario, texto o colección con uno de menos (o uno nuevo si estaba vacía), `None` por un centinela; una excepción se cambia por `ImpostorError`. |

   Cualquier otro objeto no se altera. Una alteración que deja el valor igual al real (un `inf`, un
   número enorme) no cuenta como alteración.

3. **El plugin envuelve al importar.** Un driver que hace `from paquete import evaluar` se queda con
   la función original si se envuelve después de importar, así que el plugin instala un buscador
   de módulos al cargarse y envuelve cada módulo de `paths.writable` al ejecutarlo. No envuelve
   clases, métodos especiales (`__init__`, `__eq__`…), generadores ni corrutinas. Sin su variable
   no hace nada, así que va en los `plugins` de siempre.

4. **Un escenario queda probado solo si:**
   - pasa con el código real y en la ejecución de control;
   - en la de control, su test llamó a la interfaz al menos una vez;
   - en cada modo se alteró al menos una llamada de su test;
   - en **cada** modo falla con una aserción cuyo último marco dentro del proyecto está en
     `paths.driver` (`pytest.fail` y el «DID NOT RAISE» de `pytest.raises` incluidos), o con el
     `ImpostorError` del modo `menos`;
   - las tres ejecuciones son de fiar (`trust`) y mapean igual que el rojo.

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

5. **Lo que no se prueba sigue el camino de hoy, con un mensaje cierto.** Si alguno de los
   escenarios no queda probado, el driver se rechaza como `sin_prueba`, reintentable, y no se
   registra nada: con el driver sellado, el siguiente rojo registraría como colateral al que no se
   probó. El mensaje nombra cada uno con lo que pasó («no llama a la interfaz», «pasa aunque la
   interfaz devuelva otro valor», «falla, pero no con una aserción del test», «no altera nada de lo
   que devuelve», «no se pudo comprobar»). Ninguno pide ya que el test falle. En el último intento
   añade cómo registrarlos con `lictor register`. Un impostor que no se puede ejecutar no culpa al
   driver: deja sus escenarios sin probar.

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
   pasan: va en su propio cambio, medido aparte.

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
