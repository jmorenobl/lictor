Lictor implementa escenarios Gherkin con un agente y los verifica de forma determinista. Tú
ayudas a la persona antes y después de un run. **La persona decide el contrato; Lictor escribe
los tests y el código.**

## Límites

- No escribas tests, ni el driver (`paths.driver` de `lictor.json`), ni código de la aplicación.
  Si la persona lo pide, explica que el driver lo escribe y lo sella Lictor, y que si el código
  ya existe y cumple escenarios, lo que toca es `lictor register`.
- No lances `lictor run`: es largo, gasta cuota y la persona tiene que verlo. Dale el comando.
- No hagas commits ni lances `lictor register`: propón el comando y explica qué afirma.
- No toques `.lictor/`. No escribas ni cambies `features/` ni `lictor.json` sin enseñar antes el
  texto y que la persona lo apruebe.
- Lo que se puede comprobar sin un modelo lo comprueba `lictor doctor`: lánzalo en vez de validar
  a mano.

## 1. Preparar el proyecto

Sigue la sección del perfil del proyecto, al final. Vale para cualquier perfil:

- Lo que el agente no puede tocar se decide antes del run: las dependencias, el manifiesto y el
  lockfile del proyecto, la pipeline. Esas rutas solo las cambia una persona; si un escenario solo
  se cumple cambiando una, el run para con exit 10 y la nombra.
- La interfaz pública que fijes ahora (módulo, función, firma) va luego en `hints`.

## 2. Redactar los escenarios con la persona

Propón, pregunta y **espera a que apruebe** antes de escribir el fichero.

Lo que Lictor exige, y `lictor doctor` comprueba:

- `features/<nombre>.feature`, con `# language: es` en la primera línea si va en español.
- Una etiqueta `@PREFIJO-NNN` por escenario, única en todo `features/`, y ninguna en la
  característica ni en las reglas.
- Sin `Esquema del escenario` (`Scenario Outline`): un valor por escenario, o una tabla dentro de
  un escenario.

Lo que funciona:

- Los valores literales entre comillas en los pasos.
- Un comportamiento nuevo por escenario. Los que el mismo código ya cumple salen en verde sin
  llamar al agente (verdes colaterales): el coste lo marcan los escenarios que introducen algo
  nuevo, no su número, así que no ahorres escenarios por coste.
- Un rechazo comprueba el código de salida o la excepción, y un fragmento fijo del mensaje.
- Los límites: el menor y el mayor valor válidos, y el primero inválido de cada lado.

**Comportamiento o interfaz:**

- Lo que debe cumplirse es un escenario. Es lo único que Lictor verifica.
- Dónde vive cada función, su firma y cómo se llama desde un test es interfaz, y va en `hints`.
  `hints` llega a los prompts como contexto y nadie lo verifica: una regla escrita solo ahí
  («nunca termina el proceso») puede incumplirse sin que nada lo detecte. Si importa, que sea un
  escenario.
- En el Gherkin no va interfaz: ni módulos ni funciones.

**Lo que no es un escenario:** si algo solo se cumple cambiando una ruta que solo puede tocar una
persona (una pipeline, una dependencia, la configuración de una herramienta), díselo:
«eso es configuración tuya, no un escenario». Lictor no puede verificarlo y el run pararía con
exit 10. Que lo haga la persona antes del run.

**Pregunta por los huecos.** Cada caso sin escenario lo decide el agente: díselo así a la
persona, y en cada pregunta da tu recomendación y deja que decida ella.

- Toda entrada: mayúsculas y minúsculas, espacios alrededor, vacía; y el valor que es inválido
  por dos motivos (un número romano `MMMM`: ¿fuera de rango o mal formado?).
- Números escritos como texto: ceros a la izquierda (`01`), signo (`+7`, `-7`), espacios (`" 7"`),
  decimales y lo que no es un número. Las conversiones del lenguaje suelen aceptar más de lo que la
  persona espera.
- Una CLI: el código de salida de cada error y el formato completo del mensaje de error (qué
  stream, qué texto fijo, en qué idioma). La biblioteca de argumentos añade el suyo: argparse
  antepone un `usage:` en inglés, y el resultado mezcla idiomas si nadie lo decide. Qué hacen
  `--help`, una orden desconocida y los argumentos de más o de menos.
- Una entrada que se valida o se convierte: los prefijos o formatos que existen pero no se aceptan
  (un documento de otro tipo, una variante antigua del formato), y los espacios alrededor, que la
  shell no produce pero `main(argv)` o una librería sí reciben.
- Una librería: qué excepción y con qué mensaje; los tipos no válidos.

**Antes de escribir `features/`**, cruza cada decisión aprobada en la conversación con los
escenarios del borrador, y nombra las que no tienen ninguno: «Ñ, O y U no tienen escenario: añado
uno o quedan a criterio del agente». Lo que solo se decidió hablando no existe para Lictor: si no
hay escenario, el agente puede decidir lo contrario, y la verificación no lo ve. Que la persona
elija para cada una, y que quedarse a criterio del agente sea una decisión suya, dicha.

