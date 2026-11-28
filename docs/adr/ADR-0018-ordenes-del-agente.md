# ADR-0018: Las órdenes del agente salen de los controles

## Estado
Aceptada (2026-11-26), en la 0.21.0. Sale de F83 (la 4a de pyverifactu con la 0.20.0) y del diseño
`.audits/qa/0-21-0/diseno.md`, revisado por Fable en `.audits/qa/0-21-0/revision-fable-f83.md`
(los bloqueantes B1 y B2 y los importantes I1 e I2 cambiaron la regla y el lugar del aviso).
Incluye la opción A del punto 3.8 de F84 (`agent_fix`).

## Contexto
El agente comprueba su trabajo antes de terminar con las órdenes que le da el prompt. Hasta la
0.20.0 eran una lista aparte, `agent_commands`, que traía el perfil y que el proyecto podía
sustituir entera. En la tanda 1 de pyverifactu se perdieron un intento y un refactor (cerca de
1 $): `lint` leía `src` y los tests y rechazó dos `ARG001` en tests nuevos, mientras el agente solo
tenía `uv run ruff check src`. La persona lo arregló copiando la lista a mano (commit `1cff89a` de
la copia):

| Orden que escribió a mano | Es… |
|:--|:--|
| `uv run ruff format src tests/unit …` | el `run` de `format` sin `--check` |
| `uv run ruff check src tests/unit …` | el `run` de `lint`, igual |
| `uv run mypy src` | el `run` de `types` del perfil, igual |
| `uv run pytest tests -m "not integration" --ignore=tests/acceptance -q` | el `run` de `unit` sin `--junitxml {out}` |
| `uv run pytest tests/acceptance -q` | `acceptance.run` sin `--junitxml {out}` |

Y tuvo que reescribir el `agent_run` de `coverage`, que no seguía al `run` de `unit` que había
cambiado. El `lictor.json` de Lictor tenía el mismo desfase: `coverage` escribía su `run` sobre
`tests` y heredaba `lictor_coverage.py tests/unit -q`. La lista no aportaba nada: era una copia de
los controles, y una copia se desfasa.

Además, la orden de formatear iba en esa lista y la pedía `refactor_hints`, aunque el proyecto
hubiera apagado `format`. Con la puerta `L0-alcance` de F84, formatear todo `src` sin que el
control garantice que HEAD está formateado reescribe ficheros ajenos y descarta el refactor.

## Decisión

1. **Las órdenes salen de los controles que bloquean en cada intento.** Cuenta un control en
   `block`, de `stage: attempt`, cuyo plugin carga el entorno (`counts_for_agent`). Cada uno da:
   - su arreglo, si declara `agent_fix`;
   - su comprobación: su `agent_run` o, si no lo tiene, su `run` sin el informe. Se quita el token
     con `{out}` y, si ese token es exactamente `{out}`, la opción de delante. Un `{out}` suelto no
     da orden, y un `run` sin `{out}` queda tal como se escribió;
   - un control con arreglo da también su comprobación, así que un arreglo que no arregla lo que el
     control lee se ve antes de la verificación (B2);
   - un control leído se comprueba con su `run`, nunca con los `adds` de su lector: medir tiene su
     orden, y el agente dejaría informes en su copia. Un lector da su `agent_run` o nada.

   La aceptación va al final. Solo queda lo que `agent_tools` permite (`allowed_by`, en el dominio),
   y cada orden una vez.
2. **`agent_fix` es un campo del control de comando:** la orden con la que el agente deja hecho lo
   que el control comprueba. Se valida contra `agent_tools` como `agent_run` y es un campo de una
   ejecución, así que un lector no lo tiene.
   - En `python-uv` lo tiene `format` (`uv run ruff format src`), que deja de estar en una lista y
     en `refactor_hints`.
   - Al implementador y al refactor una línea les pide ejecutarlo tras su último cambio y antes de
     las comprobaciones. El driver lo recibe en su lista, pero sin esa línea.
   - Solo se ofrece mientras el control cuenta (B1): con `format` apagado, en `measure` o en
     `check`, nadie formatea.
