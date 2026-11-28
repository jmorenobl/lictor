Para cualquier lenguaje que deje un informe JUnit (Go, Node, Python sin `uv` o sin `src/`): perfil `alpha`, nunca se detecta, `lictor init --profile generic`.

- **El nivel de prueba es «resultado leído».** Lictor comprueba que el test de cada escenario
  falla antes del código y pasa después, en un clon limpio y con el driver sellado, y que **mira
  lo que devuelve la interfaz**: corre la aceptación con las dos constantes que el driver escribe
  en cada stub, y rechaza el test que pasa con las dos (el vacuo, el que solo mira que no está
  vacío). No comprueba que compare con el valor del escenario: un test que copia lo que devuelve
  el código pasa. Un test que no llega a un stub, o un driver sin stubs, se acepta sin comprobar,
  y el sellado lo avisa con ⚠. Díselo a la persona antes del primer run, y pídele que **lea una
  vez el driver sellado** (el commit «chore(lictor): driver de aceptación»). Mete en
  `acceptance.driver_hints` «cada test compara con el valor del Gherkin».
- **La persona escribe lo que el perfil no sabe**, con tu ayuda y su aprobación: `paths`,
  `acceptance` (la orden que deja un JUnit en `{out}`, y `failure_exit_codes`, los códigos que
  significan «fallaron tests»), `code_paths` (dónde está el código, dentro de `paths.writable`) y
  `agent_tools` (lo que el agente puede ejecutar: la aceptación y cada control, con la misma
  orden; si no, se le caen de la lista y `doctor` avisa en «Órdenes del agente»).
- **Los stubs fallan con el texto `lictor: no implementado` y llevan sus constantes**, con la
  firma del código terminado. La línea que falla va sola dentro de su función, y acaba con un
  comentario `lictor-impostor: <A> | <B>`: dos sentencias que la sustituyen devolviendo dos
  valores distintos de su tipo, **ninguno su cero** salvo en un booleano (1 y 2, `"a"` y `"b"`,
  una lista de uno y otra de dos, una estructura con cada campo lleno), nunca nulos, y que solo
  usan lo que ya usa la firma; una sola (`return`) si la función no devuelve nada. Si el fallo
  para el proceso de tests (un `panic` de Go, `process.exit`), el driver lo recoge en cada test.
  Un test que espera un error comprueba **cuál** (su clase o su mensaje). La forma de cada
  lenguaje va en `acceptance.driver_hints` (recetas, abajo).
- **Un escenario pide un valor con el que comparar.** Uno que solo pide que haya resultado («recibe
  un identificador», «el saldo es positivo») pasa con las dos constantes y su driver no se sella:
  el run para y pide un valor concreto en el escenario.
- **El nombre del test lleva el ID con un borde**: `Test_IVA_001`, `"IVA-001 …"`. Ni
  `TestIVA_001` ni un subtest con el ID.
- **Las supresiones del lenguaje** van en `suppressions` (texto, lo lee el agente) y en
  `banned_patterns` (regex, las rechaza la puerta). Sin ninguna, no se rechaza ninguna.
- **Lo que instala dependencias** (`go.mod`, `go.sum`, `package.json`, el lockfile, `.npmrc`) va en
  `paths.protected` y en `forbidden_paths`.
- **`gitignore` por cadena** (`node_modules/`, `coverage/`, `__pycache__/`): lo que deje una orden
  del agente en su copia no entra en el candidato.
- **En la raíz no se crean ficheros**: `paths.writable: ["."]` solapa con `protected`. Sí se
  pueden listar ficheros que ya existen (`"writable": ["main.go", "iva/"]`). En Go, el paquete va
  en un subdirectorio (`internal/`, `pkg/`, `iva/`).
- **Tests junto al código** (Go, Node): `code_exclude` con sus globs (`["*_test.go"]`), o el
  refactor que toque uno se descarta en `L0-alcance`.
- **Adoptar un proyecto con código:** Lictor no prueba un escenario que el código ya cumple: el
  run para y lo nombra. Regístralo antes con `lictor register <ID>…`.

