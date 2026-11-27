# ADR-0010: Un paso de refactor tras cada escenario

## Estado
Aceptada (2026-11-18). Sale del roadmap tras la 0.4.0 (fase 2) y de la pasada de referencia de la
fase 1. Un revisor independiente comprobó el borrador contra el código, y esta versión recoge sus
hallazgos (punto 12). Enmendada el 2026-11-22 por F71 y F72, y el 2026-11-26 por F84 (al final).

## Contexto
Lictor lleva cada escenario a verde con la llamada más corta que lo consigue. El agente implementa
para pasar un escenario, no para dejar un diseño, y nada le pide volver sobre lo que ya escribió.
En la calculadora de la pasada de la fase 1, tras 42 escenarios, el código lo muestra (lizard y
jscpd sobre `src`):

| Medida | Al final de la fase 1 |
|:--|--:|
| Complejidad ciclomática máxima | 20 |
| Funciones con CC > 10 | 2 |
| Función más larga | 45 NLOC |
| Duplicación | 9,6 % |
| Módulos | 1 (todo en `__init__.py`) |

Los controles no lo ven porque comprueban otra cosa: lint, tipos, tests y cobertura pasan.

**Refactorizar al final del run no sirve.** El agente de cada escenario construye sobre lo que dejó
el anterior, así que la deuda crece con cada uno. Y un refactor grande al final es el cambio más
arriesgado y el más difícil de verificar de todo el run.

Esta ADR es el «refactor» del ciclo rojo-verde-refactor: después de cada verde, con el mismo
oráculo.

## Decisión

1. **Tras el commit de un escenario implementado, un paso `refactor` mejora el código sin cambiar
   su comportamiento.**
   - Es una llamada aparte, con contexto limpio: una copia nueva de HEAD, como cualquier intento.
   - Solo hay refactor después de un escenario que implementó el agente. Un colateral, un registro o
     un re-registro no cambian código, así que no tienen nada que mejorar.
   - Si el escenario arrastró colaterales en su commit, hay un único refactor, después de toda la
     cadena. Un colateral que aparezca al verificar el refactor (punto 9) no abre otro.

2. **Se verifica igual que un intento, pero sin escenario objetivo.**
   - Pasan las puertas L0 y todos los controles `attempt`, con su `min_score`. `L0-sello` también:
     el driver y `features/` siguen sellados.
   - Todos los verdes, el escenario recién implementado incluido, tienen que seguir en verde. Los
     pendientes pueden seguir fallando.
   - `VerifyAttempt` gana el rol `refactor` en lugar de duplicar la verificación: su candidato es el
     commit del punto 3 y la aceptación no exige un objetivo (`acceptance_failure` sin escenario).
   - **No baja lo que mide el oráculo** (`refactor_regressions`, dominio). Ningún control JUnit
     puede tener menos casos en verde, ni ningún control con puntuación dejar más sin probar, que en
     la verificación del verde. Lo que deja sin probar se cuenta, no se da en porcentaje: líneas y
     ramas sin cubrir, mutantes probados sin matar (`score_misses`). Enmienda de la 0.7.1: la 0.7.0
     comparaba el porcentaje, y quitar código cubierto lo baja aunque deje sin probar lo mismo; en
     la segunda pasada de la fase 2 descartó así tres refactors por una décima (F61). Sin esto, un refactor podría borrar los unitarios: en
     `python-uv` la cobertura es `measure` y sin mínimo, y `unit` pasa vacío. La referencia se
     guarda en el estado al confirmar el verde (`refactor_baseline`). Un fallo de esta guarda es
     `L0-regresión`.
   - Un refactor no alimenta `failures` ni `rejected_paths`: un fallo en `L0-rutas` lo descarta y
     nunca para el run pidiendo una persona.

3. **Si pasa, entra como un commit propio. Si no, se descarta y el verde se queda.**
   - El commit es `refactor(<ID>): mejorar el código sin cambiar el comportamiento`, un asunto fijo
     que escribe Lictor, con `Lictor-Run` y `Lictor-Refactor-Of: <sha del verde>`.
   - No lleva `Scenario:`. `SpecHistory` sigue tomando como verde el commit del escenario y no se
     reabre nada.
   - Un refactor no gasta intentos del escenario y nunca lleva a cuarentena.
   - Si la llamada terminó su turno y el árbol es igual a HEAD, no es un fallo: es «nada que
     mejorar» (`unchanged`), sin verificación ni commit. Si la llamada se cortó (`max_turns` o un
     timeout) y no dejó cambios, es un refactor descartado.