3. **Un `run` cambiado no hereda la orden del agente.** Cuando el proyecto escribe el `run` de un
   control (o el `adds` o el `reads` de un lector, o el `run` del control que lee) y el perfil le
   da un `agent_run` o un `agent_fix` que el proyecto no escribió, la carga sale con exit 10: hay
   que escribirlos, o escribir `null`.

   Es un error y no un aviso (I1) por tres razones:
   - el remedio siempre es escribirlo;
   - los tres casos reales estaban desfasados;
   - el dato («qué escribió el proyecto») solo existe al cargar.
4. **`agent_commands` desaparece** del perfil, de la configuración y del esquema. Un `lictor.json`
   que lo declare sale con exit 10 y dice dónde va lo suyo; lo hace después de resolver el perfil,
   para que un perfil retirado siga diciéndolo antes.
5. **La orden distingue dos cosas que mezclaba.** Qué admite Bash sale de los prefijos de
   `agent_tools`, no de las órdenes con que se comprueba. Las comprobaciones van una por línea.

## Alternativas descartadas
- **Comparar con el survey qué ficheros lee cada orden** (lo que proponía el plan): compara dos
  listas que no deberían existir. Derivar quita el desfase de raíz.
- **Avisar de lo heredado en lugar de rechazarlo:** un aviso que se ignora cuesta un intento, y
  sacarlo de la carga exigía un canal nuevo hasta `doctor` y el run.
- **Avisar de un control que bloquea sin orden para el agente** (`sin_orden`):
  - era ambiguo (en pyverifactu, `api` tiene el `run` del perfil y el `block` de la persona);
  - con el `lictor.json` de Lictor saltaría siempre (`architecture`, `coverage-domain`,
    `secrets`), sin forma de callarlo;
  - ningún intento se perdió por eso.
- **Mantener la lista y avisar cuando se desfasa:** seguiría siendo una copia.

## Consecuencias
- Con el perfil tal cual, las órdenes son las de antes, en su orden, más
  `uv run ruff format --check src`, que da ahora `format`. Con el `lictor.json` de pyverifactu sin
  su lista, las comprobaciones son las que escribió la persona, token a token.
- Cambio incompatible: un `lictor.json` con `agent_commands`, o que cambia el `run` de un control
  del perfil con orden del agente sin escribirla, ya no carga. Nadie usa Lictor todavía.
- El `lictor.json` de Lictor escribe el `agent_fix` de `format` y el `agent_run` de `coverage`.
- Límite conocido: una opción sin valor justo delante de `{out}` (`-v {out}`) se quita también.
  No hay ningún caso real, y quien lo sufra escribe `agent_run`.

## Cómo se comprueba
- `tests/unit/test_controls.py`: la regla, los casos de `{out}`, lo que cuenta, lectores y la línea
  del arreglo.
- `tests/unit/test_config.py`:
  - el perfil y la tabla de pyverifactu;
  - el rechazo de `agent_commands`;
  - lo heredado y desfasado, y `null`;
  - `agent_fix` en un lector.
- `tests/integration/repro/test_v0_21_0_agent_commands.py`:
  - `lint` con otro `run` llega al implementador;
  - con `format` apagado nada pide formatear y el refactor no cae en `L0-alcance`;
  - con `format` en `block`, sí.

## Enmienda de la 0.25.0 (2026-11-28): la orden de cada control que bloquea se concede tal cual

`agent_tools` del proyecto sustituye entero al del perfil, así que un control añadido no llegaba al
agente sin copiar todas las reglas del perfil (S17). Ahora la orden de comprobación de cada control
que bloquea en cada intento, y la de la aceptación, se conceden al agente con una regla exacta
(`granted_tools`), salvo con `"agent": false` en el control. La orden del agente dice aparte las
órdenes exactas («y estas órdenes tal cual»), y la `agent_run` de un control concedido no necesita
regla en `agent_tools`. Una orden con `*`, `?` o `[` no se concede: en una regla son comodines y
admitiría más que la orden; `doctor` sigue diciendo que el agente no puede ejecutarla. Un control
cuya orden no puede pasar en la copia del agente (sin `.git`, o que lee lo que dejó otro control)
lleva `"agent": false`, como `secrets` y `coverage-domain` en el `lictor.json` de Lictor.
