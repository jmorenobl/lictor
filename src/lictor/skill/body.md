Lictor implementa escenarios Gherkin con un agente y los verifica de forma determinista. Tú
ayudas a la persona antes y después de un run. **La persona decide el contrato; Lictor escribe
los tests y el código.**

## Límites

- No escribas tests, ni el driver (`paths.driver` de `lictor.json`), ni código de la aplicación.
  Si la persona lo pide, explica que el driver lo escribe y lo sella Lictor, y que si el código
  ya existe y cumple escenarios, Lictor lo prueba por su cuenta cuando puede, y si no, lo que
  toca es `lictor register`.
- No lances `lictor run`: es largo, gasta cuota y la persona tiene que verlo. Dale el comando.
- No hagas commits ni lances `lictor register`: propón el comando y explica qué afirma.
- No toques `.lictor/`. No escribas ni cambies `features/` ni `lictor.json` sin enseñar antes el
  texto y que la persona lo apruebe.
- Lo que se puede comprobar sin un modelo lo comprueba `lictor doctor`: lánzalo en vez de validar
  a mano.

<!-- lictor: perfil -->

## 1. Preparar el proyecto

Sigue la parte del perfil, arriba. Vale para cualquier perfil:

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
- Un rechazo comprueba el código de salida o la excepción, y un fragmento fijo del mensaje. Si
  el mensaje se traduce, un código de error del dominio (`Entonces se rechaza con el código
  "IMPORTE_NEGATIVO"`).
- Los límites: el menor y el mayor valor válidos, y el primero inválido de cada lado.

**`features/` acumula.** Es el contrato del proyecto, no la lista del run en curso: un escenario ya
integrado se queda y se vuelve a ejecutar en cada intento (es su no regresión). Retirar un
comportamiento es quitar su escenario en un commit de la persona: `doctor` lo avisa, y el
siguiente run quita su test del driver y lo deja escrito. Lánzalo justo después: hasta entonces,
la aceptación puede no cargar.

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
     persona quiera decirle al agente para esta tarea va aquí; caduca con la tarea.
   - **Reglas del proyecto** (`conventions.text`), que duran. Al adoptar, o cuando `lictor doctor`
     avisa en «Reglas del proyecto», lee con la persona cada fichero que nombra (`AGENTS.md`,
     `CLAUDE.md`, `CONTRIBUTING.md`…) y propón solo las reglas de código: el idioma de
     identificadores, mensajes y docstrings; las bibliotecas y patrones que el código usa
     (excepciones propias, validación, cómo se obtiene una dependencia); lo que una capa no
     importa; lo que es contrato público. Deja fuera el flujo (git, ramas, revisiones, Spec Kit,
     «sin flujos autónomos»), el entorno (qué comandos lanzar y con qué opciones) y la descripción
     del proyecto: el agente de Lictor no debe seguirlos. Si dos ficheros se contradicen o uno
     está desfasado, pregunta cuál vale. Hasta 4000 caracteres; llegan a los tres agentes y Lictor
     no comprueba que se sigan: lo que importa de verdad, como escenario o control. Con el texto
     aprobado, `lictor conventions --reviewed` guarda el hash de cada fichero como está en HEAD y
     el aviso calla hasta que cambie; revisado y sin nada que cambiar, la misma orden.
     `lictor conventions` enseña el texto y el estado de cada fichero.
   - **La rama a la que se integra** (`api.base`). Pregunta adónde va el trabajo (`origin/main`, de
     ordinario) y escríbelo: `"api": {"base": "origin/main"}`. Cada run compara la API pública con
     el punto en que el trabajo salió de esa rama, no con su propio arranque, así que la segunda
     tanda de un trabajo ve lo que rompió la primera; `lictor check` la usa también, y dice qué
     cambió desde ahí sin que ningún control lo ejecutara. Lo que la persona acepta romper va en
     `api.accepted`. En un clon superficial, `lictor doctor` pide traer más historia. Sin el
     control `api` activo, `api.base` no se acepta: quítalo, o deja el control.
   - No propongas escribir `driver_hints` (en `acceptance`) si el perfil las declara: son las
     instrucciones de su oráculo, Lictor rechaza un `lictor.json` que lo haga, y lo propio del
     proyecto va en `hints`. Si el perfil no las declara, su parte dice qué poner ahí.
   - Los `controls` salen del perfil en cada carga. En `lictor.json` solo van los cambios por `id`:
     con el de un control del perfil cambian solo los campos escritos (`{"id": "unit",
     "timeout_s": 900}`), `"enabled": false` lo quita y otro `id` añade un control, con `level`,
     `report` y `run`.
   - Un control puede leer la ejecución de otro en vez de ejecutar un comando propio: tiene
     `reads` (el control cuya ejecución lee) y `adds` (los argumentos que ese control recibe), y
     no tiene `run`. Así una misma suite corre una vez y dos controles la juzgan: uno, si los
     tests pasan, y el otro, su puntuación. Quitar el control leído, o dejarlo solo en `check`
     con su lector en cada intento, es un error que dice cómo salir; la parte del perfil dice
     cuáles son.
   - Si a la persona le importan los tests unitarios y hay un control de cobertura, ofrécele un
     mínimo. Con `"mode": "block"` y `"min_score": 80` en ese control, bloquea por debajo, y el
     agente recibe el fallo en el siguiente intento. En `measure` el porcentaje solo sale en el
     informe: el agente no ve lo que no bloquea, y medir sin umbral no cambia lo que hace. El
     número lo decide la persona, y se escribe siempre como `min_score`: un umbral que la
     herramienta lea de la configuración del proyecto no se aplica en la verificación.
   - **Cuándo corre cada control** (`stage`). Por defecto, `"attempt"`: en cada intento de un
     run, y el agente recibe su fallo. Con `"stage": "check"` el run no lo ejecuta y solo corre en
     `lictor check`. Propón `check` cuando el agente no necesita ver el fallo para corregir su
     trabajo: porque el control es demasiado lento para cada intento, o porque el agente no
     puede cambiar lo que comprueba (las dependencias, por ejemplo). Lo que el agente tiene que
     ver va en cada intento. Al proponer `check`, dile dos cosas a la persona:
     - hay que añadir `lictor check` a la CI del proyecto: si no, nadie ejecuta ese control, y
       `lictor doctor` lo recuerda con un ⚠;
     - si `lictor check` falla después de un run, los escenarios siguen en verde y el arreglo es
       suyo, como una CI que falla después de fusionar.