4. **Qué puede tocar: `paths.writable`**, es decir, `src/` y `tests/unit/` en `python-uv`.
   Reorganizar `src` en módulos cambia la interfaz interna, y los unitarios del agente tienen que
   poder seguirla. Que no los borre lo impide la guarda del punto 2; el comportamiento lo sigue
   juzgando la aceptación. El driver y `features/` quedan fuera, sellados.

5. **Un intento, sin reintento.** Si falla, se descarta: el verde ya está a salvo y el siguiente
   refactor vuelve a mirar el mismo código. Si la pasada de la fase 2 muestra muchos descartes, se
   sube a dos sin otra ADR.

6. **Qué se le dice al agente.** Su prompt (`prompts/refactor.md`) recibe:
   - el escenario que acaba de pasar y los ficheros que cambió su commit;
   - el mandato de no cambiar el comportamiento y de no tocar los tests de aceptación;
   - las rutas que puede tocar, las prohibidas y los verdes que deben seguir en verde;
   - las medidas de `agent_run` con su mínimo, como el implementador (F55);
   - los objetivos de calidad del perfil, `refactor_hints`: funciones cortas, complejidad baja, sin
     duplicación, un módulo por responsabilidad. Viven solo en el perfil, como `driver_hints`:
     entran en `Defences` y en `config_fingerprint`, y `lictor.json` que los declare se rechaza.
     `refactor.md` no nombra herramientas.

   No hay un control de complejidad. Primero se mide si el prompt basta; si no, el siguiente paso
   es un control con puntuación y `min_score`, porque solo así `agent_measures` se lo dice al
   agente.
   Enmienda de la 0.13.0 (ADR-0013, F60): el prompt no bastó, y el control no es de puntuación ni
   de `min_score`, porque la complejidad es un máximo por función. Es un control `complexity` que
   mide sin bloquear, nombra al refactor las funciones que su verde dejó por encima del límite y le
   impide dejar más (`L0-regresión`).

7. **Configuración: `"refactor": {"enabled": true}`** en `lictor.json`, activado por defecto.
   `lictor init` no lo escribe: el valor por defecto sale del modelo. Entra en
   `config_fingerprint`, así que cambiarlo impide reanudar.

8. **Señales del agente.** Las decide `classify_agent`, como en un intento.
   - Un rate limit o un backend no disponible pausan, con `resume_to` en `refactor`.
   - Un `error` por debajo del límite (`keeps_attempt`) repite la llamada sin gastar el intento,
     como `build_driver`; el segundo seguido es `unavailable` y pausa. En un intento, en cambio, un
     `error` así verifica el árbol que dejó: el refactor no lo hace porque su intento es único.
   - `max_turns` o un timeout con respuesta del modelo verifican su árbol: decide si pasa.
   - `spec_conflict` no significa nada aquí: el árbol se verifica igual.
   - Un `state.tampered`, o una copia que no se puede leer (señal `rejected`), descarta el
     refactor sin verificar.

9. **Un pendiente que pasa tras un refactor es un verde colateral**, encadenado como en
   `VerifyAttempt`: decide el oráculo sellado, no el tipo de commit. Como es comportamiento nuevo
   dentro de un commit `refactor`, se cuenta aparte en `scenario.refactored`, en `report.md` y en la
   medición.

10. **Grafo, estado y reanudación.**
    - El camino nuevo es `commit → refactor → verify_refactor → commit_refactor → pick_next`.
      `route_commit` decide si se refactoriza y `route_refactor`, `route_verify_refactor` y
      `route_commit_refactor` lo demás: funciones puras del dominio. `route_pause` añade
      `refactor` y `commit_refactor` a sus destinos.
    - `refactor` y `verify_refactor` ejecutan código que Lictor no escribió: llevan `untrusted`,
      `IntegritySnapshot` y el lock del checkpoint. `commit_refactor` avanza con `advance_head`
      (un solo `merge --ff-only`); si la rama está bloqueada, pausa como `commit`.
    - **Estado:** el refactor reutiliza `attempt_tree` y `last_verify`, y añade `refactor_of` (el sha
      del verde) y `refactor_baseline`; los errores seguidos del backend siguen en
      `agent_failures`. Al
      reutilizar `last_verify`, la reanudación acepta el candidato del refactor como el de un
      intento, y `commit_refactor` repetido tras un crash compara HEAD con el candidato.
    - `commit_refactor` es un caso de uso propio (`CommitRefactor`): `CommitGreen` emitiría
      `scenario.green` del escenario.
    - **Matriz de kills.** Los puntos nuevos caben en clases que ya existen:

      | Punto | Clase |
      |:--|:--|
      | `refactor:antes`, `verify_refactor:antes`, `commit_refactor:antes` | repetir desde el checkpoint |
      | `refactor:durante`, `refactor:despues` | llamada al agente |
      | `verify_refactor:durante` | verificación a medias |
      | `verify_refactor:despues` | candidato sin registrar |
      | `commit_refactor:durante`, `commit_refactor:despues` | rama movida sin registrar |

      Sus «durante» se instalan aparte (la verificación y el avance de rama del refactor), y sus
      celdas comprueban también que hay exactamente un commit `refactor:`.

