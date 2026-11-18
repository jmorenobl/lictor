# ADR-0010: Un paso de refactor tras cada escenario

## Estado
Aceptada (2026-11-18). Sale del roadmap tras la 0.4.0 (fase 2) y de la pasada de referencia de la
fase 1. Un revisor independiente comprobó el borrador contra el código, y esta versión recoge sus
hallazgos (punto 12).

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
     puede tener menos casos en verde, ni ningún control con puntuación una puntuación más baja, que
     en la verificación del verde. Sin esto, un refactor podría borrar los unitarios: en
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
  control con puntuación del punto 6.

## Cómo se comprueba
Se repite la pasada de la calculadora con las mismas entradas y se compara con la referencia de la
0.6.1, para que la diferencia sea solo del refactor. Metas: CC máxima ≤ 10 y ninguna función con
CC > 10, ninguna de más de 30 NLOC, duplicación < 3 %, un módulo por etapa, 0 falsos verdes y
cobertura ≥ 80 %.
