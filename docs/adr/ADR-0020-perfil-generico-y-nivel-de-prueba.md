# ADR-0020: Perfil genérico y nivel de prueba

## Estado
Aceptada (2026-11-27), en la 0.23.0. Sale del diseño `.audits/qa/0-23-0/diseno.md`, revisado por
Fable en `.audits/qa/0-23-0/revision-fable.md` (el bloqueante B1, corregido en su adenda por B-S1,
y los importantes I1 a I7). Decisiones D1 a D11 de Jorge, como las recomienda el diseño (§12).

## Contexto
Hasta la 0.22.0 había un solo perfil, `python-uv`, que obliga a `src/`, a `uv` y a pytest-bdd. Su
oráculo (los impostores de stubs, las clases de cada fallo, el enlace con el escenario y el
impostor de la interfaz, ADR-0011, 0012, 0016) es lo que hace que «0 falsos verdes» sea cierto en
las pasadas: todo él corre en el proceso bajo prueba, como plugins de pytest.

Fable llevó un perfil hecho a mano a Go y a Node con el agente simulado: los dos ciclos llegan a
verde, y también un test sin aserción (`reducido(100);`), que falla en el rojo porque la interfaz
no está implementada y pasa después con cualquier código. Es el falso verde del agente honesto, y
sin plugin no hay nada que lo vea. Dos cosas impedían además cargar un perfil así: `code_paths`
era solo del perfil, y `paths` y `acceptance` eran obligatorios en él.

## Decisión

1. **El nivel de prueba es una función pura del dominio sobre las defensas resueltas**
   (`domain/proof_level.py`, `proof_level`). Responde a una pregunta: ¿puede un test que no
   compara con el escenario acabar en verde?

   | Nivel | Condición | Qué no comprueba |
   |:--|:--|:--|
   | **resultado probado** | `impostors`, `red_kinds`, `link: "scenario"` e `interface_impostor` | «No se comprueba que el test compare con el valor del escenario: un test que copia en el assert lo que devuelve el código pasa» (fase 2c) |
   | **básico** | cualquier otra combinación | «No se comprueba que el test compare nada: uno sin aserción falla en el rojo porque la interfaz no está implementada, y pasa después con cualquier código» |

   Una combinación parcial es básica: se dice de menos, nunca de más. Lo garantizado por
   «resultado probado» es de cada test que Lictor comprobó; las excepciones ya se listan aparte
   («Tests aceptados sin comprobar», un driver sin stubs, los registrados a mano). El nivel es del
   run, no del commit: no va en ningún trailer (D2). **Sitio reservado:** la 0.24.0 añadirá un
   nivel intermedio con su propia condición, la del impostor básico de dos constantes, sin código
   hasta entonces (D1).
2. **Los huecos que Lictor sabe calcular**, cada uno con un código estable y su frase (`Gap`):
   `rojo_sin_clase`, `enlace_por_nombre`, `ya_cumplido_sin_prueba`, `valor_sin_peso`,
   `entorno_sin_hash`, `sin_supresiones` (las dos listas vacías, que es cuando la puerta se
   apaga), `sin_controles`, `sin_lectura_de_controles`, `control_sin_plugin` (un control `attempt`
   cuyo plugin el entorno no carga: así vale igual para un `python-uv` sin `pytest-cov`) y
   `plugins_sin_comprobar` (sin el entorno no se sabe). La frase del nivel no se repite como hueco.
3. **Se dice en cinco sitios**, y en ninguno cambia el código de salida (D3): `doctor` («Nivel de
   prueba», tras «Los controles pasan en HEAD»: ⚠ con los huecos, ✓ sin ninguno), `run.started`
   (`proof: {level, gaps}`, con códigos: la frase la saca el dominio, así que un diario viejo se
   lee con la de hoy), el progreso al empezar, `status` y `report.md` (en «Resumen», y «Lo que
   este nivel no comprueba» justo después).
4. **La configuración, en tres clases.** El proyecto escribe lo que el perfil no declara, y solo
   añade a lo que declara:

   | Clase | Campos | Regla |
   |:--|:--|:--|
   | se suma | `suppressions`, `banned_patterns`, `forbidden_paths`, `code_paths`, y en `verification` `env`, `inherit` y `prepare_inherit` | perfil + proyecto, en ese orden y sin duplicados: una más solo rechaza más |
   | exclusiva | `verification.prepare`, `acceptance.driver_hints`, `code_exclude` | el proyecto la escribe solo si el perfil no la declara (`code_exclude`: si no declara `code_paths`); si no, exit 10 con su mensaje |
   | solo del perfil | el oráculo, `survey`, `acceptance.needs_plugins`, `verification.files`, `plugins`, `plugins_command`, `plugin_variable`, `reserved`, `prepare_ask`, `prepare_home_files`, `driver_exempt_paths`, `refactor_hints`, `rule_files`, `hint_values` | nunca la escribe el proyecto, aunque el perfil no la declare |

   Lo que decide la persona (`paths`, `acceptance`, `controls`, `agent_tools`, `hints`,
   `gitignore`…) no son defensas y sigue como estaba. La huella no cambia de forma: ya incluye la
   configuración y las defensas resueltas.