<!-- lictor: detalle -->

**Lo que `lictor doctor` dice con este perfil.** «Nivel de prueba: resultado leído», con un ⚠ por cada
hueco: cualquier fallo cuenta como rojo (también uno de compilación), el test se empareja por su
nombre, un escenario ya cumplido no se prueba, el entorno no se vigila si no hay `prepare`, ninguna
supresión se rechaza si no las escribes, y sin controles un verde solo pasó la aceptación.
«Lo que los controles no ven» y «Cambios que ningún control ejecutó» no se calculan: el perfil no
sabe leer las órdenes de tu cadena. Son hechos, no errores: no cambian el código de salida.

**`lictor.json` de ejemplo (Go):**

```json
{
  "version": "0.1",
  "profile": "generic@0.2",
  "backend": {"model": "…"},
  "hints": "Go 1.26. El paquete iva/ expone Calcular(base float64) float64.",
  "paths": {
    "writable": ["iva/"],
    "driver": ["acceptance/"],
    "protected": ["features/", "acceptance/", "lictor.json", "go.mod", "go.sum"]
  },
  "code_paths": ["iva/"],
  "code_exclude": ["*_test.go"],
  "acceptance": {
    "run": "gotestsum --junitfile {out} -- ./acceptance/...",
    "failure_exit_codes": [1],
    "driver_hints": "…(receta de Go, abajo)…"
  },
  "controls": [
    {"id": "vet", "level": "L1", "run": "go vet ./...", "report": "exit-code"},
    {"id": "unit", "level": "L2", "run": "gotestsum --junitfile {out} -- ./iva/...", "report": "junit", "empty_exit_codes": [0]}
  ],
  "agent_tools": ["Bash(go test:*)", "Bash(go vet:*)", "Bash(gotestsum:*)"],
  "suppressions": ["//nolint"],
  "banned_patterns": ["//\\s*nolint", "\\bt\\.Skip(Now|f)?\\("],
  "forbidden_paths": ["go.mod", "go.sum"],
  "verification": {
    "env": {"GOCACHE": "{env_dir}/gocache", "GOMODCACHE": "{env_dir}/gomod", "GOPATH": "{env_dir}/gopath", "GOTOOLCHAIN": "local", "GOFLAGS": "-modcacherw"}
  }
}
```

**Go:**

- Aceptación: `gotestsum --junitfile {out} -- ./acceptance/...`, `failure_exit_codes: [1]`. Un
  driver que no compila deja un único caso del paquete: el reintento recibe el error del
  compilador.
- Entorno **sin `prepare`**: las cachés de Go cambian en cada compilación, y con `prepare` el hash
  del entorno fallaría en cada intento (`L0-entorno`). `GOCACHE`, `GOMODCACHE` y `GOPATH` van a
  `{env_dir}` en `verification.env`: heredarlas no basta, porque Go las saca de `HOME`, que es un
  directorio vacío por orden. `GOFLAGS=-modcacherw`: sin él Go deja la caché de módulos de solo
  lectura, y `git worktree remove` no puede borrar el worktree al retirarlo (solo vale para lo que
  se extrae desde entonces). Si el proyecto ya usa `GOFLAGS`, se juntan: `-mod=mod -modcacherw`.
- `driver_hints`: «Un test por escenario llamado `Test_<ID>` (con guion bajo, `Test_IVA_001`) en
  `acceptance/`. Los stubs hacen `panic("lictor: no implementado") // lictor-impostor: return 1 | return 2`,
  con la llave de la función en otra línea (gofmt la deja así). Un `error` junto a un resultado
  va `nil` en las dos constantes; una función que solo devuelve `error` usa dos centinelas del
  paquete (`var ErrX = errors.New(…)`), nunca un `errors.New` en la constante, porque el import
  no compilaría en el rojo o en la variante. Cada test empieza con `defer
  enEscenario(t)`, también dentro de cada goroutine que llame a la interfaz (ahí con
  `t.Errorf`). Sin `t.Run`: cada subtest sale en el informe como otro caso con el mismo ID, y un
  escenario es un solo caso; las filas de una tabla del Gherkin se recorren con un bucle dentro del
  mismo test, y el mensaje de cada fallo nombra la fila. El helper es `func enEscenario(t
  *testing.T) { if r := recover(); r != nil { t.Fatalf("pánico en el escenario: %v\n%s", r, debug.Stack()) } }`, sin `t.Helper()`. Cada
  test compara con el valor del Gherkin.» Con varios paquetes de aceptación, cada uno lleva su
  helper. No vetes `recover(`: el driver lo necesita.