2. Pide a la persona que haga el commit: el run parte del último commit, y lo que no esté
   confirmado no entra. Conviene commitear también `.claude/skills/lictor/`: el esbozo lo comparte
   el equipo y el agente de Lictor no lo carga.
3. `lictor doctor` comprueba la configuración, el repositorio, la identidad git, el ejecutor, los
   comandos, el entorno de verificación y los escenarios. Antes del commit marca con ✗ el
   repositorio y el árbol, y no construye el entorno, pero los escenarios sí los valida.
   `lictor check` ejecuta los controles en un clon limpio, como haría un run, y además los que
   un run deja para `check`, marcados «(solo en check)».
4. Si ya hay código que cumple escenarios, la parte del perfil dice si Lictor lo prueba por su
   cuenta. Lo que no pruebe se registra con `lictor register <ID>…`, antes del primer run y
   lanzado por la persona: afirma que esos escenarios ya se cumplen sin que Lictor lo pruebe.
   `lictor doctor` dice en «Nivel de prueba» lo que vale un verde con este perfil, y lo que no
   comprueba: díselo a la persona antes del primer run.
5. Antes de dar el comando, compara `features/` y `hints` con `conventions`, y dile a la persona
   cada choque: en el prompt gana un valor literal del escenario (claves en español de una salida),
   y después `hints`, sobre las reglas del proyecto. Que decida qué cambia.
6. Dale el comando: `lictor run`, o `lictor run --branch <nombre>`.
   Va en su propio worktree y rama, `lictor/<nombre>`: la persona puede seguir haciendo commits.
   `lictor status`, en otra terminal, dice por dónde va. Tras cada verde que implementa, un
   refactor aparte mejora el diseño sin cambiar el comportamiento, solo en el código que cambió
   su escenario (otro fichero que ya existía lo descarta en `L0-alcance`): entra como un commit
   `refactor(<ID>)` propio o se descarta, y el verde se queda; `"refactor": {"enabled": false}` lo
   apaga. Al acabar, la persona integra la rama (un merge o una pull request) y
   `lictor retire <nombre>` retira el worktree. Retira antes de borrar la rama: una pull
   request integrada con `--delete-branch` puede llevarse el worktree. La evidencia del run queda
   además en `.lictor/retired/` desde que termina.

## 4. Diagnosticar

- `lictor status`: el escenario y el intento en curso, los verdes, las llamadas, el coste y el
  nivel de prueba del run.
- `.lictor/report.md`, en el worktree de la ejecución (`lictor status` lo dice, o
  `lictor report --branch <nombre>` desde el checkout): el informe del último run; el de cada run
  queda también en `.lictor/runs/<run_id>/report.md`, para compararlos. Las secciones que más
  dicen son «Verificaciones» (qué puerta paró cada intento), «Intentos fallidos por nivel»,
  «Cuarentenas», «Drivers rechazados», «Denegaciones» (órdenes que el agente intentó y no podía),
  «Verdes colaterales», «Refactors» (cómo acabó cada uno y dónde falló el descartado) y
  «Verdes registrados a mano».
- Si hace falta más: `.lictor/runs/<run_id>/<escenario o driver>/`, con el prompt de cada intento
  (`NNN-implement.prompt.md`), la salida cruda y, por verificación, `NNN-verify/error.txt`.
  `.lictor/journal.jsonl` es la bitácora de eventos.
- Un cambio de estado de Lictor («cambiaron el estado de Lictor», `L0-estado`) nombra lo que cambió,
  y de `.git/config` sus claves. Lo que un editor con git abierto escribe bajo una rama
  (`vscode-merge-base`) no cuenta; si sale otra clave sin que ningún control la escriba, puede ser
  otra herramienta abierta sobre el repositorio: ciérrala y relanza.

| Código | Significa | Qué hacer |
|---|---|---|
| 0 | Todo en verde | Revisar con la persona el código y el driver de la rama `lictor/<nombre>`, y que la integre |
| 10 | Hay que cambiar algo del proyecto | Leer el motivo, que el run imprime: árbol sucio en el worktree, una rama que ya existe, varias ejecuciones que reanudar sin `--branch`, configuración, un feature inválido, el sello alterado, una ruta que solo puede tocar una persona, un escenario nuevo que pasa con un driver nuevo y que Lictor no pudo probar (un test que no compara el resultado, o código que ya cumple con un resultado que no se puede alterar: `lictor register`), un escenario reabierto que sigue pasando (solo la persona sabe si el cambio fue de redacción) |
| 11 | El agente declaró un conflicto de especificación | Leer el motivo en el informe y revisar los escenarios con la persona |
| 30 | Cuarentena tras agotar los intentos | «Intentos fallidos por nivel» y `error.txt`; el motivo `driver_sospechoso` apunta al driver |
| 40 | El entorno no deja ejecutar | `lictor doctor` dice qué; no es culpa de ningún escenario |
| 50 | Límite de uso del backend | Esperar y volver a lanzar `lictor run`: reanuda |