5. **`paths`, `acceptance` y `detect` son opcionales en el perfil.** Si el perfil no declara
   `paths`, `acceptance` o `code_paths` y `lictor.json` tampoco, la carga sale con 10 y un mensaje
   que los nombra todos de una vez (`missing_project_fields_message`). Un perfil sin `detect` no
   se detecta nunca (`all` de nada valía para cualquier proyecto). `init --profile generic`
   escribe las cuatro claves de siempre y dice en stdout lo que falta, `agent_tools` incluido;
   nunca valores de ejemplo, que ejecutarían un comando inventado (D4).
6. **`generic@0.1`, empaquetado y `alpha`**: lo universal y nada más. Sin oráculo, sin `prepare`,
   sin supresiones, sin controles; la lista de `forbidden_paths` de herramientas de agentes y
   editores, y unos `refactor_hints` neutros. Su skill da las recetas de Go, de Node y de Python
   sin `uv` o sin `src/`. Sigue en `alpha` hasta la 0.24.0 (D11).
7. **Un perfil no puede declarar `oracle.impostors` sin `red_kinds`**: Fable midió que sin las
   propiedades del plugin el impostor de stubs rechaza también los tests correctos.
8. **El stub falla con una señal fuera del dominio de la función, en todos los perfiles, y nunca
   devuelve un valor.** La orden del driver lo dice con una sola frase: el fallo del stub solo
   tumba su test, y si en el lenguaje para el proceso de tests (Go `panic`, `process.exit`) el
   driver lo recoge en cada test, con su traza; un test que espera un error comprueba cuál. Una
   constante es un impostor, no un stub: con dos escenarios de signo contrario (NIF válido y no
   válido) ninguna los hace fallar a los dos, y el driver no se sellaba nunca (B-S1, ejecutado). La
   forma de cada lenguaje la dan los `driver_hints`; en Go, un `defer` con `recover` en cada test,
   cada `t.Run` y cada goroutine, que no oculta la traza ni dice «no implementado».

## Consecuencias
- «0 falsos verdes» es una cifra de `python-uv`, con resultado probado, no de Lictor: el nivel se
  dice en cada informe.
- **La 0.23.0 sale con el falso verde del test vacuo declarado** como hueco del nivel básico
  (D11). `generic.skill.md` pide a la persona que lea una vez el driver sellado, el único sitio
  donde se ve. Lo cierra el impostor básico de la 0.24.0: dos constantes distintas por tipo, y se
  acepta el test que falla con el stub y con al menos una (I-S5).
- Hay dos regímenes de perfil con una sola regla, y la huella de `python-uv` cambia (sin
  compatibilidad hacia atrás).
- Lo que engaña sin plugin se calla o se dice: la tabla del rojo da el estado del JUnit, «Tests
  aceptados sin comprobar» dice que el perfil no los comprueba, «Cambios que ningún control
  ejecutó» dice por qué no se sabe, el refactor solo oye que los tests lo guardan si un control
  los cuenta, y un informe sin casos de ningún escenario trae la salida de la aceptación (el error
  del compilador en Go).

## Cómo se comprueba
- La tabla de `test_proof_level.py` (`python-uv` → resultado probado sin huecos; `generic` →
  básico; un control sin plugin; cada pieza del oráculo que falta).
- La regla de configuración, campo a campo, en `test_config_classes.py`.
- Los casos `repro` con la marca `toolchain`: Go y Node a verde con el agente simulado, los stubs
  con `panic` recogido, el olvido del `defer`, el driver que no compila y el entorno de Node con
  hash; y el test vacuo de Go como `@known_limitation`, un `xfail` estricto hasta la 0.24.0.

## Enmienda de la 0.24.0 (2026-11-28): el nivel intermedio

El sitio reservado lo ocupa **«resultado leído»** (`resultado_leido`), con un impostor de stubs (la
regla C, o las constantes que declara el driver, ADR-0021) y sin el oráculo entero. Qué no
comprueba: «que el test compare con el valor del escenario: uno que copia lo que devuelve el
código, o cuya comparación es más débil que la igualdad y las dos constantes rompen, pasa. Un test
que no consta que llegue a un stub se acepta sin comprobar, y se dice». «Resultado probado» sigue
exigiendo la regla C. `generic@0.2` declara el impostor, así que su nivel es «resultado leído»;
`generic@0.1` queda retirado. El falso verde declarado de D11 se cierra para los tests que llegan
a un stub.