- Una función que devuelve `error` por su naturaleza tiene un stub que hace `panic` igual: si
  devolviera un error de «no implementado», un escenario que espera un error pasaría en el rojo.
- La línea del stub con una estructura entera es larga: si el lint del proyecto mide el largo de
  línea, ese control lleva `"driver": false`, porque el commit del driver pasa los controles que
  bloquean antes de sellarse.
- Controles: `go vet ./...` (L1) y los tests unitarios con `gotestsum` (L2).

**Node:**

- Aceptación: `node --test --test-reporter=junit --test-reporter-destination={out} acceptance/*.test.js`
  (el patrón, no el directorio: con un directorio, Node 24 da un único caso y el driver se rechaza).
  Los stubs lanzan `new Error("lictor: no implementado"); // lictor-impostor: return 1; | return 2;`,
  que solo tumba su test. Coma flotante:
  `100 * 1.1 !== 110`; el test compara con un margen o en céntimos.
- Con dependencias, `prepare` las instala en el entorno:
  `sh -c 'npm ci --ignore-scripts && mkdir -p {env_dir} && mv node_modules {env_dir}/node_modules'`,
  con `env` `LNM={env_dir}/node_modules`; el entorno lleva hash y se vigila en cada orden,
  y la aceptación enlaza `node_modules` antes de correr:
  `sh -c 'ln -sfn "$LNM" node_modules && exec node --test --test-reporter=junit --test-reporter-destination="$0" acceptance/*.test.js' {out}`.
  El enlace a mano es la parte frágil. **La copia del agente no tiene `node_modules`, y permitir
  `sh -c` lo permite todo: con dependencias, el agente trabaja a ciegas** hasta una versión
  posterior.
- `banned_patterns`: `\.(skip|only)\(`, `@ts-(ignore|nocheck)`, `eslint-disable`. `code_exclude`:
  `["*.test.js"]` si los unitarios van junto al código.

**Python sin `uv` o sin `src/`:**

- Sin entorno propio: `python -m pytest tests/acceptance --junitxml {out}`, con el Python y las
  dependencias de la máquina (`entorno_sin_hash`). El test se empareja por su nombre
  (`test_iva_001`), sin pytest-bdd enlazado. Para `src/` con `uv`, mejor `python-uv`.
- Con entorno propio: `prepare` pasa por `shlex` y no por una shell, así que va en `sh -c
  'python -m venv {env_dir} && {env_dir}/bin/pip install -r requirements.txt'`. `{env_dir}` solo se
  sustituye en `verification.env`, y `PATH` es del perfil: las órdenes lo usan por una variable,
  `env` `PY={env_dir}/bin/python` y `PYTHONDONTWRITEBYTECODE=1` (si no, el bytecode cambia el hash),
  y la aceptación es `sh -c '"$PY" -m pytest tests/acceptance --junitxml "$0"' {out}`. Sin
  `"$PY"`, el `python` del `PATH` no es el del entorno, y los tests corren fuera de él. (Receta sin
  medir: la pasada de Go no la cubre.)
- Stubs: `raise NotImplementedError("lictor: no implementado")  # lictor-impostor: return 1 | return 2`.
- `banned_patterns`: `#\s*noqa`, `#\s*type:\s*ignore`, `pytest\.(skip|xfail)\(`.

**Lo que este perfil aún no hace:** probar un escenario que el código ya cumple, saber qué ve cada
control y comprobar que un test compara con el valor del escenario (no solo que mira el
resultado). La lectura del driver sellado sigue siendo de la persona.