Después de escribir: `lictor doctor`, que en «Escenarios» valida los features. Arregla con la
persona lo marcado con ✗; un ⚠ (un fichero vacío, una característica sin escenarios) no impide
ejecutar, y el run lo salta.

## 3. Configurar

1. `lictor init`, si no está hecho: escribe `lictor.json`, añade `.lictor/` al `.gitignore` e
   instala el esbozo de esta skill en `.claude/skills/lictor/`. Imprime lo que decidió: el perfil,
   el modelo, el `hints` que escribió y lo que añadió al `.gitignore`. Revisa con la persona:
   - `backend.model`: el modelo que usará el agente.
   - `hints`: ajústalo al proyecto. Dónde vive el código, la interfaz y cómo se prueba. Lo que la
     persona quiera decirle al agente va aquí.
   - No propongas añadir `driver_hints` a `lictor.json`: es del perfil, Lictor rechaza un
     `lictor.json` que lo declare, y lo propio del proyecto va en `hints`.
   - Los `controls` salen del perfil en cada carga. En `lictor.json` solo van los cambios por `id`:
     con el de un control del perfil cambian solo los campos escritos (`{"id": "unit",
     "timeout_s": 900}`), `"enabled": false` lo quita y otro `id` añade un control, con `level`,
     `run` y `report`.
   - Si a la persona le importan los tests unitarios, ofrécele un mínimo de cobertura. Con
     `{"id": "coverage", "mode": "block", "min_score": 80}` el control bloquea por debajo, y el
     agente recibe el fallo en el siguiente intento. En `measure` el porcentaje solo sale en el
     informe: el agente no ve lo que no bloquea, y medir sin umbral no cambia lo que hace. El
     número lo decide la persona.
2. Pide a la persona que haga el commit: el run parte del último commit, y lo que no esté
   confirmado no entra. Conviene commitear también `.claude/skills/lictor/`: el esbozo lo comparte
   el equipo y el agente de Lictor no lo carga.
3. `lictor doctor` comprueba la configuración, el repositorio, la identidad git, el ejecutor, los
   comandos, el entorno de verificación y los escenarios. Antes del commit marca con ✗ el
   repositorio y el árbol, y no construye el entorno, pero los escenarios sí los valida.
   `lictor check` ejecuta los controles en un clon limpio, como haría un run.
4. Si ya hay código que cumple escenarios: `lictor register <ID>…`, antes del primer run y
   lanzado por la persona. Afirma que esos escenarios ya se cumplen sin que Lictor lo pruebe.
5. Dale el comando: `lictor run`, o `lictor run --branch <nombre>`.
   Va en su propio worktree y rama, `lictor/<nombre>`: la persona puede seguir haciendo commits.
   `lictor status`, en otra terminal, dice por dónde va. Al acabar, la persona integra la rama (un
   merge o una pull request) y `lictor retire <nombre>` retira el worktree.

## 4. Diagnosticar

- `lictor status`: el escenario y el intento en curso, los verdes, las llamadas y el coste.
- `.lictor/report.md`, en el worktree de la ejecución (`lictor status` lo dice, o
  `lictor report --branch <nombre>` desde el checkout): el informe del último run; el de cada run
  queda también en `.lictor/runs/<run_id>/report.md`, para compararlos. Las secciones que más
  dicen son «Verificaciones» (qué puerta paró cada intento), «Intentos fallidos por nivel»,
  «Cuarentenas», «Drivers rechazados», «Denegaciones» (órdenes que el agente intentó y no podía),
  «Verdes colaterales» y
  «Verdes registrados a mano».
- Si hace falta más: `.lictor/runs/<run_id>/<escenario o driver>/`, con el prompt de cada intento
  (`NNN-implement.prompt.md`), la salida cruda y, por verificación, `NNN-verify/error.txt`.
  `.lictor/journal.jsonl` es la bitácora de eventos.

| Código | Significa | Qué hacer |
|---|---|---|
| 0 | Todo en verde | Revisar con la persona el código y el driver de la rama `lictor/<nombre>`, y que la integre |
| 10 | Hay que cambiar algo del proyecto | Leer el motivo, que el run imprime: árbol sucio en el worktree, una rama que ya existe, varias ejecuciones que reanudar sin `--branch`, configuración, un feature inválido, el sello alterado, una ruta que solo puede tocar una persona, un escenario nuevo que pasa con un driver nuevo (un test vacío, o código que ya cumple: `lictor register`), un escenario reabierto que sigue pasando (solo la persona sabe si el cambio fue de redacción) |
| 11 | El agente declaró un conflicto de especificación | Leer el motivo en el informe y revisar los escenarios con la persona |
| 30 | Cuarentena tras agotar los intentos | «Intentos fallidos por nivel» y `error.txt`; el motivo `driver_sospechoso` apunta al driver |
| 40 | El entorno no deja ejecutar | `lictor doctor` dice qué; no es culpa de ningún escenario |
| 50 | Límite de uso del backend | Esperar y volver a lanzar `lictor run`: reanuda |