11. **Eventos, evidencia y lo que se muestra.**
    - `agent.started` y `agent.finished` con el rol `refactor`; `verify.finished` con
      `role: "refactor"`; uno nuevo, `lictor.scenario.refactored.v1`, con `outcome` (`applied`,
      `discarded` con `failed_at`, o `unchanged`), el commit y los colaterales.
    - Evidencia en `runs/<run>/<ID>/`: `NNN-refactor.json`, su `.prompt.md` y
      `NNN-refactor-verify/`, con `NNN` el intento que dio el verde y `-rN` si se repite.
    - `RunProgress` da una línea por refactor; `status` dice «refactorizando»; `report.md` tiene una
      columna de refactor por escenario y el coste del rol aparte.

12. **Lo que añadió la revisión.**

    | Hueco | Cómo se cubre |
    |:--|:--|
    | `report.md` deduplica por tipo, run, escenario, intento y evidencia, sin rol | El rol entra en la clave |
    | `RunProgress` y el informe suman llamadas y coste por dueño | El rol `refactor` va aparte |
    | `status` no conoce los nodos nuevos | Se añaden |
    | `false_greens` solo mira los commits con `Scenario:` | Comprueba también el árbol final |
    | Activado por defecto, cada caso e2e paga una llamada y una verificación más | `make_project` lo apaga salvo en los casos de refactor y en `@smoke` |

## Consecuencias
- **Más coste y más tiempo por escenario implementado:** una llamada y una verificación más. La
  fase 2 lo mide.
- **El historial tiene un commit `refactor:` detrás de cada verde implementado que mejoró algo.**
  `git log --grep Scenario:` sigue dando los verdes.
- **Un refactor descartado no le cuesta nada al escenario,** solo la llamada.
- **Nada protege de un refactor que empeore el código** y pase los controles. Para eso sería el
  control con puntuación del punto 6. Desde la ADR-0013, la complejidad por encima del límite sí:
  un refactor no puede dejar más que su verde.

## Cómo se comprueba
Se repite la pasada de la calculadora con las mismas entradas y se compara con la referencia de la
0.6.1, para que la diferencia sea solo del refactor. Metas: CC máxima ≤ 10 y ninguna función con
CC > 10, ninguna de más de 30 NLOC, duplicación < 3 %, un módulo por etapa, 0 falsos verdes y
cobertura ≥ 80 %.

## Enmienda F71 y F72 (2026-11-22): las guardas en código adoptado

En la primera adopción de un proyecto real (`pyverifactu`, fase 4a), 5 de 16 refactors se
descartaron sin empeorar nada: 4 en `L0-regresión` por 1 o 2 líneas o ramas sin cubrir más que el
verde (F71) y 1 en `L0-supresiones` por juntar tres `# noqa` en uno (F72). Los 4 de F71 midieron la
cobertura solo después de editar, sin un número del verde con que comparar (el prompt lo daba para
los clones y no para la cobertura), y los 4 reorganizaban código que el verde no cubría: partirlo o
moverlo añade sentencias sin cubrir. La orden que tenía el agente, además, no mostraba las ramas sin
cubrir. Revisado por Fable, que comprobó las medidas contra los informes de cada descarte.

**F71.** La guarda no cambia: un refactor sigue sin poder dejar ni una línea ni una rama más sin
cubrir que su verde (F61). Cambia lo que se le dice:
- el prompt da, por cada control con puntuación, lo que el verde dejaba sin probar («El verde dejaba
  712 líneas y 327 ramas sin cubrir»), de `refactor_baseline`, y qué hacer si sube: es código que el
  verde no cubría y que se ha partido o movido; cubrirlo con un test unitario o no partirlo;
