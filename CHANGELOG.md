# Registro de cambios

Todos los cambios relevantes de Lictor se anotan aquí. El formato sigue
[Keep a Changelog](https://keepachangelog.com/es-ES/1.1.0/) y el proyecto usa
[versionado semántico](https://semver.org/lang/es/). Los códigos de salida son API pública: un cambio
en su significado se anota en «Cambiado».

## [Sin publicar]

### Cambiado

- **Lictor se puede ejecutar dentro de un worktree enlazado** (`git worktree add`). En uno, `config`,
  `hooks/` e `info/` están en el directorio común del repositorio: el ancla, la huella de los
  ficheros de control y la comprobación de cada llamada al agente los leen ahí, además de `HEAD` y
  `config.worktree` del worktree y de los ficheros que apuntan de uno a otro. Un cambio en ellos
  durante una llamada falla el intento en `L0-estado` y se deshace; después de un kill, impide
  reanudar. En un checkout normal no cambia nada (ADR-0005).
- **La ref con la que se exporta el commit que se verifica es de cada worktree**
  (`refs/worktree/lictor/verified`), para que dos ejecuciones en dos worktrees no se la pisen. La
  antigua `refs/lictor/verified` ya no se usa y se puede borrar.
- **Una ejecución empezada con la 0.2.0 no se reanuda**: sale con 10 y lo dice, en vez de leer el
  ancla antigua como una manipulación. Hay que empezar de nuevo con `lictor run --new`.

## [0.2.0] - 2026-11-10

La skill de Lictor para Claude Code y lo que la pasada greenfield `romanos` mostró que hacía falta
para que el primer contacto funcione: una terminal que cuenta lo que pasa, un informe con totales,
`doctor` que valida los escenarios y `init` que dice lo que decidió.

### Añadido

- **La skill de Lictor para Claude Code.** `lictor init` instala un esbozo en
  `.claude/skills/lictor/SKILL.md`, salvo con `--no-skill`. Al cargarse, Claude Code ejecuta
  `lictor skill --show`, que imprime la skill de la versión instalada, así que al actualizar Lictor
  la skill se actualiza sola. `lictor skill` instala o restaura el esbozo sin tocar `lictor.json`, y
  `lictor doctor` avisa (⚠) si el del proyecto no es el de esta versión. La skill ayuda a preparar
  el proyecto, a redactar los escenarios con la persona y a leer los resultados. Nunca escribe tests
  ni código, ni lanza `run`, ni hace commits. El agente de Lictor no la carga, y si en su sesión
  aparece alguna skill del proyecto, `agent.finished` la nombra y la terminal lo avisa (#16).
- **`lictor run` cuenta cada paso en stderr**: el driver, cada intento, cada verde con su tiempo y
  su coste, los colaterales, las cuarentenas y los totales. `--quiet` lo apaga, y stdout queda para
  el resultado. El evento nuevo `agent.started` se escribe antes de cada llamada (#15).
- **`lictor doctor` valida los features** con la misma regla que la carga de la especificación: que
  parseen, que cada escenario tenga exactamente un ID válido y único, sin `Esquema del escenario` ni
  IDs en una característica o una regla. Cada problema sale con fichero, línea y qué hacer, con exit
  10. Un fichero vacío o una característica sin escenarios es un aviso (⚠): el run lo salta (#13).
- **`lictor init` resume lo que decidió:** el perfil, el modelo, el `hints` que escribió, lo que
  añadió al `.gitignore` y la skill, y dice el siguiente paso, el commit y luego `lictor doctor`. La
  versión de Python de `hints` sale de `requires-python`.
- **Un control de cobertura en `python-uv`.** `coverage` mide la cobertura de `src` con los tests
  unitarios, en modo `measure`, sin bloquear. Necesita `pytest-cov`, una dependencia que solo puede
  añadir una persona: en un proyecto sin ella se omite, con su motivo, en vez de fallar en cada run.
  Lo hace un campo nuevo de los controles, `when_plugin`, que omite el control si el entorno de
  verificación no carga ese plugin.
- Una copia del informe por ejecución en `.lictor/runs/<run_id>/report.md`, que se retira con la
  evidencia del run, y el resumen dice de qué ejecución es.
- `lictor --version` dice si la instalación es editable y desde dónde: con un venv activado, la copia
  del repositorio y la publicada decían el mismo número.
- El README explica el orden de `uv init` y git, qué va en `hints` y cómo probar una CLI.

### Cambiado

- **Una ruta que solo puede tocar una persona para el run, no el escenario.** Un escenario que solo
  se cumple cambiando una ruta de `forbidden_paths` o de `paths.protected` (una dependencia en
  `pyproject.toml`, una pipeline en `.github`) gastaba todos sus intentos en `L0-rutas` y acababa en
  cuarentena con exit 30, que se lee como «el agente no supo». Ahora los prompts dicen qué rutas no
  se pueden tocar, y si dos intentos seguidos tocan la misma ruta rechazada, el run para con exit 10
  y un motivo que nombra el escenario y la ruta, sin gastar los intentos que quedan. El oráculo
  (`features/` y el driver sellado) no cuenta: insistir en reescribirlo sigue acabando en cuarentena
  (#14).
- **`acceptance.driver_hints` es solo del perfil.** `lictor init` lo copiaba a `lictor.json` y desde
  entonces el run leía la copia, así que las mejoras del perfil no llegaban a los proyectos ya
  iniciados. Ahora se lee del perfil en cada carga, y **un `lictor.json` que lo declare se rechaza
  al cargar (exit 10)**: bórralo de `acceptance`; lo propio del proyecto va en `hints`.
- `lictor status` dice el escenario y el intento en curso, los verdes implementados, colaterales y
  registrados, y las llamadas y el coste, en español (#15).
- `report.md` tiene una fila para el driver y otra para el total, los tokens de entrada con la caché,
  el SHA corto, los estados en español, la duración de reloj y las denegaciones por dueño e intento
  (#15).
- El driver nuevo pasa por `L0-supresiones` antes de ejecutarse: una supresión o un cambio de
  `sys.path` que deje se rechaza. Antes quedaba sellado para todos los intentos (#15).
- **El perfil cambió y sigue siendo `python-uv@0.1`.** Prohíbe cambiar `sys.path` en lo que añade el
  agente o el driver, `driver_hints` lo dice, `hints` es una plantilla con la versión de
  `requires-python` y tiene el control `coverage`. Todavía no hay proyectos que dependan de él, así
  que no sube de versión ni hay reglas de compatibilidad.
- **Una ejecución a medias de la 0.1.1 no se puede reanudar con la 0.2.0**: el perfil cambia y con
  él la huella de la configuración, así que `lictor run` sale con 10. Termínala con la 0.1.1 antes
  de actualizar, o empieza otra con `lictor run --new`.

### Corregido

- Si algo manipulaba de verdad el checkpoint mientras corría el agente, el run moría con «no such
  table: checkpoints» en vez de fallar el intento en `L0-estado`. Tras cada nodo que ejecuta código
  ajeno, la conexión del checkpoint se cierra sin volcar el WAL y se abre otra (#12).

## [0.1.1] - 2026-11-06

Correcciones de la pasada greenfield `romanos` con la 0.1.0 de PyPI.

### Corregido

- `lictor status` durante una ejecución ya no gasta el intento en curso. Abría el checkpoint con
  una conexión de escritura y, al cerrarla, SQLite volcaba el WAL al fichero principal: el intento
  fallaba en `L0-estado` y, con `max_attempts: 3`, dos `status` podían poner un escenario en
  cuarentena. Ahora lo lee en solo lectura, y sin ejecución no crea el fichero.
- El prompt del reintento tras un fallo en `L0-estado` ya no le lista al agente ficheros de
  `.lictor/` que no ve: dice que el intento se descartó porque el estado de la ejecución cambió
  fuera de su copia. `state.tampered` y la evidencia siguen nombrándolos.
- `lictor doctor` y `lictor check` en un repositorio sin commits salían con «Error interno», la
  traza de git y código 1. `doctor` lo muestra como una comprobación fallida más («El repositorio no
  tiene commits: haz el primero»), y las dos órdenes salen con 10. `lictor run` también lo dice.
- Los eventos de la bitácora decían `source: lictor/0.1.0` en cualquier versión: estaba escrito a
  mano. Ahora es la versión instalada.
- El README de PyPI tenía el banner y los enlaces a los ejemplos, el esquema y los ADR rotos: eran
  rutas relativas al repositorio. Ahora apuntan a GitHub.

### Cambiado

- El perfil `python-uv` rechaza en `L0-rutas` lo que hace que otra herramienta ejecute código o siga
  instrucciones al abrir el proyecto: `.claude`, `CLAUDE.md`, `CLAUDE.local.md`, `AGENTS.md`,
  `.mcp.json`, `.vscode` y `.github`, en cualquier directorio.
- `forbidden_paths` se compara sin distinguir mayúsculas: en macOS, `claude.md` se abre como
  `CLAUDE.md`.
- El driver ya no está exento de todo `forbidden_paths` en sus rutas, solo de lo que el perfil
  nombra en `driver_exempt_paths` (en `python-uv`, `conftest.py`). Lo demás se descarta de su árbol.
- **Una ejecución a medias de la 0.1.0 no se puede reanudar con la 0.1.1**: el perfil cambia y con
  él la huella de la configuración, así que `lictor run` sale con 10. Termínala con la 0.1.0 antes
  de actualizar, o empieza otra con `lictor run --new`. El perfil sigue siendo `python-uv@0.1`:
  subir la versión obligaría a editar el `lictor.json` de cada proyecto.

## [0.1.0] - 2026-11-05

Primera versión publicada, en PyPI como `lictor`. Incluye la implementación inicial, el plan de
corrección que siguió a una auditoría de la base (#1 a #6) y las correcciones de la primera pasada
con el CLI real de Claude Code (#8).

### Añadido

- `lictor init`, `doctor`, `check`, `run`, `status` y `report`, con el perfil `python-uv`, un
  ejecutor para el CLI de Claude Code y otro falso (`backend.kind: "fake"`) para probar sin modelo.
- Ciclo por escenario: driver de aceptación, rojo, implementación, verificación por niveles (L0–L5)
  y commit o cuarentena, orquestado con LangGraph y reanudable tras una pausa o una muerte del
  proceso.
- Casos de reproducción de la auditoría en `tests/integration/repro/`, con el perfil real y `uv` (#1).
- Evidencia por intento en `.lictor/runs/<ejecución>/`, con el hash de cada paso en la bitácora
  (#1, #4).
- Oráculo del rojo declarado por el perfil: cada test se enlaza con el escenario que ejecutó, el
  fallo se clasifica por la clase de la excepción y un driver nuevo se prueba con impostores (#3).
- Verdes ligados a la especificación de su commit: un escenario cuya especificación cambia se
  reabre (#3).
- Verdes colaterales: un pendiente que el árbol ya hace pasar se registra sin llamar al agente (#3).
- Puntuación de mutación en el informe y en `lictor check` (#4).
- Un cerrojo por proyecto: dos `lictor run` no pueden pisarse (#2).
- `lictor register <ID>...` para registrar, como afirmación de una persona, escenarios que el código
  ya cumple, sobre todo al adoptar Lictor en un proyecto existente. El informe y `lictor status`
  marcan esos verdes como registrados a mano (#6).
- Retención de `.lictor/runs/`: al empezar una ejecución nueva se conservan las
  `retention.keep_runs` más recientes (10 por defecto). El informe distingue la evidencia retirada de
  la alterada (#6).
- El ejemplo `examples/prestamos-biblioteca`, con código previo, `Antecedentes` y `Regla` (#6).
- Este registro de cambios y los ADR-0004 a ADR-0006.
- `lictor --version`, la licencia EUPL-1.2 y la publicación en PyPI y TestPyPI desde una etiqueta.
- El prompt de cada llamada al agente se guarda en `runs/` (`NNN-*.prompt.md`) con su hash en la
  bitácora, y el informe tiene una sección «Denegaciones».

### Cambiado

- **El verde se decide sobre lo que se commitea.** El agente trabaja en una copia sin `.git` ni
  `.lictor/`. Su resultado se lee como un árbol, y se verifica un commit candidato en un clon limpio,
  en un entorno de verificación comprobado por hash y sin configuración de usuario. La rama solo
  avanza a ese commit (#1, #2).
- El agente se ejecuta sin la configuración, los hooks ni el `CLAUDE.md` del usuario o del
  repositorio (#1).
- El agente solo dispone de las herramientas de sus reglas (`--tools`), con `dontAsk` en vez de
  `acceptEdits`, sin memoria automática y sin las variables de la sesión de Claude Code ni del
  entorno virtual de quien lo lanza.
- Los estados del backend (límite de uso, sesión, error) se deciden por campos estructurados, nunca
  por el texto. Un backend que no responde pausa con exit 40 sin gastar intentos (#1, #4).
- Las defensas de la verificación (entorno, plugins, rutas y patrones prohibidos) pertenecen al
  perfil y `lictor.json` no puede declararlas (#1).
- `allow_empty` se sustituye por `empty_exit_codes` en el perfil. Un `lictor.json` con `allow_empty`
  se rechaza al cargar (#4).
- **`lictor check` verifica como `lictor run`:** en un clon limpio y en el entorno de verificación.
  Lo que esté sin commitear se captura y se verifica como un commit candidato fuera de la rama. Ya
  no se ejecuta en el árbol de trabajo (#6).
- `lictor doctor` comprueba lo mismo que `run` antes de empezar: la identidad git, los programas de
  los controles y de la aceptación, el directorio temporal y que `verification.prepare` construye el
  entorno. Lo informa todo sin parar en el primer fallo (#6).
- **Código de salida:** un programa de un control que no se encuentra sale con 40 (entorno no
  disponible) en vez de 10. El 10 queda para lo que se corrige en el proyecto: la configuración, un
  comando vacío o el árbol sucio (#6).
- `lictor run` construye el entorno de verificación antes de la primera llamada al agente, así que
  un `prepare` roto ya no gasta el driver (#6).
- El modelo por defecto es `claude-sonnet-5-5`. Siguen sin admitirse alias (#6).
- El control de mutación del propio Lictor bloquea por debajo del 95 % (`min_score`) (#6).

### Eliminado

- `scripts/verify_self.py`: lo sustituye `lictor check --keep-going --level L1,L2,L4,L5` (#6).
- El adaptador ya no limpia ni revierte el árbol de trabajo del proyecto (`reset_to_head`) (#2).

### Corregido

- `scenario.green` y la columna Commit del informe daban al escenario implementado el commit
  colateral que va encima; ahora dan el suyo.
- El rojo de un driver sellado que pasa no dejaba su evidencia en la bitácora
  (`driver.checked`).
- Sin identidad git, `lictor run` fallaba después de gastar el driver, con «Error interno» y sin el
  mensaje de git. Ahora para antes, con exit 40 y el motivo (#6).
- `condense_error` conservaba el ruido y perdía el motivo del fallo (#1).
- Un comando que escribía bytes que no son UTF-8, o que dejaba un proceso hijo con su salida
  abierta, colgaba o tumbaba la ejecución (#4).
- La extracción del escenario para el prompt se cortaba ante un docstring que contenía una palabra
  clave de Gherkin (#4).
- Un enlace simbólico en bucle o roto bajo `features/` acababa en error interno (#4).
- `langchain-core` se importaba sin estar declarado como dependencia (#6).

### Seguridad

- Git se ejecuta sin hooks, sin `fsmonitor`, sin objetos reemplazados y sin las ubicaciones
  heredadas del entorno (#1, #2).
- El código del proyecto no entra en el arranque del intérprete (`PYTHONPATH`, `sitecustomize`), y
  las variables heredadas que cargan código se limpian (#1).
- El directorio temporal es privado y se comprueba por descriptor antes de usarlo (#2).
- La instantánea de integridad detecta y deshace los cambios en el estado de `.lictor/` y en los
  ficheros de control de `.git` durante cada llamada al agente y cada comando del proyecto. Una
  reanudación se compara con el ancla del checkpoint (#2).

### Problemas conocidos

- **El verde todavía no resiste a un agente adversario.** El agente corre con los permisos del
  usuario. Por ruta absoluta alcanza `.git`, `.lictor/`, el entorno de verificación y `~/.netrc`:
  esas escrituras se detectan mientras Lictor está vivo, pero un checkpoint reescrito también puede
  engañar a una reanudación.
- **El código del proyecto que se importa en los tests corre en el mismo proceso que el runner,** y
  puede forjar el informe de aceptación.
- **Un proceso que el agente deje vivo puede plantar en el entorno de verificación** entre
  `prepare` y su hash.

  Las tres limitaciones se cerrarán con el sandbox del incremento 4. `forbidden_paths` y
  `banned_patterns` son heurísticas: encarecen la trampa, no la impiden.
- La marca «registrado a mano» es informativa: cualquiera puede escribir trailers con git.
- Windows no está soportado.
- Solo hay un perfil, `python-uv`, y un backend real, el CLI de Claude Code con suscripción.
- `Scenario Outline` no está soportado.
- Lictor commitea en la rama actual: mientras corre, nadie más debe mover HEAD en ese checkout, y
  solo puede correr una ejecución por checkout. Cada ejecución en su clon y su rama es el ADR-0005.

[Sin publicar]: https://github.com/jmorenobl/lictor/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/jmorenobl/lictor/compare/v0.1.1...v0.2.0
[0.1.1]: https://github.com/jmorenobl/lictor/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/jmorenobl/lictor/releases/tag/v0.1.0
