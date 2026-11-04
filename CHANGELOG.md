# Registro de cambios

Todos los cambios relevantes de Lictor se anotan aquí. El formato sigue
[Keep a Changelog](https://keepachangelog.com/es-ES/1.1.0/) y el proyecto usa
[versionado semántico](https://semver.org/lang/es/). Los códigos de salida son API pública: un cambio
en su significado se anota en «Cambiado».

## [Sin publicar]

Primera versión, la 0.1.0, todavía sin etiquetar. Incluye la implementación inicial y el plan de
corrección que siguió a una auditoría de la base (#1 a #6).

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

### Cambiado

- **El verde se decide sobre lo que se commitea.** El agente trabaja en una copia sin `.git` ni
  `.lictor/`. Su resultado se lee como un árbol, y se verifica un commit candidato en un clon limpio,
  en un entorno de verificación comprobado por hash y sin configuración de usuario. La rama solo
  avanza a ese commit (#1, #2).
- El agente se ejecuta sin la configuración, los hooks ni el `CLAUDE.md` del usuario o del
  repositorio (#1).
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