- `python-uv` mide con un módulo propio, `lictor_coverage.py`: ejecuta pytest con branch coverage,
  cuenta como `score_misses` y, con el informe del verde junto a él (`coverage-del-verde.json`, que
  Lictor copia de la evidencia de la verificación del verde), dice en qué funciones sube y baja lo
  que queda sin cubrir, y termina con las dos cuentas y las del verde. El estado guarda la carpeta
  de esa verificación (`refactor_baseline_step`).

**F72.** Para el implementador y el refactor, `L0-supresiones` exige que las supresiones de las
líneas añadidas quepan en las de las quitadas, en todo el cambio (`suppressions_not_moved`, con
`Vcs.removed_lines`). Cada supresión se cuenta por su texto exacto, desde donde coincide el literal
o el patrón hasta el fin de línea, con los espacios normalizados: mover una función con su `noqa` a
otro módulo, renombrar un fichero, reindentar o juntar varias iguales pasa; añadir una, cambiar su
código o ampliarla, no. El driver fresco sigue con las líneas añadidas (F12): solo añade. Límites
conocidos, con un agente que no es adversario: quitar una supresión y poner la misma sobre otro
código suma cero; mover `# pragma: no cover` a un `def` excluye el bloque y baja lo que queda sin
cubrir; un patrón al principio de una llamada partida en varias líneas solo cuenta esa línea.

## Enmienda F84 (2026-11-26): el refactor mejora lo que cambió su escenario

En la 4a de `pyverifactu` con la 0.20.0, 9 de los 21 refactors aplicados cambiaron ficheros de
código que ya existían y que su escenario no había tocado. Ninguno cambió el comportamiento, pero
entre todos sumaron unas 1 000 líneas al diff de la PR, y dos de ellos (CLI-001 y COR-001)
reescribieron el envío antiguo de `aeat_soap.py`, que los `hints` del proyecto mandaban no tocar; hubo
que revertirlos a mano. Al cruzar cada refactor con su orden, 8 de los 9 tocaron un fichero que la
orden les pedía arreglar: la complejidad y los clones que se nombraban eran los de todo `src`, y lo
que Lictor manda va por encima de los `hints`. En CLI-001 y COR-001, la lista de complejidad eran
justo las cuatro funciones del envío antiguo. Revisado por Fable, que rehízo el cruce y confirmó
los datos.

| | Por fichero | Por líneas (lo que se hace) |
|:--|--:|--:|
| Refactors con alguna función nombrada, de los 21 | 2 | 0 |
| Refactors con algún clon nombrado | 8 | 2 |

**Se pide lo que cambió el escenario, por funciones; se permite trabajar por ficheros; y una puerta
descarta lo que salga de ahí.** Las guardas de `refactor_regressions` siguen siendo de todo el
proyecto: cambia lo que se pide y dónde, no lo que no se puede empeorar.

- **Lo que se pide.** De la verificación del verde, la orden nombra solo las funciones por encima del
  límite con alguna línea que el verde cambió y los clones con algún sitio en esas líneas; el resto
  lo cuenta en una línea («no son de este refactor»). Las líneas salen de `Vcs.changed_lines`, en la
  numeración del commit verde, que es el árbol que su verificación midió. Un tramo que solo borra
  cuenta para la línea anterior. Una función anidada y la que la contiene se nombran las dos. El
  informe de complejidad lleva `end_line` por función (ADR-0013) y el checkpoint guarda todos los
  clones (ADR-0014), porque el alcance se saca de ellos.
- **Dónde puede trabajar.** El perfil declara dónde está el código (`code_paths`, `python-uv`:
  `src/`), solo en el perfil y en la huella; un `lictor.json` que lo declare, o un `paths.writable`
  que no alcance ninguna de esas rutas, sale con exit 10. Dentro del código, el refactor puede
  cambiar los ficheros que cambió el verde y los que comparten con ellos un clon que la orden nombra
  (quitarlo exige editar los dos lados, y abre ese fichero entero), y crear ficheros nuevos. Fuera
  del código (los tests unitarios) puede seguir al código: EXP-001, legítimo, cambió un test que su
  verde no tocó, y `refactor_regressions` ya descarta el que pasa menos casos. Borrar o mover un
  fichero que ya existía cuenta como cambiarlo (`Vcs.changes` divide los renombrados).
