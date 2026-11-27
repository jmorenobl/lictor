# Registro de cambios

Todos los cambios relevantes de Lictor se anotan aquí. El formato sigue
[Keep a Changelog](https://keepachangelog.com/es-ES/1.1.0/) y el proyecto usa
[versionado semántico](https://semver.org/lang/es/). Los códigos de salida son API pública: un cambio
en su significado se anota en «Cambiado».

## [Sin publicar]

## [0.23.2] - 2026-11-28

### Cambiado

- **La receta de Go no usa `t.Run`** (G1): cada subtest sale en el informe como otro caso con el
  mismo ID, y un escenario es un solo caso. Las filas de una tabla del Gherkin se recorren con un
  bucle dentro del mismo test. Antes la receta pedía el `defer` «también dentro de cada `t.Run`».
- **La receta de Go añade `GOFLAGS=-modcacherw`** (G7): sin él Go deja la caché de módulos de solo
  lectura dentro del worktree, y no se podía retirar.
- **`lictor retire` retira una ejecución que no llegó a empezar**: antes se negaba y pedía un
  `lictor run`, que la habría empezado y gastado.

### Corregido

- **Un worktree a medio borrar ya no se toma por una ejecución que sigue** (G8). Si no se podía
  leer su configuración, contaba como reanudable: `retire` se negaba y proponía un `lictor run`
  que gastaría. Ahora dónde está cada ejecución se lee sin su configuración. Una rota o retirada a
  medias nunca se reanuda: `run` avisa de ella, y con `--branch` sale con 10 y dice qué hacer;
  `status` la cuenta sin fallar. `retire` marca la ejecución antes de borrar nada y con la marca
  termina el retiro a medias. Sin ella, solo borra lo que es de Lictor, y antes mira qué queda. Un
  worktree desenlazado porque se movió el checkout no se toca: se repara con `git worktree repair`.
- **`retire` quita lo que una herramienta dejó de solo lectura en su entorno** (G7), como la caché
  de módulos de Go, devolviendo el permiso de escritura solo dentro de lo suyo. Lo mismo al
  reconstruir el entorno y al barrer los temporales, que antes dejaban restos sin avisar.
- **Un escenario que el driver sellado da en varios casos para el run con 10** (G1), tras dos
  verificaciones seguidas, en vez de gastar todos los intentos de todos los escenarios. El mensaje
  de «varios casos» dice por qué pasa y cómo se arregla.

## [0.23.1] - 2026-11-27

### Cambiado

- **El job «Cadenas: Go y Node» corre con Node 24 y con Node 26**, la LTS de mantenimiento y la
  activa: una receta de la skill fallaba solo con la 24.
- **Lo que solo declara el perfil se rechaza con una sola frase**, «solo lo declara el perfil, y
  ningún proyecto lo escribe», también `oracle`, `driver_exempt_paths`, `status`, `detect` y los
  campos de `verification` que antes daban el error de pydantic en inglés. La lista sale de los
  modelos, así que un campo nuevo del perfil se rechaza igual.
- **Las reglas de las órdenes que nombran una lista tienen su frase sin ella**: sin supresiones,
  sin rutas vedadas, sin verdes, sin nada protegido fuera del driver. Ya no salen «No añadas
  supresiones: ninguna.» ni «Los escenarios ninguno ya funcionan».

### Corregido

- **Un `code_exclude` que excluye todo el código de HEAD** (`*.go`) dejaba la puerta del refactor
  sin nada que mirar, sin aviso. Ahora es ✗ «Rutas de código» en `lictor doctor` y al empezar un
  run, con exit 10. Un proyecto que aún no tiene código ahí queda «no comprobado» en `doctor`, y
  la puerta `L0-alcance` lo juzga sobre el árbol de su primer verde.

## [0.23.0] - 2026-11-27

### Añadido

- **El nivel de prueba, calculado (ADR-0020).** Una función del dominio lee las defensas resueltas
  y dice qué vale un verde: **resultado probado** con todo el oráculo de `python-uv` (impostores de
  stubs, clases de cada fallo, enlace con el escenario e impostor de la interfaz), **básico** con
  menos. Junto al nivel salen los huecos que Lictor sabe calcular, cada uno con su frase: un rojo
  sin clase, el enlace por nombre, un escenario ya cumplido sin prueba, el valor del Gherkin sin
  pesar, un entorno sin hash, ninguna supresión, ningún control que bloquee, nada que lea qué ve
  cada control, un control cuyo plugin el entorno no carga. Lo dicen `lictor doctor` («Nivel de
  prueba», con ⚠ si hay huecos, sin cambiar el código de salida), el arranque del run, `lictor
  status` (también en `--json`) y `report.md` (en «Resumen» y en «Lo que este nivel no
  comprueba»). `run.started` lo guarda como códigos.
- **El perfil `generic@0.1` (`alpha`)**, para cualquier lenguaje que deje un informe JUnit (Go,
  Node, Python sin `uv` o sin `src/`). Nunca se detecta: `lictor init --profile generic`, que dice
  en su salida lo que hay que escribir (`paths`, `acceptance`, `code_paths` y `agent_tools`). Su
  skill trae las recetas de Go, de Node y de Python, con las trampas medidas. Su nivel es básico:
  **un test que no compara nada da un verde**, y la skill pide leer una vez el driver sellado. Lo
  cierra el impostor básico de la 0.24.0.
- **`code_exclude`** en `lictor.json`, con globs (`*_test.go`), para un perfil que no declara
  `code_paths`: los tests unitarios junto al código dejan de ser código para la puerta del refactor
  (`L0-alcance`).
- **«Órdenes del agente» en `lictor doctor`**: avisa de un control que bloquea, o de la
  aceptación, que `agent_tools` no deja ejecutar al agente, o que no tiene orden que darle porque
  su informe `{out}` va como argumento suelto. `doctor` dice además si el perfil no es estable
  («perfil 'generic@0.1' (alpha)»).
- **El job de la CI «Cadenas: Go y Node»**, solo en Linux (Go 1.26, Node 24, `gotestsum` v1.13.0):
  los casos `toolchain`, con un arnés propio y un oráculo que no usa Lictor.

### Cambiado

- **Las defensas, en tres clases.** `suppressions`, `banned_patterns`, `forbidden_paths` y
  `code_paths` de `lictor.json` **se suman** a las del perfil (antes, un `lictor.json` que las
  escribía no cargaba). `verification.prepare` y `acceptance.driver_hints` son **exclusivas**: el
  proyecto las escribe solo si su perfil no las declara. El resto (el oráculo, `survey`, los
  plugins, `refactor_hints`…) sigue siendo solo del perfil. Cambia `config_fingerprint`: un run de
  una versión anterior no se reanuda.
- **`paths`, `acceptance` y `detect` son opcionales en un perfil.** Lo que falte, entre ellos y
  `code_paths`, se nombra de una vez al cargar, con exit 10.
- **La orden del driver pide, en todos los perfiles, stubs que fallan con el «no implementado» del
  lenguaje y nunca devuelven un valor**, y que el driver recoja ese fallo en cada test si en su
  lenguaje para el proceso de tests (un `panic` de Go). Un test cuyo escenario espera un error
  comprueba cuál. Sin impostor de la interfaz, dice que un escenario que el código ya cumple lo
  registra una persona.
- **Un perfil no puede declarar `oracle.impostors` sin `red_kinds`.**

### Corregido

- **Lo que engañaba sin el plugin de `python-uv`:** «Medidas del driver» daba «falla sin una
  aserción del driver» de un test que fallaba por su aserción (ahora, el estado del JUnit); «Tests
  aceptados sin comprobar» decía que se comprobaron todos; «Cambios que ningún control ejecutó»
  decía que el run no movió la rama, cuando sí la movió; «Entorno de verificación listo» sin
  entorno; `doctor` daba por comprobadas las dependencias sin nada que comprobar; el refactor oía
  que pasar menos tests lo descarta, sin un control que los cuente; las órdenes del implementador y
  del refactor decían «No añadas supresiones: .» sin ninguna.
- **Un driver nuevo cuyo informe no tiene caso de ningún escenario** (uno de Go que no compila)
  recibe en el reintento la salida de la aceptación, con el error del compilador. **Con el driver
  sellado, un escenario sin caso** ya no se lee como un test mal nombrado: «la aceptación se cortó
  antes de ejecutar N tests».
- **Un perfil sin marcadores de `detect` se detectaba en cualquier proyecto.**

## [0.22.0] - 2026-11-27

### Añadido

- **El código lee la metadata de su propio paquete en la verificación (F82, ADR-0019).** El
  proyecto sigue sin instalarse, a propósito, pero el plugin que pone `src` en el path registra en
  memoria, dentro de pytest, la distribución que instalarlo dejaría: el nombre, la versión y los
  entry points (menos `pytest11`), leídos del `pyproject.toml` del clon sin ejecutar nada por el
  nuevo módulo del perfil `lictor_metadata.py`. La versión puede ser estática o dinámica en un
  literal (hatch `path`, setuptools `attr` o `file`, flit), normalizada como la escriben los
  backends. Solo se registra si `uv` instalaría el proyecto (`[build-system]`, y `tool.uv.package`
  no es `false`) y si el entorno no tiene ya una distribución con ese nombre; una versión de git no
  se registra. En agentix, `__version__ = version("agentix")` dejaba `unit` con 24 errores de
  recogida y «Los controles pasan en HEAD» en ✗; ahora pasa, con 2652 tests. En pyverifactu, su
  grupo `verifactu_py.certificate_providers` salía vacío sin que nada lo dijera; ahora se ve.
- **«Dependencias de la verificación» dice si la metadata del proyecto está:** ✓ con su nombre, su
  versión y sus grupos; ✓ «sin metadata del proyecto» si no es un paquete (sin `[build-system]`),
  donde tampoco la tendría instalado; o ⚠ con la razón («la versión sale de git») y qué hacer. Es un aviso: un código que no lee su metadata no pierde nada. Un run nuevo repite los
  avisos de esa comprobación al empezar (`run.started` los guarda en `dependencies`).

### Cambiado

- **En `lictor check` de Lictor, su `__version__` deja de ser `0+desconocida`:** es el primer
  proyecto que lee su metadata. Cambia `config_fingerprint`, porque cambia el contenido de
  `verification.files`: un run de una versión anterior no se reanuda.
- **`importlib-metadata` entra en el grupo de desarrollo de Lictor**, solo para que la CI pruebe que
  el backport recibe una distribución de su propia clase. No cambia las dependencias del paquete.

- **La autoverificación de la CI va en dos jobs a la vez: L1, L2 y L4 por un lado y la mutación
  (L5) por otro.** El paso único tardaba 10 min y se cortaba a los 12: en un runner un 20–40 % más
  lento se cortó tres veces en seis runs, siempre durante la mutación y sin dejar su log. Cada
  job tiene su presupuesto (6 y 10 min) y su corte (15 y 18 min el paso, 17 y 20 el job), con la
  regla de AGENTS.md. Los `timeout_s` del `lictor.json` de Lictor quedan por debajo del corte de su
  paso (`coverage`, el de por defecto, 600 s; `mutation`, 900 s), así que un control colgado lo
  corta Lictor, con su log y su veredicto, y `--keep-going` sigue. Un test lo comprueba leyendo
  `ci.yml`. Los dos jobs dicen qué runner les tocó (`lscpu`). No cambia el paquete: solo la CI y
  el `lictor.json` del repositorio, así que un `lictor check` local corta `coverage` a los 600 s.

## [0.21.0] - 2026-11-26

### Cambiado

- **El refactor mejora lo que cambió su escenario (F84).** En la 4a de pyverifactu, 9 de 21
  refactors cambiaron código que su escenario no tocó, y 8 de ellos porque la orden les nombraba la
  complejidad y los clones de todo `src`. Ahora la orden nombra solo las funciones por encima del
  límite con alguna línea que cambió el verde y los clones con algún sitio en esas líneas, y cuenta
  el resto en una línea; los objetivos de calidad se acotan a lo que cambió el escenario. Lo que no
  se puede empeorar sigue siendo de todo el proyecto. Enmienda de la ADR-0010.
- **El informe de complejidad lleva `end_line`** por función: uno sin él no es válido (ADR-0013).
  El checkpoint guarda todos los clones del verde, no solo los 20 que se imprimen (ADR-0014).
- **Las órdenes del agente salen de los controles (F83, ADR-0018).** `agent_commands` desaparece:
  el agente comprueba su trabajo con cada control que bloquea en cada intento (su `agent_run`, o su
  `run` sin el informe) y con la aceptación, así que un control que el proyecto cambia llega al
  agente tal cual. En la 4a de pyverifactu, `lint` leía más que la orden fija del perfil y se
  perdieron un intento y un refactor. La orden dice qué admite Bash por los prefijos de
  `agent_tools` y lista las comprobaciones una por línea. **Incompatible:** un `lictor.json` con
  `agent_commands` sale con exit 10, y también uno que cambia el `run` de un control (o el `adds`
  de `coverage`, o el `run` de `unit` que lee) y hereda el `agent_run` o el `agent_fix` del perfil:
  hay que escribirlos, o `null`.
- **La orden de formatear es el `agent_fix` de `format`,** y solo se da mientras ese control
  bloquea en cada intento: con `format` apagado nadie formatea, y el refactor no cae en
  `L0-alcance` por reescribir ficheros ajenos. Sale de `refactor_hints`.
- **`refactor_of` es obligatorio** en los nodos del refactor: si falta, el run sale con un error
  interno en lugar de comparar con un HEAD que, tras un colateral, es un commit vacío.
- **Un Entonces que no puede leer lo alterado prueba su test si lo lee donde compara el valor del
  escenario (F85, enmienda de la ADR-0016).** En la 4a de pyverifactu, COR-006 fue a la persona como
  `solo_el_valor`: su Entonces parseaba el XML que devuelve el código, y alterado ya no se podía
  leer. Ahora un modo en que el Entonces falla con un error cuenta como cazado si se rompió en la
  sentencia que compara un valor del escenario, o en una que la alimenta (definición y uso). Un
  Entonces así sin ningún valor del escenario es `ilegible`, final y para la persona, en lugar de
  reintentarse con un mensaje falso. Sobre las pasadas medidas, sin falsos probados nuevos: COR-005,
  COR-006, ENR-001, ENR-002 y RES-001 ×2 quedan probados.
- **`menos` sobre un texto** quita el último carácter y, si era un blanco, también el último que no
  lo es: quitar solo el `\n` final no cambiaba lo que lee un test que parsea o limpia el texto.
- **`api.base` sin control `api`** sale con exit 10 al cargar: no compara nada, y una referencia que
  no existe paraba el run igualmente. **Incompatible.**
- **`lictor check --base` sin control `api`** compara igual «Cambios que ningún control ejecutó» y
  dice que la API no se compara, en lugar de saltarse las dos cosas en silencio.
- **Los fallos con el mismo error se agrupan también por su sitio:** dos aserciones distintas con el
  mismo mensaje ya no se juntan en la orden.

### Añadido

- **`L0-alcance`: una puerta del refactor.** Tras `L0-rutas`, descarta el refactor que modifica,
  mueve o borra un fichero de código que ya existía y que su escenario no cambió, salvo el otro lado
  de un clon que la orden le nombra; los ficheros nuevos y los tests unitarios pueden seguir al
  código. El verde se queda, como en cualquier descarte.
- **`agent_fix` en los controles:** la orden con la que el agente deja hecho lo que un control
  comprueba, validada contra `agent_tools` como `agent_run`.
- **`lictor.statement`**, una propiedad de `lictor_outcome`: las sentencias en que se lanzó el fallo
  de un paso, desde la del paso, con lo que alimenta cada una.
- **`code_paths` en el perfil** (`python-uv`: `src/`): dónde está el código, solo del perfil y en la
  huella. Un `lictor.json` que lo declare sale con exit 10, y también uno cuyo `paths.writable` no
  alcance ninguna de esas rutas, porque la puerta no comprobaría nada.

### Corregido

- **`lictor.frame` y `lictor.origin` ya no toman por del proyecto una ruta relativa,** como la que da
  un módulo compilado de su fuente (`src/lxml/parser.pxi`): un `NotImplementedError` de una
  extensión así podía contar como el rojo de un stub.
- **Al reanudar un run, el progreso ya no repite el ⚠ de la API** de lo que se rompió antes de la
  pausa, y los totales cuentan también lo de antes.
- **El final de un run no se rompe si git falla** al leer «Cambios que ningún control ejecutó»: el
  informe da la razón.

## [0.20.0] - 2026-11-25

Lo que la 0.19.0 dejó a medias y la repetición de la fase 4a de pyverifactu necesita para leerse:
qué rompió el trabajo entero y qué cambió sin que ningún control lo ejecutara. Medido en los clones
de la 0.19.0 antes de diseñar, revisado por Fable con sus hallazgos de peso comprobados
ejecutándolos (`.audits/qa/0-19-1/`). `lictor.json` gana un campo (`api.base`) y el diario un
evento: un run a medias no se reanuda.

### Añadido

- **`api.base`: la base de la API pública es la rama a la que se integra.** Con `"api": {"base":
  "origin/main"}`, cada run compara la API con el punto en que HEAD salió de esa rama
  (`merge-base`), no con su propio arranque: la segunda tanda de un trabajo ve lo que rompió la
  primera. Sin el campo, como antes. `lictor check` la usa sin `--base`, y `--base` compara también
  desde el punto en que lo verificado se separó de esa referencia. Entra en la huella de la
  configuración.
- **«La API frente a su base», en `doctor` y al arrancar un run.** Con `api.base`, compara HEAD con
  ella antes del primer intento: una referencia que no existe o sin antepasado común (un clon
  superficial: trae más historia) es ✗ del proyecto; con `api` en `block`, una rotura que HEAD ya
  tiene y nadie aceptó también, porque cada intento fallaría haga lo que haga el agente; en
  `measure` es un aviso. Lo que ya estaba roto queda en `run.started` (`api_start`), y el progreso
  solo avisa (⚠) de lo que un verde rompe de nuevo.
- **«Cambios que ningún control ejecutó».** Al terminar un run que movió su rama, `report.md` lista
  lo que cambió desde su arranque que ningún control ejecuta, con la regla del contrato de arranque
  sobre el árbol final: tests que ningún control ejecuta o que solo ejecuta `lictor check`, ficheros
  que importan el código y ningún control lee, Python fuera del código que nadie lee, lo que no es
  Python, y los borrados aparte. Un test que un control nombra y del que su JUnit no tiene ningún
  caso (un `-m "not integration"`) cuenta como no ejecutado: en la fase 4a eran tres ficheros de
  `tests/integration` que el arranque daba por cubiertos. `lictor check` con base dice lo mismo
  desde ella. Son hechos: no bloquean ni cambian el código de salida.

### Cambiado

- **`lictor check` dice por qué falla cada control**, bloquee o mida: bajo su línea, la razón (los
  nombres rotos de la API, las funciones complejas, los casos fallidos) y dónde está entera
  (`.lictor/check/<id>.error.txt`; la de la API, con todas sus roturas). Ya no repite al final el
  primer fallo.
- **Los fallos iguales se cuentan una vez.** El error que lee el agente, y el de «Los controles
  pasan en HEAD», agrupan los casos con el mismo mensaje y la misma última línea del detalle: «24
  casos con el mismo error», con todos los nombres que quepan y la causa
  (`PackageNotFoundError`). Antes, 24 veces «collection failure» y la causa fuera del límite.
- **El veredicto de `api` da solo los hechos.** La frase de `LICTOR: SPEC_CONFLICT` sale de él: el
  implementador la oye en sus medidas, y solo cuando `api` bloquea; que una rotura aceptada se
  apunta en `api.accepted` lo lee la persona en `check`, no el agente.
- **«Los controles pasan en HEAD» no ejecuta `api` cuando hay `api.base`**: lo compara «La API
  frente a su base», una sola vez.

## [0.19.0] - 2026-11-25

Lo que estorbó llevar la fase 4a de pyverifactu a su repositorio (PR #1 de verifactu-py) y lo que
sale al pasar `doctor` por otros proyectos reales (agentix, bookwright, local-llm-bench). Medido en
clones de los cuatro antes de diseñar, revisado por Fable con sus hallazgos comprobados
ejecutándolos (`.audits/qa/0-19-0/`). Cambia el perfil (dos controles nuevos) y `lictor.json` (dos
campos nuevos): un run a medias no se reanuda.

### Añadido

- **Reglas del proyecto (`conventions`, F79).** `lictor.json` acepta `conventions.text`: las reglas
  de código del proyecto (idioma, bibliotecas, capas…), hasta 4000 caracteres. Llegan a los
  agentes del driver, del implementador y del refactor como «Reglas del proyecto», con un orden fijo
  para cuando chocan: las reglas de Lictor > los valores del escenario > `hints` > `conventions` >
  `refactor_hints`. Al driver se le dice que los nombres del código siguen esas reglas, no el idioma
  del Gherkin. `lictor doctor` avisa (⚠) de cada `AGENTS.md`, `CLAUDE.md`, `CONTRIBUTING.md` o
  `.github/copilot-instructions.md` de HEAD sin revisar o que cambió desde la revisión, por hash de
  contenido. `lictor conventions` enseña las reglas y el estado de cada fichero, y
  `lictor conventions --reviewed` los da por revisados. La skill extrae con la persona solo las
  reglas de código. `report.md` las imprime y dice que no se verifican.
- **Control `api` (L1, `measure`; ADR-0017, F80).** Mide la API pública alcanzable desde los
  módulos públicos de `src` (reexportes, `__all__`, herencia, constantes, campos, miembros de
  `Enum`, y de cada invocable sus parámetros y si es `async`) con `python_uv/lictor_api.py`, sin
  dependencias, y la compara con el commit en que empezó el run. Mover una definición y
  reexportarla no es rotura. En `block` el intento falla nombrando lo roto; en `measure` el
  implementador oye una frase fija y el verde que rompe algo sale con ⚠. En pyverifactu da
  exactamente lo que rompió la fase 4a: 4 símbolos eliminados y `register` sin `--blockchain`.
  `api.accepted` en `lictor.json` acepta roturas por nombre. `report.md` tiene «API pública», con
  dónde se cita cada nombre roto fuera de `paths.writable`; `lictor check --base <rev>` compara.
- **Control `format` (L1, `block`, F75):** `uv run ruff format --check src`, y
  `uv run ruff format src` el primero de `agent_commands`. Un proyecto que no lo usa escribe
  `{"id": "format", "enabled": false}`.
- **«Los controles pasan en HEAD».** `doctor` y un run nuevo ejecutan una vez en HEAD los
  controles que bloquean, antes de ningún agente: lo que falla ahí es del proyecto (✗, 10), con su
  error y dónde está su salida. En agentix lo da `unit` (el paquete lee su propia metadata y no está
  instalado en el entorno de verificación, F82); en pyverifactu antes del arreglo, `format`.
  Un control cuyo programa no se encuentra no se ejecuta ahí: ya lo dice «Comandos de
  verificación», como de la máquina (40).
- **La CI del proyecto frente a los controles (F76).** `doctor` y un run nuevo leen
  `.github/workflows/` y `.pre-commit-config.yaml` (con PyYAML) y avisan, con fichero y línea, de lo
  que la CI comprueba con `pytest`, `ruff check`, `ruff format` o `mypy` y ningún control hace, o
  hace en menos ficheros. Un paso se acepta en `blind_spots.accepted` como `"fichero: comando"`.
- **`lictor retire` guarda la evidencia (F56)**: `journal.jsonl`, `report.md` y `runs/` del run van
  a `.lictor/retired/<nombre>-<run>/` del checkout antes de retirar el worktree, con
  `retention.keep_retired` (10). Si no se puede copiar, no se toca nada y sale con 40.
- **La duración del rojo (F52).** `driver.sealed`, `driver.rejected`, `driver.checked` y
  `driver.measured` llevan `duration_ms` y `phases` (rojo, stubs, interfaz, Gherkin: ejecuciones y
  duración). La línea del sello lo dice, y `report.md` suma el rojo y tiene «Comprobaciones del
  rojo».

### Cambiado

- **El `pythonpath` del proyecto vuelve, al final del path (F77).** `lictor_project_path` lo lee
  del fichero que pytest eligió y lo añade tras `src`, detrás de las dependencias: en agentix
  recogía 13 ficheros menos de `tests/unit`. En pytest 7/8 bloquea el plugin `python_path`, que
  fallaba sin fichero de configuración antes de la 8.1.
- **Los plugins se marcan para reescribir antes de importarse (F81)**, como hace pytest con los
  suyos: con xdist y `filterwarnings = ["error"]` la suite no arrancaba.
- **`--cov` ya no ciega la inspección (F78):** se lee como opción de valor opcional sobre `addopts`
  y el comando. En bookwright salen los 1052 tests que tapaba. Dos casos nuevos dan 10 (✗ en
  «Dependencias de la verificación»): un `--cov` al final de `addopts`, que se come la ruta del
  comando, y un `--cov` en `addopts` con un mínimo de cobertura, con el que la aceptación nunca
  pasaría.
- **`.lictor/` lleva su propio `.gitignore` con `*` (F53):** `run` ya no avisa de su propio
  cerrojo como cambio sin confirmar, y `git worktree remove` acepta el worktree.
- El refactor se descarta en `L0-regresión` si rompe algo de la API pública que su verde no rompía.
- Nueva dependencia: `PyYAML`.

## [0.18.0] - 2026-11-25

Un escenario cuyo resultado es un objeto se prueba, y el Entonces tiene que leer el valor de su
escenario (enmienda de los objetos de valor de la ADR-0011, y ADR-0016). En los 317 escenarios de
las pasadas, juzgados como si fueran nuevos, el impostor probaba 284; ahora prueba 307. De los
otros, 9 van a la persona con un mensaje cierto en lugar de reintentarse con uno falso, y uno
(VER-005), que recibe un valor del escenario y no lo usa, se rechaza diciéndolo. Ningún test
correcto se rechaza.

### Añadido

- **El impostor de la interfaz altera objetos de valor** (F62): una dataclass, un `NamedTuple`, un
  modelo de pydantic v2, una clase de attrs y un miembro de un `Enum` que no sea `Flag`. Todos los
  campos a la vez, en una copia de la misma clase construida sin ejecutar su código. Lo que vuelve
  a la interfaz vuelve real, también un objeto mutable que el driver no ha tocado.
- **Una excepción que solo provocó un valor alterado no prueba nada**: `provocada`, final. El
  impostor numera las llamadas del driver y dice cuáles lanzaron en el control y cuáles tragó o
  sustituyó en cada modo.
- **El impostor del Gherkin** (`python_uv/lictor_gherkin.py`, ADR-0016). Un Entonces que recibe
  un valor del escenario y no lo lee nunca (un hecho de su bytecode, sin más ejecuciones) se
  rechaza como `ignora_el_valor`, con reintento, diciendo el paso y el valor. Un test que la
  interfaz no prueba pero que lee cada valor de su escenario (un fichero, la salida de una orden,
  lo que recibió un doble) va a la persona como `solo_el_valor`, final, con dónde lo lee y sin
  invitar a registrarlo. Solo corre sobre lo que la interfaz no prueba.
- **Las medidas del driver dicen qué pesó en cada veredicto**: `interface`, `provoked` y `gherkin`
  en `lictor.driver.measured.v1`, y una columna «Gherkin» en «Medidas del driver» de `report.md`.

### Cambiado

- `interface_impostor` del perfil tiene `gherkin`, la variable del impostor del Gherkin
  (`LICTOR_GHERKIN` en `python-uv`), y `lictor_gherkin` está en sus `plugins` y `files`.
- Un test cuyo resultado es una clase que no es de valor, o `[0]` de una lista de uno, y que lee el
  valor de su escenario, ya no se reintenta con un mensaje que no puede cumplir: va a la persona.
- En los stubs, `solo_el_valor` se rechaza como `a_traves` (`sin_codigo`), y `provocada` con su
  propio mensaje.

## [0.17.0] - 2026-11-24

El impostor de la interfaz en código adoptado (ADR-0011, enmienda de las corrutinas y las
librerías). En `pyverifactu`, de 51 escenarios que el código ya cumpliera, el impostor probaba 7 y
a 38 les decía en falso que no llamaban a la interfaz; ahora prueba 22, y de los que no puede probar
dice por qué. En los 254 escenarios síncronos de las otras pasadas no cambia ningún veredicto.

### Añadido

- **El impostor de la interfaz envuelve las corrutinas** (F69). Una API `async` que el código ya
  cumple se prueba como una síncrona: el resultado se altera al terminar el `await`. El llamador es
  quien crea la corrutina (`asyncio.run(f())`, `gather`, `create_task`), no el bucle que la avanza, y
  una corrutina que nunca se espera no cuenta como llamada.
- **Un test que solo ejecuta una orden a través de una librería tiene su desenlace, `a_traves`**
  (F74). Si el proyecto llama a la interfaz después de que el driver entrara en él por una librería
  (typer guarda la orden antes de que el impostor la envuelva), el impostor no puede alterar nada:
  el rechazo es final al primer intento, sin gastar el segundo, y la persona lee por qué librería
  pasó y cómo registrarlo. En los stubs, un test así se rechaza.
- **Las medidas del driver dicen qué llegó a la interfaz por una librería**: `via`, `handed` y
  `libraries` en `lictor.driver.measured.v1`, y una columna en «Medidas del driver» de `report.md`.

### Cambiado

- **Lo que el driver devuelve a la interfaz vuelve a ser el valor real.** Un Entonces que pedía una
  huella y se la pasaba a la interfaz recibía la huella alterada, y un test correcto acababa en
  `no_aserta`. Solo para valores inmutables: una lista que el driver cambió es suya.
- **Una función que el driver le pasa a una librería** (`pytest.raises(E, f, x)`, `anyio.run(f)`)
  sigue en `no_llama`, reintentable, pero el mensaje dice qué librería la llama y que la llame desde
  el paso.

### Corregido

- **Los módulos del perfil `python-uv` cargan con Python 3.9.** `lictor_impostor.py`,
  `lictor_outcome.py` y `lictor_complexity.py` no se podían importar con 3.9, así que en un proyecto
  con 3.9 la aceptación no arrancaba y el control `complexity` fallaba. Una prueba lo comprueba con
  la gramática de 3.9 y, donde hay un 3.9 instalado, importándolos.

## [0.16.0] - 2026-11-23

El contrato de arranque (ADR-0015): antes del primer run, `lictor doctor` dice lo que falta y lo que
los controles no ven. En `pyverifactu`, lo que vio a mano la revisión de la fase 4a lo dice ahora
`doctor`, más dos cosas que la pasada no vio.

### Añadido

- **`doctor` y cada run nuevo comprueban las dependencias de la verificación** (F66). La aceptación
  del perfil `python-uv` necesita `pytest-bdd`: sin él, el run se paraba en el rojo o en `L0-rutas`
  después de gastar la llamada del driver. Y cada programa que un control lanza con `uv run` tiene
  que estar en el entorno: un `ruff` fuera del lock hacía fallar `lint` en cada intento, y un
  `pytest` fuera del lock usaba el de la máquina, con otro intérprete.
- **«Lo que los controles no ven»** (F67, F70), en `doctor`, al empezar cada run y en `report.md`:
  tests que ningún control ejecuta, tests que solo ejecuta `lictor check`, tests o ficheros que
  carga el run y que el agente no puede cambiar (un `conftest.py` que importa el código, con los
  nombres que importa), y código que usa el proyecto y que ningún control lee. Lo lee un módulo del
  perfil (`lictor_survey.py`) sin importar ni ejecutar nada del proyecto; cuando no se puede saber qué
  ejecuta un control, lo dice en lugar de avisar en falso. No para nada.
- **`blind_spots.accepted` en `lictor.json`**: las rutas que la persona acepta que no se vean. Sus
  avisos solo se cuentan.
- **La skill de `python-uv` explica cómo adoptar un proyecto con código**, aviso por aviso.

### Cambiado

- **`lictor doctor` y `lictor run` salen con 10 cuando falta una dependencia de la verificación.**
  El código no es nuevo (es del proyecto), pero un proyecto así antes pasaba `doctor` con 0 y el run
  fallaba más tarde.

## [0.15.1] - 2026-11-23

La 0.15.0 no llegó a publicarse: la suite completa, que la publicación exige antes de subir nada, se
cortó dos veces en macOS a los 90 minutos sin ningún test fallado. Esta versión publica lo mismo que
la 0.15.0 (su sección, más abajo, dice qué cambia) con la suite completa arreglada.

### Corregido

- **La suite completa vuelve a repartir los `repro` entre los procesos.** Con el reparto por defecto
  de pytest-xdist cada proceso empieza con un bloque de tests seguidos, y los `repro` se recogen los
  primeros: cuando crecieron los tests unitarios, el bloque del primer proceso los abarcó todos, y en
  macOS se ejecutaban uno detrás de otro mientras los otros dos procesos esperaban. En diez días el
  trabajo pasó de 36 a más de 90 minutos. Ahora corre con `--dist worksteal`, y la suite completa
  tiene su propio `Presupuesto`, que avisa de un trabajo de más de 60 minutos: faltaba, y por eso no
  avisó nada.

## [0.15.0] - 2026-11-22 [NO PUBLICADA]

Lo que la primera adopción de un proyecto real (pyverifactu, 45 escenarios) mostró que fallaba: un
stub en `__init__` rechazaba el driver entero, y el refactor se descartaba por subir una o dos líneas
sin cubrir que no podía ver o por juntar supresiones que ya había. En la tanda siguiente, de cinco
refactors no se descartó ninguno (antes, 5 de 16): todos midieron con la medida nueva y dos
corrigieron a mitad una subida de cobertura.

### Corregido

- **Un stub en `__init__` ya no rechaza el driver entero** (F68, enmienda de la ADR-0012). Los
  impostores de stubs ponían `True` o un doble donde el stub lanzaba `NotImplementedError`, y un
  `__init__` que devuelve algo distinto de None lanza `TypeError` al construir el objeto: ningún
  test llegaba a su Entonces. Ahora cada constante pasa por `lictor_doubles.stub()`, que en un
  `__init__` devuelve None. En la primera pasada de adopción el agente había renunciado a las
  clases para esquivarlo.

- **El refactor sabe cuánto dejaba sin cubrir el verde** (F71, enmienda de la ADR-0010). En la
  primera adopción de un proyecto, 4 de 16 refactors se descartaron por una o dos líneas o ramas sin
  cubrir de más, sin poder verlo. El prompt da ahora las del verde y qué hacer si suben, y
  `python-uv` mide con `lictor_coverage.py`, que cuenta como Lictor y dice en qué funciones sube.
  La guarda no cambia.
- **Mover o juntar supresiones ya no descarta** (F72). `L0-supresiones` exige, al implementador y al
  refactor, que las supresiones de lo añadido quepan en las de lo quitado, por su texto exacto: un
  refactor que juntó tres `# noqa` en uno se descartaba. El driver fresco sigue igual.

### Al actualizar desde la 0.14.0

- **Un run empezado con la 0.14.0 no se reanuda:** cambian las constantes del perfil, el
  `agent_run` de `coverage`, una regla de `agent_tools` y dos ficheros del entorno de verificación,
  que entran en la huella. Empieza uno nuevo con `lictor run --new`.
- **Un `lictor.json` que reescribe el `agent_run` de `coverage`** sigue con su orden, que no cuenta
  las ramas ni compara con el verde: pásala al módulo, por ejemplo
  `"uv run python -I -B {lictor_files}/lictor_coverage.py tests/unit -q"`.

## [0.14.0] - 2026-11-21

Un control de duplicación que mide en cada verificación sin bloquear, dice al refactor qué clones
dejó su verde y le impide dejar más código repetido (ADR-0014, F65). En tres pasadas de la calculadora acaba
sin ningún clon, y los dos que aparecieron los quitó el refactor a la primera.

### Al actualizar desde la 0.13.0

- **Un run empezado con la 0.13.0 no se reanuda:** el perfil cambia (un control, un fichero en el
  entorno de verificación, una regla de `agent_tools` y `refactor_hints`) y entra en la huella.
  Empieza uno nuevo con `lictor run --new`.

### Añadido

- **Control `duplication` en `python-uv`** (L1, `measure`). Lo mide un módulo del perfil,
  `lictor_duplication.py`, que lee `src` sin importarlo ni ejecutarlo y vale con Python 3.9 o
  posterior: no hay dependencia nueva. Un clon es un trozo de 50 tokens o más que se repite en dos
  sitios, con los nombres y los literales cambiados de forma coherente dentro de cada función; no
  cuentan los comentarios, el formato, las importaciones, las firmas, los docstrings ni las tablas
  (entradas de un `dict`, listas de campos o de argumentos). `report: "duplication"` es nuevo en
  `lictor.json`.
- **El refactor recibe los clones que dejó su verde**, estén en el fichero que estén, primero los de
  los ficheros que cambió el escenario, con los tokens en clones que no puede superar, o que no hay
  ninguno, y una orden para medirlo él.
- **`verify.finished` guarda `duplication`** (`clones`, `in_clones`, `code_tokens`), y `report.md`
  y `lictor check` lo dicen en una frase («2 clones, 250 de 1800 tokens en clones»).

### Cambiado

- **Un refactor que deja más tokens en clones que su verde se descarta** en `L0-regresión`. Ni el
  formato ni cambiar el nombre de una variable dentro de una función mueven el número.
- **`refactor_hints` pide compartir un recorrido repetido** (que una parte use el resultado de la
  otra, o una base común) en lugar de extraer trozos sueltos.
- **Con `mode: block`, el implementador sabe que no puede haber código repetido** y cómo medirlo.
- **El refactor mide la complejidad con la orden sola, antes de editar y al terminar.** En la
  pasada de la 0.13.0, dos refactors la encadenaron con `&&` tras la aceptación, que falla con
  escenarios pendientes, y no llegó a ejecutarse. Con la línea nueva, en dos pasadas más, ninguno la
  encadenó y 6 de los 7 aplicados midieron antes y después.

## [0.13.0] - 2026-11-21

Un control de complejidad que mide en cada verificación sin bloquear, dice al refactor qué
funciones dejó su verde por encima del límite y le impide dejar más (ADR-0013, F60).

### Al actualizar desde la 0.12.0

- **Un run empezado con la 0.12.0 no se reanuda:** el perfil cambia (un control, un fichero en el
  entorno de verificación, una regla de `agent_tools` y `refactor_hints`) y entra en la huella.
  Empieza uno nuevo con `lictor run --new`.

### Añadido

- **Control `complexity` en `python-uv`** (L1, `measure`, `limit: 10`). Lo mide un módulo del
  perfil, `lictor_complexity.py`, que lee `src` sin importarlo ni ejecutarlo: no hay dependencia
  nueva y existe en todo proyecto. La definición es fija y está en `refactor_hints`: 1, más cada
  `if`, `elif`, ternario, `for`, `while`, `except`, `case` (salvo un `case _` final), `for` o `if`
  de una comprensión y operando de un `and`/`or` tras el primero; las funciones anidadas cuentan
  aparte. Da lo mismo que radon y lizard en el código de las pasadas, y más que ruff `C901`, que no
  cuenta `and`/`or` ni ternarios. `report: "complexity"` y `limit` (el máximo por función, que un
  proyecto cambia por `id`) son nuevos en `lictor.json`.
- **El refactor recibe las funciones que su verde dejó por encima del límite**, estén en el fichero
  que estén, con su valor, o que no hay ninguna, y una orden para medirlo él
  (`{lictor_files}`, una copia del módulo fuera del proyecto durante la llamada).
- **`verify.finished` guarda `complexity`** (`limit`, `over`, `excess`, `peak`), y `report.md` y
  `lictor check` lo dicen en una frase («1 función supera 10 (la peor, 12)»).

### Cambiado

- **Un refactor que deja más complejidad por encima del límite que su verde se descarta** en
  `L0-regresión`: ni la suma de lo que superan el límite ni lo que lo supera la peor función pueden
  subir. Mejorar sin llegar al límite se acepta.
- **El prompt del refactor ya no dice «tienes un solo intento»:** dice qué descarta un refactor y
  que un descarte no le cuesta nada al escenario. El motivo de `L0-regresión` pasa a «El refactor
  deja peor que el verde lo que miden los controles».
- **Con `mode: block`, el implementador recibe el límite y la orden para medirlo.**

## [0.12.0] - 2026-11-21

Los impostores de stubs exigen una aserción de un Entonces, con dos dobles que dejan llegar al test
a ella (ADR-0012). El impostor de la interfaz altera fechas. Cambian veredictos y mensajes.

### Al actualizar desde la 0.11.0

- **Un run empezado con la 0.11.0 no se reanuda:** el perfil y sus plugins cambian y entran en la
  huella. Empieza uno nuevo con `lictor run --new`.

### Cambiado

- **Un test pendiente nuevo se acepta solo si alguna constante le hace fallar una aserción del
  driver en un Entonces.** Antes bastaba con que fallara de cualquier forma, y un test sin `assert`
  que leía `.total` de lo que devolvía un stub se sellaba (falla con `None` por `AttributeError`).
- **Dos dobles se añaden a `None` y `True`** (`python_uv/lictor_doubles.py`): a un atributo, un
  índice, una llamada o una operación responden con otro doble, y no son iguales a nada. `ONE` es
  verdadero y se itera una vez; `EMPTY` es falso y vacío. Son finitos: pasado cierto nivel, o
  demasiadas operaciones en un test, lanzan su propio error. En 347 tests pendientes de 37 drivers
  reales, sin ellos la regla nueva rechazaba 9 correctos; con ellos, ninguno.
- **Un test que no depende de lo que devuelven los stubs lo juzga el impostor de la interfaz**,
  sobre el árbol con los stubs en `return None` y sin envolverlos. Es el de una orden llamada desde
  un Dado (F64), que antes rechazaba el driver con un mensaje que empujaba a comprobar tipos en los
  Dados. Va también ahí un test que pasa con `None` y con `True`, que ningún doble distingue de
  `r in (None, True)`. Si la interfaz no encuentra nada que alterar, se acepta y se anota
  (`stub_unchecked` en `driver.sealed`, «Tests aceptados sin comprobar» en `report.md`).
- **Los mensajes de rechazo nombran cada test** y dicen qué pasó: una aserción fuera del Entonces,
  un valor con el que pasa, o el error que no le deja llegar a su aserción, con su paso y su línea.
  Ninguno pide comprobaciones en los Dados.
- **Formas correctas que ahora se rechazan, y se pueden reescribir:** convertir lo que devuelve la
  interfaz a otro tipo (`Decimal()`, `asdict`, `json.dumps`, una expresión regular), `x not in r`
  y desempaquetar el resultado. Al agotarse los intentos, el mensaje se lo dice a la persona.
- **Una ejecución de una constante que se cae no cuenta**, en lugar de rechazar el driver. Si se
  caen todas, sí lo rechaza.
- **El impostor de la interfaz altera fechas**, un día más o menos (parte de F62). Un escenario
  que ya se cumplía y cuyo Entonces compara una fecha deja de ir a la persona.
- **`oracle.impostors.replacements`** pasa a una lista de `{code, name, double}`, y la variable del
  impostor de la interfaz lleva `stubs`.
- **`lictor.driver.measured.v1`**: la medida de cada stub lleva su desenlace, lo que dijo la
  interfaz y el veredicto, en lugar de `rule_c`.

## [0.11.0] - 2026-11-20

El impostor de la interfaz altera las colecciones con elementos de su forma y por el principio
(enmienda de las colecciones de la ADR-0011). Cambian veredictos y el mensaje de `no_aserta`.

### Al actualizar desde la 0.10.0

- **Un run empezado con la 0.10.0 no se reanuda:** los plugins del perfil cambian y entran en la
  huella. Empieza uno nuevo con `lictor run --new`.

### Cambiado

- **Una lista o tupla cambia por el principio, con un elemento de su forma.** En `mas` se antepone
  el último, alterado si es un número, un texto o una colección; en `menos` se quita el primero. Un
  diccionario gana en `mas` una clave con un valor así. Antes el centinela entraba en la lista: un
  test correcto que leía un atributo de cada elemento fallaba por un error (`no_aserta`), y uno que
  leía el primero sobrevivía. En la biblioteca, los probados pasan de 136 a 162 de 187, los
  `no_aserta` de 13 a 1 y los que sobreviven de 14 a 0.
- **`x[-1]` ya no se prueba** con una lista de números o textos: `menos` quita el primero. Es un
  límite conocido, como el primer elemento de una lista de uno.
- **El mensaje de `no_aserta` dice qué pasó:** la excepción, el paso, el fichero y la línea, y el
  modo, y que el otro valor lo pone Lictor y el test no debe tratarlo aparte. Con solo el fichero,
  el agente real movía sus pasos fuera de él en vez de arreglar el test.
- **El centinela se llama `AlteredByLictor`**, para que un error que tropieza con él diga de dónde
  viene.

### Añadido

- **`lictor.line`**, la línea del último frame del fallo dentro del proyecto, en `lictor_outcome`.

## [0.10.0] - 2026-11-20

El impostor de la interfaz aplica la regla B (enmienda de la ADR-0011): solo un Entonces prueba que
un test comprueba lo que devuelve la interfaz. Cambian veredictos y mensajes al agente.

### Al actualizar desde la 0.9.0

- **Un run empezado con la 0.9.0 no se reanuda:** el plugin y las instrucciones del driver del
  perfil cambian y entran en la huella. Empieza uno nuevo con `lictor run --new`.

### Cambiado

- **Solo prueba una aserción de un Entonces**, o el `ImpostorError` en un Cuando o un Entonces. Una
  aserción de un Dado ya no prueba un escenario «ya se cumplía»: en la biblioteca, 13 de 33
  probados lo eran así sin mirar su Entonces (F63).
- **Lo que devuelve un Dado, un fixture o el código fuera de un paso no se altera.** Lictor pasa al
  plugin los pasos que altera (`steps`, sin valor por defecto) y comprueba que lo haya cumplido: un
  modo que alteró un Dado no prueba nada.
- **`sin_alterar` es exacto:** nada alterable desde el Cuando, o solo el `None` de una orden. Es
  final, con exit 10 a la primera si ningún otro escenario se puede arreglar. Antes el `None` de un
  Dado lo escondía y el test «sobrevivía», con un mensaje falso y un reintento en balde.
- **Mensajes al agente:**
  - `fuera_del_entonces`, nuevo y reintentable, dice en qué paso falló la aserción y que la
    comprobación va en un Entonces, también la de un error.
  - `no_llama` distingue al test que solo llama a la interfaz desde un Dado.
  - `sin_alterar` solo pide no cambiar ese test. Por qué no se pudo comprobar va a la persona, en el
    rechazo final o en el último intento, y la línea final ya no dice que el test esté mal.
- **Instrucciones del driver de `python-uv`:** los Dados solo preparan, el resultado lo obtienen el
  Cuando o el Entonces, y la comprobación va en un Entonces.
- **`lictor.driver.measured.v1`** ya no mide B, que es la regla: guarda por escenario lo que probó
  la interfaz (`outcome`), las llamadas y alteraciones por paso y el paso que falla. C sigue
  medido y sin aplicarse. `report.md` lo muestra en «Medidas del driver».

### Añadido

- **`lictor.interface.altered_none`**, los resultados alterados que eran `None`.

### Quitado

- **`lictor.interface.alterable.<paso>`** y los campos `rule_b`, `today`, `alterable` y `caught` de
  las medidas.

## [0.9.0] - 2026-11-19

Lictor anota como hechos lo que solo sacaba la sonda de la pasada de la biblioteca, y mide con
ellos lo que dirían las reglas que vienen (fase 2b, paso A). No cambia ningún veredicto ni ningún
mensaje al agente, pero añade un evento y propiedades del perfil, así que sube la versión menor.

### Al actualizar desde la 0.8.0

- **Un run empezado con la 0.8.0 no se reanuda:** los plugins del perfil cambian y entran en la
  huella. Empieza uno nuevo con `lictor run --new`.

### Añadido

- **El paso en que falla cada test.** `lictor_outcome` anota `lictor.step` (`given`, `when` o
  `then`, con «Y» y «Pero» resueltos) en toda ejecución de la aceptación, `pytest.fail` incluido.
  Un `xfail` lanzado en un paso no cuenta.
- **Las llamadas a la interfaz por paso.** `lictor_impostor` anota también
  `lictor.interface.calls.<paso>` y `lictor.interface.altered.<paso>`, y en el control
  `lictor.interface.alterable.<paso>`: lo que los dos modos podrían alterar, sin alterarlo. Un valor
  cuya propia aritmética lanza no cuenta como alterable y no rompe el control.
- **`lictor.driver.measured.v1`**, una vez por rojo de un driver nuevo, antes de sellarlo o de
  rechazarlo: cómo falló cada pendiente en el rojo, cómo le fue con cada constante de los
  impostores de stubs y qué haría con él la regla C (una aserción del driver en un Entonces), y lo
  que vio el impostor de la interfaz paso a paso con lo que diría la regla B (una aserción de un
  Entonces, o su error desde el Cuando). `report.md` lo muestra en «Medidas del driver».

## [0.8.0] - 2026-11-19

Un escenario nuevo que el código ya cumple se prueba con un impostor de la interfaz, en lugar de
pedir un registro a mano (ADR-0011, F58). Cambia lo que hace un run, así que sube la versión menor.

### Al actualizar desde la 0.7.1

- **Un run empezado con la 0.7.1 no se reanuda.** El perfil `python-uv@0.2` declara
  `oracle.interface_impostor`, un plugin más y `LICTOR_*` en `reserved`, que entran en la huella:
  la reanudación sale con 10. Empieza uno nuevo con `lictor run --new`.
- **Un `lictor.json` no puede fijar ni heredar una variable `LICTOR_*`:** son del perfil.

### Añadido

- **El impostor de la interfaz.** Con un driver nuevo, un escenario nuevo que ya pasa se prueba
  antes de rechazar el driver: el plugin `lictor_impostor` del perfil altera lo que la interfaz
  devuelve al driver (el número + 1 y − 1, el booleano al contrario, un texto o una colección con
  un elemento de más o de menos, la excepción tragada o cambiada por otra). Si en los dos modos el
  test falla por una aserción suya, el código ya cumplía el escenario: queda en verde sin llamar al
  agente y sin registro a mano, en un commit vacío con `Lictor-Collateral` y `Lictor-Proof:
  interfaz`. La aceptación corre tres veces más, solo cuando hay escenarios nuevos que pasan.
- `report.md` tiene una sección «Ya se cumplían» y marca esos escenarios «verde (ya se cumplía)»;
  `lictor status` y el progreso del run los cuentan aparte (`--json`: `greens.already_met`).
- `driver.sealed` lleva `already_met` y `stub_impostors` (cuántos stubs sustituyeron los
  impostores; 0 si no había ninguno y no se ejecutaron).

### Cambiado

- **El rechazo `sin_prueba` dice lo que vio el impostor:** que el test no llama a la interfaz, que
  sigue pasando con otro valor, que falla pero no por una aserción suya, o que lo que devuelve no
  se puede alterar. Ya no afirma que el test «pasa sin llegar a la interfaz» ni pide que falle
  mientras la interfaz no esté implementada. Cuando ningún reintento puede cambiarlo (un objeto que
  no se altera, una ejecución del impostor que cae), para con exit 10 a la primera y pide decidir
  con `lictor register`, en lugar de gastar el resto de intentos del driver.
- **Los impostores de stubs no se dan por superados sin stubs.** En un proyecto con código no había
  nada que sustituir y la comprobación pasaba sin probar nada; ahora no se ejecutan y el sello lo
  dice. Un test que falla por `NotImplementedError` sin ningún stub que sustituir se rechaza.
- El prompt del driver ya no dice que cada test tiene que fallar: si el código ya cumple el
  escenario, el test pasa y Lictor lo comprueba. Pide los valores del Gherkin, nunca de ejecutar el
  código.

### Limitaciones conocidas

- El impostor prueba que el test mira el resultado, no que lo compare con el valor del escenario:
  un driver que copia en el assert lo que devuelve el código lo supera aunque ese valor esté mal.

## [0.7.1] - 2026-11-19

Un arreglo de la 0.7.0: la guarda del refactor descartaba los refactors que solo quitaban código
cubierto. Sin campos nuevos en `lictor.json`, así que sube el parche.

### Corregido

- **`L0-regresión` ya no descarta un refactor que solo quita código cubierto.** La 0.7.0 comparaba
  el porcentaje de cobertura con el del verde, y quitar código que los tests ya ejecutaban lo baja
  aunque quede sin probar exactamente lo mismo: un refactor que simplificaba se descartaba por una
  décima. Ahora se compara lo que queda sin probar, en número: no más líneas ni ramas sin cubrir
  que el verde, y, para un control de mutación, no más mutantes probados sin matar. El prompt del
  refactor lo dice así, y el error nombra cuántas quedan sin cubrir en lugar del porcentaje.
- Un run de la 0.7.0 que se reanude con un refactor a medias no compara la cobertura de ese
  refactor: el checkpoint guardaba el porcentaje, que ya no se lee. Los casos JUnit sí se comparan.

## [0.7.0] - 2026-11-18

Un paso de refactor tras cada escenario implementado (ADR-0010): el «refactor» de
rojo-verde-refactor, con el mismo oráculo. `lictor.json` gana un campo opcional, `refactor`, así que
sube la versión menor.

### Al actualizar desde la 0.6.1

- **Un run empezado con la 0.6.1 no se reanuda.** El perfil `python-uv@0.2` declara ahora
  `refactor_hints` y la configuración resuelta incluye `refactor`, que entran en la huella: la
  reanudación sale con 10. Empieza uno nuevo con `lictor run --new`.
- **Cada escenario implementado cuesta una llamada y una verificación más.** Se apaga con
  `"refactor": {"enabled": false}` en `lictor.json`.

### Añadido

- **Refactor tras cada verde implementado.** Después del commit de un escenario que implementó el
  agente, una llamada aparte, con una copia nueva de HEAD, mejora el diseño sin cambiar el
  comportamiento, con los objetivos de calidad del perfil (`refactor_hints`). Se verifica como un
  intento, sin escenario propio: las puertas L0, el sello y los controles de cada intento, y todos
  los verdes tienen que seguir en verde. Si pasa, entra como un commit
  `refactor(<ID>): mejorar el código sin cambiar el comportamiento`, sin `Scenario:` y con
  `Lictor-Refactor-Of`; si no, se descarta y el verde se queda. Tiene un solo intento, no gasta los
  del escenario y nunca lo pone en cuarentena. Un colateral, un registro o un re-registro no tienen
  refactor, porque no cambian código.
- **`L0-regresión`: un refactor no puede bajar lo que mide la verificación.** Se descarta si un
  control JUnit pasa menos casos, o un control con puntuación puntúa menos, que en la verificación
  del verde. Así no puede borrar los unitarios, que pasan vacíos, ni bajar la cobertura, que en
  `python-uv` solo se mide.
- **Un pendiente que pasa tras un refactor queda en verde colateral**, encadenado como en un
  intento, y `report.md` dice de qué refactor salió.
- **El refactor se ve aparte.** `lictor run` dice en stderr cómo acaba cada uno (aplicado, sin
  cambios o descartado, y dónde falló), `lictor status` dice si hay uno en curso y separa su gasto
  (`--json` gana `refactor`), y `report.md` gana una columna «Refactor», una fila «Todos los
  refactors» y una sección «Refactors». El journal gana `lictor.scenario.refactored.v1`, y
  `verify.finished` y las llamadas al agente llevan el rol `refactor`. La evidencia va en
  `NNN-refactor.json` y `NNN-refactor-verify/`, junto al intento que dio el verde.

## [0.6.1] - 2026-11-18

Un arreglo de la 0.6.0: medir la cobertura, como la 0.6.0 pide al agente, paraba el run en el primer
escenario. Sin campos nuevos en `lictor.json`, así que sube el parche.

### Al actualizar desde la 0.6.0

- **Un run empezado con la 0.6.0 no se reanuda.** El `gitignore` del perfil `python-uv@0.2` nombra
  ahora `.coverage`, y entra en la huella de la configuración resuelta: la reanudación sale con 10.
  Empieza uno nuevo con `lictor run --new`. Un proyecto creado con `lictor init` de la 0.6.0 no
  necesita tocar su `.gitignore`: Lictor ya deja fuera `.coverage` por su cuenta.

### Corregido

- **Medir la cobertura ya no para el run en el primer escenario** (F59). La 0.6.0 pide al agente
  medirla con el `agent_run` del perfil, y pytest-cov deja `.coverage` en su copia. El `.gitignore`
  que escribía `lictor init` no lo nombraba, así que entraba en el cambio del agente: `L0-rutas` lo
  descartaba en cada intento y, al repetirse la ruta, el run salía con 10 diciendo que una persona
  tenía que cambiar `.coverage`. Ahora lo que generan las herramientas del perfil (su lista
  `gitignore`) se queda fuera del cambio del agente, diga lo que diga el `.gitignore` del proyecto,
  y `lictor init` añade `.coverage` y `.coverage.*`. Lo mismo vale para `lictor check` con cambios
  sin confirmar.

### Cambiado

- **El fichero global de ignorados del usuario (`~/.config/git/ignore`) ya no decide qué entra en
  el cambio del agente ni en lo que verifica `lictor check`.** Lo que se verifica no depende de la
  configuración de la máquina, como ya pasaba con el resto de la configuración de git.

## [0.6.0] - 2026-11-18

Dos arreglos de la pasada de referencia con la calculadora: un escenario que el agente cumplía ya
no acaba en cuarentena porque su respuesta nombrara el conflicto, y el agente sabe qué cobertura se
le exige y cómo medirla. `lictor.json` gana un campo opcional, `agent_run`, así que sube la versión
menor.

### Al actualizar desde la 0.5.0

- **Un run empezado con la 0.5.0 no se reanuda.** El `coverage` del perfil `python-uv@0.2` declara
  ahora `agent_run`, que entra en la huella de la configuración resuelta: la reanudación lo ve como
  otra configuración y sale con 10. Empieza uno nuevo con `lictor run --new`. Nada más cambia:
  `lictor.json` sigue cargando igual.

### Corregido

- **Un conflicto declarado ya no pone en cuarentena un escenario que su intento cumple.** El
  agente que terminaba con «LICTOR: SPEC_CONFLICT no aplica», diciendo que no había conflicto,
  mandaba el escenario a cuarentena sin verificar lo que había escrito, y el run salía con 11.
  Ahora el intento con un conflicto declarado se verifica igual: si pasa, el escenario queda en
  verde; si no, va a cuarentena por `spec_conflict`, como antes, sin gastar los intentos que
  quedan, y el motivo dice dónde falló. El prompt aclara que la línea solo se escribe para declarar
  un conflicto. El código de salida 11 no cambia de significado.
- **El agente sabe medir la cobertura que se le exige.** Con `{"id": "coverage", "min_score": 80}`,
  el agente no sabía qué mínimo había ni cómo medirlo, y la mayoría de los reintentos de un run
  eran por cobertura. Ahora el prompt de implementación dice el mínimo de cada control que el run
  verifica y la orden con la que medirlo, que entra en las que puede ejecutar.

### Añadido

- **`agent_run` en un control:** la orden con la que el agente mide ese control por su cuenta.
  Con `min_score`, el agente recibe el mínimo y esta orden; la verificación nunca la ejecuta. Una
  que escribe el proyecto y `agent_tools` no permite impide cargar `lictor.json`; una heredada del
  perfil bajo unas `agent_tools` más estrechas solo deja de decirse. El `coverage` de
  `python-uv@0.2` declara `uv run pytest tests/unit -q --cov=src --cov-branch
  --cov-report=term-missing`; un proyecto que cambia `adds` debería cambiar también `agent_run`.
  Cambia la huella de la configuración resuelta, así que un run empezado con la 0.5.0 no se
  reanuda.

## [0.5.0] - 2026-11-17

La skill de Lictor habla solo del perfil del proyecto, y lo esencial de ese perfil va al principio,
donde sobrevive a la compactación de la conversación. La verificación, los controles y `lictor.json`
no cambian.

### Al actualizar desde la 0.4.0

- **`lictor doctor` avisa (⚠) de que el esbozo de la skill no es el de esta versión.** Su
  descripción ya no dice qué hacer si la skill no carga, porque eso no sirve para decidir cuándo
  se activa, y el cuerpo del esbozo ya lo dice. Ejecuta `lictor skill` en el proyecto para
  actualizarlo.

### Cambiado

- **`lictor skill --show` habla solo del perfil del proyecto**, en vez de concatenar la parte de
  todos los perfiles. Es el que nombra `lictor.json`, aunque el fichero no cargue; sin él, el que
  detectaría `lictor init`; y si no hay ninguno, la skill lista los perfiles para que la persona
  elija y se lea el suyo con `--profile` (ver «Añadido»). Busca `lictor.json` subiendo hasta la raíz del repositorio, así que una
  sesión abierta en `src/` sigue hablando de su proyecto y nombra el directorio desde el que
  ejecutar `lictor`.
- **Lo esencial del perfil va al principio de la skill, y el detalle al final.** Al compactar la
  conversación, Claude Code solo conserva el principio de una skill (unos 5000 tokens). La parte
  del perfil empezaba hacia el token 5000 y quedaba entera fuera; ahora lo esencial (las
  dependencias antes del run, la CLI probada en el proceso, `min_score` y el momento de los
  controles añadidos) termina hacia el token 1000. La skill acaba con una línea fija, y si falta,
  la propia skill dice que vuelva a imprimirse con `lictor skill --show`.

### Añadido

- **`lictor skill --show --profile <nombre>`** imprime la skill de ese perfil, sea cual sea el del
  proyecto. Rechaza un perfil o una versión como `lictor init --profile`: uno desconocido, una
  versión retirada (`python-uv@0.1`) o una que no coincide salen con 10. Sin `--show`, sale con 10.

## [0.4.0] - 2026-11-16

Cada control dice cuándo corre: en cada intento de un run o solo en `lictor check`. Los tests
unitarios corren una vez por intento, y `coverage` puntúa esa misma ejecución. Los informes se leen
como hechos y los veredictos los da el dominio, lo que corrige varios casos en que un informe roto
pasaba por bueno.

### Al actualizar desde la 0.3.0

Lo que deja de funcionar, y qué hacer en cada caso:

- **`"profile": "python-uv@0.1"` sale con 10.** Cámbialo por `"python-uv@0.2"` en `lictor.json`.
  Es lo único que necesita un `lictor.json` escrito por `lictor init` de la 0.3.0, salvo que
  cambie `coverage` o `unit` como en el punto siguiente.
- **Dos cambios por `id` que valían en la 0.3.0 salen con 10**, porque `coverage` ya no ejecuta
  nada: lee la ejecución de `unit`. El mensaje dice cómo salir:
  - `timeout_s`, `empty_exit_codes` o `when_exists` en `coverage`: escríbelos en `unit`.
  - `{"id": "unit", "enabled": false}` sin quitar `coverage`: quítalo también
    (`{"id": "coverage", "enabled": false}`) o dale a `coverage` un `run` propio.
- **El `fail_under` de `[tool.coverage.report]` deja de aplicarse en la verificación, y nada lo
  avisa.** El perfil pasa `--cov-fail-under=0`, porque si no haría fallar `unit` sin ningún test en
  rojo. Si tu proyecto bloqueaba con él, escribe el umbral como `min_score`:
  `{"id": "coverage", "mode": "block", "min_score": 80}`.
- **Una ejecución a medias de la 0.3.0 no se reanuda** (exit 10): la huella de la configuración
  cambia con el perfil y con `stage`. Termínala con la 0.3.0 antes de actualizar, o empieza otra
  con `lictor run --new`.
- **`acceptance.report` sale con 10**: la aceptación siempre lee un informe JUnit. Quítalo de
  `lictor.json`.
- **`when_plugin` exige un perfil con `plugins_command`**, también en un control que añade el
  proyecto: antes, sin él, el control se ejecutaba a ciegas. `python-uv` lo tiene, así que con este
  perfil no cambia nada.
- **Algunos informes que pasaban ahora fallan**, porque que pasaran era un defecto: un JUnit
  ausente o vacío, un informe de cobertura que no mide nada con `min_score`, una mutación sin
  mutantes probados y uno con recuentos imposibles. Están en «Corregido».
- **El journal escribe `lictor.verify.finished.v2`**, sin `control_durations`: cada control con lo
  que le pasó (`outcome`: omitido, no ejecutado o ejecutado, con veredicto o sin él si manipuló el
  estado) y la aceptación aparte. `report.md` cuenta los eventos `v1` como de una versión anterior
  y no los interpreta. Si algo tuyo lee el journal, tiene que leer el formato nuevo.
- **`lictor doctor` avisa (⚠) de que el esbozo de la skill no es el de esta versión.** El esbozo
  escribe ahora `allowed-tools` como una cadena separada por espacios, como pide el estándar Agent
  Skills, y no como una lista YAML. Ejecuta `lictor skill` en el proyecto para actualizarlo.

### Cambiado

- **`coverage` puntúa la ejecución de `unit`** (ADR-0008, perfil `python-uv@0.2`). Los tests
  unitarios corren una vez por intento: `coverage` es un control lector, con `reads: "unit"` y
  `adds`, los argumentos que `unit` recibe al final de su comando, y no ejecuta nada. Sin
  `pytest-cov` en el proyecto, `unit` corre tal cual y `coverage` sale omitido, como antes. Lo que
  cambia al juzgar:
  - **Un test que solo falla bajo cobertura bloquea**, porque rompe `unit`. El error que recibe el
    agente nombra el comando con que se ejecutó `unit`.
  - **Un test en rojo ya no bloquea a través de `coverage`.** Con `unit` en `measure` y `coverage`
    en `block`, `coverage` puntúa, marca la puntuación «(de un unit fallido)» y bloquea solo si no
    llega a su mínimo. Para que un test en rojo bloquee, `unit` va en `block`, como en el perfil.
  - **Una pasada sin tests, con código en `src`, puntúa un 0 %.**
  - `lictor check --control coverage` ejecuta también `unit` y lo dice; `--control unit` ejecuta
    el mismo `unit` que el run.
- **Todo comando se trocea al cargar.** Un `run`, un `adds` o un `acceptance.run` con comillas sin
  cerrar es un error de configuración que nombra el control (exit 10), no una excepción al
  ejecutarse.
- Al terminar un run en un worktree enlazado, el motivo final nombra también la ruta del worktree,
  además de la rama y de cómo integrarla.
- El hash del entorno de verificación y del sello recorre el árbol con `os.scandir`: da el mismo
  hash en la mitad de tiempo (de 0,4 s a 0,2 s en un entorno de 6499 ficheros).

### Añadido

- **Cada control dice cuándo corre: `"stage": "attempt"` o `"check"`** (ADR-0007). Con
  `attempt`, el valor por defecto, corre en cada intento de un run, como hasta ahora. Con `check`,
  el run no lo ejecuta y solo corre en `lictor check`: para lo que no cabe en un intento, como la
  mutación, o lo que el agente no puede cambiar, como la auditoría de dependencias. Se escribe por
  `id`, como `mode`, y vale con los dos modos y con `min_score`.
  - El run los nombra en «Controles solo en `lictor check`» de `report.md` (desde `run.started`,
    que los lista en `check_controls`) y, si guardó algún verde, en una frase del motivo final;
    en un worktree enlazado, con la ruta del worktree donde ejecutar `lictor check`, también si
    el run paró antes de su final.
  - `lictor check` los ejecuta con los demás y los marca «(solo en check)». `lictor doctor` busca
    sus programas y avisa (⚠) de que un run no los ejecuta, sin cambiar su código de salida; si
    falta el programa de uno, también es un aviso, porque un run empezaría igual. El
    `preflight` y la reanudación de un run solo buscan los programas de los controles `attempt`.
  - Con `{"id": "coverage", "stage": "check"}` la cobertura se mide solo en `check`, y la línea de
    `unit` lo marca: allí `unit` corre con los argumentos de cobertura.
  - Ningún control del perfil `python-uv` cambia de momento: todos van en cada intento. La skill
    explica cuándo proponer `check` y que entonces la CI del proyecto tiene que ejecutar
    `lictor check`.
  - Si `lictor check` falla después de un run, los escenarios siguen en verde: el arreglo es de la
    persona.
- **Todo log empieza por el comando que se ejecutó** (ADR-0009), citado como lo necesitaría una
  shell: los de los controles, la aceptación, el rojo, los impostores y el `prepare` del entorno.

### Corregido

- **Los informes se leen como hechos y los veredictos los da el dominio** (ADR-0009). Un informe
  que no se puede leer ya no hace fallar a Lictor con una excepción: es un informe no válido, con
  su motivo. Lo que se juzga es lo mismo que antes, salvo estos defectos:
  1. Un JUnit ausente, vacío, en un directorio o en un enlace roto ya no pasa como «sin casos»: con
     0 tests, pytest escribe un JUnit sin casos, así que su ausencia es que la suite murió antes.
  2. La codificación de un JUnit sigue las reglas de XML en todos los sitios: uno con BOM o con
     una codificación declarada se lee, y uno cuyos bytes no se pueden decodificar no es válido.
  3. El informe de la aceptación solo sale de `{out}`: el rojo y los impostores ya no leen la
     salida estándar ni una ruta escrita dentro del informe.
  4. La aceptación de `lictor check` juzga como la del run si se puede fiar de su informe; pasa y
     falla en los mismos casos que antes, con un motivo más preciso.
  5. Los motivos de una suite caída dicen lo que pasó (sin informe, con uno no válido, sin casos)
     sin repetir el error del lector. Llegan al agente.
  6. Un informe de cobertura que no mide nada (0 sentencias y 0 ramas) ya no puntúa un 100 %: no
     tiene puntuación, y con `min_score` falla.
  7. Un informe con recuentos imposibles (más líneas cubiertas que sentencias, más mutantes que el
     total, `{"totals": {}}`) no es válido. Antes daba porcentajes por encima del 100 %.
  8. Una mutación sin mutantes probados (todos omitidos) falla sin puntuación.
  9. Un informe de cobertura o de mutación que no es UTF-8 no es válido.
  10. Un comando cortado por su tiempo límite lo dice («Se cortó a los N s»), en vez de «código -9».
- `lictor status` escribe los rangos de IDs en el orden de los IDs, no en el del feature: una regla
  escrita al final con los IDs más bajos ya no parte un rango en tres
  (`DNI-001 a DNI-023, DNI-028 a DNI-032, DNI-024 a DNI-027` es ahora `DNI-001 a DNI-032`).

## [0.3.0] - 2026-11-11

Cada ejecución trabaja en su propio worktree y su propia rama, así que puedes seguir haciendo
commits mientras corre. `lictor.json` guarda solo lo que decides tú, la cobertura pasa a ser una
puntuación con mínimo, y la skill cruza lo decidido con los escenarios.

### Añadido

- **Cada ejecución trabaja en un worktree y una rama propios** (ADR-0005). `lictor run` desde tu
  checkout crea un worktree desde el último commit en `~/.lictor/worktrees/<repo>-<hash>/<nombre>`,
  con la rama `lictor/<nombre>`, y ejecuta allí el run entero. Tu checkout y tu rama no se tocan:
  puedes seguir trabajando y haciendo commits mientras corre. Al terminar, el motivo dice en qué rama
  está el resultado; integrarlo, con un merge o una pull request, lo haces tú.
  - `--branch <nombre>` elige el nombre; sin él, es el id de la ejecución. Una rama que ya existe se
    rechaza con 10, salvo que sea la de la ejecución que se reanuda.
  - Lo que no has confirmado no entra en la ejecución, y `run` lo avisa. Ya no hace falta un árbol
    limpio en tu checkout; `doctor` lo muestra como aviso (⚠). Dentro del worktree se sigue exigiendo.
  - `lictor run` desde tu checkout reanuda la ejecución que quedó a medias. Con varias, pide
    `--branch`; `--new` empieza otra a su lado, y así pueden correr varias a la vez.
  - `lictor status` desde tu checkout muestra cada ejecución con su rama y su worktree. Con
    `--json`, la salida es `{"runs": [...]}`.
  - La ubicación se cambia por máquina con la variable `LICTOR_HOME` (por defecto `~/.lictor`), y
    nunca puede estar en el directorio temporal: un reinicio borraría las ejecuciones a medias.
  - Dentro de un worktree enlazado, creado a mano o por otra herramienta, `run` trabaja en él tal
    cual.
- **`lictor retire <nombre>`** retira el worktree de una ejecución después de integrar su rama.
  Se niega con cambios sin confirmar o con una ejecución que se puede reanudar, y nunca borra la
  rama sin preguntar: con `--delete-branch`, o si contestas que sí en una terminal, y solo si ya
  está integrada (`git branch -d`).
- **`lictor status --branch <nombre>` y `lictor report --branch <nombre>`** muestran el estado y
  regeneran el informe de esa ejecución desde tu checkout.

### Quitado

- **La regla «no hagas commits durante el run»** desaparece del README y de la skill: la ejecución
  trabaja en su propio worktree. La guía rápida acaba integrando la rama `lictor/…`.

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
- **`lictor init` escribe solo lo que decides tú**: `version`, `profile`, `backend.model` y
  `hints`. Lo demás sale del perfil en cada carga, así que una mejora del perfil llega a tu proyecto
  sin editar `lictor.json`.
- **`controls` en `lictor.json` son cambios por `id` sobre los del perfil.** Con el `id` de uno del
  perfil cambian solo los campos que escribes, `"enabled": false` lo quita y otro `id` añade un
  control (con `level`, `run` y `report`). Un `lictor.json` con todo copiado, como los de la 0.2.0,
  sigue cargando igual y conserva su huella, así que lo que resuelve a lo mismo se reanuda. Dos
  diferencias: una lista vacía ya no quita los controles, y un control del perfil que borraste de
  la lista vuelve; quítalos con `"enabled": false`. Quitar un `id` que el perfil no tiene sale con
  10. `schema/lictor.schema.json` describe ahora el fichero que escribes. El perfil sigue siendo
  `python-uv@0.1`: no ha cambiado.
- `lictor init` no repite en `.gitignore` lo que ya dice otra línea: `.venv` cubre `.venv/`, y
  `*.py[oc]`, `*.pyc`.
- **La cobertura es una puntuación.** El control `coverage` de `python-uv` exporta el JSON de
  coverage.py (`--cov-report=json:{out}`) y el lector nuevo `coverage` guarda el porcentaje de
  líneas y ramas juntas. Se ve junto al resultado en `lictor check`, en `verify.finished` y en
  «Puntuaciones» de `report.md`, como el de mutación. `min_score` vale también para la cobertura:
  `{"controls": [{"id": "coverage", "min_score": 80}]}`. Un control con mínimo que no midió nada,
  como una pasada sin tests, falla. El control sigue en `measure` y el perfil sigue siendo
  `python-uv@0.1`. Un `lictor.json` con los controles copiados de la 0.2.0 conserva el `coverage`
  antiguo, sin puntuación: quita esa entrada de la lista para recibir el del perfil.
- Las puntuaciones se muestran cortadas a un decimal, sin redondear: un 94,18 % se ve como 94,1 %,
  y un 89,96 % ya no se lee como un 90 % que no alcanza.
- **La skill cruza lo decidido con los escenarios.** Antes de escribir `features/` nombra las
  decisiones aprobadas en la conversación que no tienen escenario y pregunta si añade uno o quedan
  a criterio del agente, porque lo que solo se habló no existe para Lictor. Para una entrada que se
  valida pregunta también por los formatos que existen pero no se aceptan, los espacios alrededor
  y el formato completo del mensaje de error. Al configurar ofrece un mínimo de cobertura que
  bloquee: en `measure` el agente no ve el resultado.
- **Una ejecución empezada con la 0.2.0 no se reanuda**: sale con 10 y lo dice, en vez de leer el
  ancla antigua como una manipulación. Hay que empezar de nuevo con `lictor run --new`.
- **`lictor doctor` con un `lictor.json` que no carga sigue con lo demás.** Lo marca como
  «✗ Configuración» y comprueba el repositorio, el árbol, los escenarios, la skill, la identidad y
  el directorio temporal; el ejecutor, los comandos y el entorno quedan «no comprobado». Sale con
  10, como antes, pero con la lista entera.
- **`lictor init` va antes que los escenarios.** La guía rápida del README empieza por `init`, que
  instala la skill que ayuda a redactarlos, y el «Siguiente paso» de `init` lo dice.
- Los prompts del driver y del implementador nombran las únicas órdenes que admite Bash, las
  `agent_commands` del perfil, y que para leer y editar están Read, Grep, Glob, Edit y Write.
- «Verdes colaterales» de `report.md` va ordenado por ID. El asunto de un commit que registra
  varios escenarios sin el agente los cuenta (`test: verde colateral, 11 escenarios`); los IDs
  siguen en los trailers `Scenario:`. `lictor status` agrupa en una línea los pendientes y en otra
  los verdes de antes de la ejecución, con rangos (`DNI-003 a DNI-027: pendientes`).

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

[Sin publicar]: https://github.com/jmorenobl/lictor/compare/v0.23.2...HEAD
[0.23.2]: https://github.com/jmorenobl/lictor/compare/v0.23.1...v0.23.2
[0.23.1]: https://github.com/jmorenobl/lictor/compare/v0.23.0...v0.23.1
[0.23.0]: https://github.com/jmorenobl/lictor/compare/v0.22.0...v0.23.0
[0.22.0]: https://github.com/jmorenobl/lictor/compare/v0.21.0...v0.22.0
[0.21.0]: https://github.com/jmorenobl/lictor/compare/v0.20.0...v0.21.0
[0.20.0]: https://github.com/jmorenobl/lictor/compare/v0.19.0...v0.20.0
[0.19.0]: https://github.com/jmorenobl/lictor/compare/v0.18.0...v0.19.0
[0.18.0]: https://github.com/jmorenobl/lictor/compare/v0.17.0...v0.18.0
[0.17.0]: https://github.com/jmorenobl/lictor/compare/v0.16.0...v0.17.0
[0.16.0]: https://github.com/jmorenobl/lictor/compare/v0.15.1...v0.16.0
[0.15.1]: https://github.com/jmorenobl/lictor/compare/v0.15.0...v0.15.1
[0.15.0]: https://github.com/jmorenobl/lictor/compare/v0.14.0...v0.15.0
[0.14.0]: https://github.com/jmorenobl/lictor/compare/v0.13.0...v0.14.0
[0.13.0]: https://github.com/jmorenobl/lictor/compare/v0.12.0...v0.13.0
[0.12.0]: https://github.com/jmorenobl/lictor/compare/v0.11.0...v0.12.0
[0.11.0]: https://github.com/jmorenobl/lictor/compare/v0.10.0...v0.11.0
[0.10.0]: https://github.com/jmorenobl/lictor/compare/v0.9.0...v0.10.0
[0.9.0]: https://github.com/jmorenobl/lictor/compare/v0.8.0...v0.9.0
[0.8.0]: https://github.com/jmorenobl/lictor/compare/v0.7.1...v0.8.0
[0.7.1]: https://github.com/jmorenobl/lictor/compare/v0.7.0...v0.7.1
[0.7.0]: https://github.com/jmorenobl/lictor/compare/v0.6.1...v0.7.0
[0.6.1]: https://github.com/jmorenobl/lictor/compare/v0.6.0...v0.6.1
[0.6.0]: https://github.com/jmorenobl/lictor/compare/v0.5.0...v0.6.0
[0.5.0]: https://github.com/jmorenobl/lictor/compare/v0.4.0...v0.5.0
[0.4.0]: https://github.com/jmorenobl/lictor/compare/v0.3.0...v0.4.0
[0.3.0]: https://github.com/jmorenobl/lictor/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/jmorenobl/lictor/compare/v0.1.1...v0.2.0
[0.1.1]: https://github.com/jmorenobl/lictor/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/jmorenobl/lictor/releases/tag/v0.1.0