- **`L0-alcance`.** Tras `L0-rutas` y antes de `L0-sello`, solo en el refactor: descarta el que
  cambia un fichero de código fuera de ese alcance, nombrándolo (también en `reverted_files`). El
  verde se queda, como en cualquier descarte. La orden y la puerta llaman a la misma función,
  `green_scope`, que no se guarda en el estado: depende de `refactor_of`, `baseline` y `code_paths`,
  y los objetos de git no cambian, así que una reanudación saca el mismo alcance. `refactor_of` es
  obligatorio en los dos nodos: con un colateral encadenado, HEAD es un commit vacío.
- **La orden** lo dice con una regla propia, que incluye no hacer un cambio que obligue a tocar
  otro fichero de código (quien importa lo que se mueve). Los objetivos de calidad se acotan a lo que
  cambió el escenario, y `refactor_hints` ya no invita a reorganizar `__init__.py`.

No se añade una decisión del proyecto para excluir código (`refactor.exclude`): los dos casos del
envío antiguo venían de que la orden lo nombraba, y por líneas ENR-001, el único refactor dentro de
`aeat_soap.py`, no lo nombra. La puerta es por fichero porque una por función tendría que seguir el
código que se mueve dentro del fichero, y eso no se explica en una línea.

Límites conocidos:
- dentro de un fichero del escenario, la puerta no ve lo que el refactor hace de más;
- «tocado» no es «escrito»: una línea del verde en una función antigua mete toda la función en lo
  que se pide;
- las rutas del informe y las de git se comparan como texto: un informe de un control propio debe
  nombrar los ficheros como git, relativos a la raíz;
- con `format` deshabilitado, la orden de formatear `src` reescribía ficheros ajenos y la puerta
  descartaba el refactor. Lo resuelve la ADR-0018: la orden de formatear es el `agent_fix` de
  `format` y solo se da mientras ese control bloquea en cada intento.

Deuda: es la octava vez que el perfil escribe `src` (el survey, seis controles y
`lictor_project_path`); unificarlo en `code_paths` queda para otra versión.

## Enmienda de la 0.23.0 (2026-11-27): dónde está el código

`code_paths` deja de ser solo del perfil y **se suma**: el perfil dice dónde está el código, y el
proyecto añade rutas en `lictor.json`; si el perfil no declara ninguna (`generic`), el proyecto las
declara. Tras sumar tiene que quedar al menos una, y alguna dentro de `paths.writable`
(`code_paths_problem`, sin cambios). Lo resuelto es «perfil + proyecto», sin duplicados, en ese
orden.

- **Por qué sumar no debilita.** Una ruta más de código solo amplía lo que `L0-alcance` vigila: un
  refactor que cambia un fichero de esa ruta fuera de los de su verde se descarta, y antes pasaba.
  El alcance del verde también crece, pero solo con los ficheros que ese verde ya cambió. Quitar
  una ruta no se puede: el proyecto no tiene cómo restar.
- **Obligatorio aunque el refactor esté apagado.** `refactor.enabled` entra en la huella, y
  apagarlo no debe cambiar lo que hay que declarar para que la configuración cargue.
- **Los tests junto al código (`code_exclude`).** En Go y en Node los tests unitarios viven junto
  al código (`iva/x_test.go`), y la orden dice que pueden seguir al código que se mueve, pero la
  puerta los trataba como código y descartaba el refactor que añadía un caso a uno que ya existía
  (ejecutado por Fable en `wt-x1`). `code_exclude` son globs sobre la ruta entera (`*_test.go`,
  `*.test.js`) que dejan de ser código para la puerta y para el alcance (`CodePaths.holds`, domain
  `refactor_scope.py`). Solo los escribe un proyecto cuyo perfil no declara `code_paths`: sobre
  las rutas de un perfil, excluir debilitaría la puerta, y la carga sale con 10
  (`code_exclude_message`). Es la única excepción de la regla «el proyecto solo añade», y por eso
  es exclusiva, no sumable (ADR-0020).

`code_paths_message`, que rechazaba `code_paths` en `lictor.json`, desaparece.

En la 0.23.1, un `code_exclude` que excluye todos los ficheros de HEAD bajo `code_paths` (`*.go`) es
✗ «Rutas de código» en `doctor` y al empezar un run, con exit 10: la puerta no tendría nada que
mirar. Se juzga sobre los ficheros de HEAD porque ningún patrón fijo distingue `*.go` de
`*_test.go`; sin código aún bajo esas rutas no se juzga nada.
