# ADR-0016: Un Entonces tiene que leer el valor de su escenario

## Estado
Aceptada (2026-11-25). Sale de la clase «el resultado es un objeto» del roadmap, tras la 0.17.0
(`.audits/qa/prompt-objetos.md`). Se midió antes de diseñarla, fuera de Lictor, con dos candidatos:
alterar objetos (la enmienda de los objetos de valor de la ADR-0011) y cambiar el valor del
Gherkin, sobre los 317 escenarios de las pasadas y tres proyectos sintéticos
(`.audits/qa/objetos/diseno.md`). La revisó por su cuenta un revisor independiente, que escribió 48
casos y los ejecutó (`.audits/qa/objetos/fable/revision.md`). Esta versión recoge sus hallazgos,
comprobados ejecutándolos. Se publica en la misma versión que la enmienda de la ADR-0011: cada una
sola empeora algún caso.

## Contexto
El impostor de la interfaz (ADR-0011) prueba que el Entonces lee lo que devuelve el código, pero no
que lo compare con el valor del escenario. Con los objetos de valor, prueba 307 de los 317. De los
10 que quedan, en 9 lo que el test comprueba no es lo que la interfaz le devuelve:

- un fichero que escribe el código (ALM-001);
- las peticiones que recibe un doble HTTP (ENR-001, 002 y 005);
- la salida de una orden que ejecuta typer (CLI-001, 002 y EXP-001);
- `[0]` de una lista de uno (RES-001, en dos pasadas).

Hoy, 5 de esos se reintentan con un mensaje falso («no compara el resultado») y 4 son finales. El
décimo, VER-005, recibe del Gherkin el tipo de registro («alta») y no lo usa nunca. Nada lo dice.

Un tercer hecho lo sabe el Gherkin: el valor del escenario. Si cambiarlo hace fallar el Entonces,
el test lo lee. Pero leerlo no basta. Un test que compara el valor con lo que guardó un Dado
(DEB-012, SV-001), o el literal copiado del código de la ADR-0011, también falla con otro valor.
Por eso se descartó la regla «prueba cualquiera de los dos»: daba por buenos los 317, y con ellos
esos tests vacíos. La interfaz prueba que se lee el resultado del código, y el Gherkin que se lee
el valor del escenario. Hacen falta los dos.

## Decisión

1. **Qué es un valor del Gherkin.** Cada argumento que un paso Entonces recibe del Gherkin: los que
   extrae su parser y la función del paso declara, ya convertidos, y su tabla de datos y su doc
   string. Se numeran en orden en cada test. El impostor del perfil (`python_uv/lictor_gherkin.py`,
   en `verification.files` y `plugins`) no hace nada sin su variable, `interface_impostor.gherkin`
   del perfil (`LICTOR_GHERKIN`, que `reserved` cubre). Recibe los mismos modos que la interfaz.
   Usa dos funciones internas de pytest-bdd: si faltan, calla.
2. **Un valor que el Entonces no lee** es un hecho de su código, y sale del control sin más
   ejecuciones. El paso lo recibe, y su bytecode nunca lo carga (`LOAD_FAST`, `LOAD_DEREF`,
   `LOAD_CLOSURE`, en cualquier parte del nombre de la instrucción, porque 3.13 junta dos en una).
   Una clausura que lo captura sí lo lee. El desenlace es `ignora_el_valor`, con reintento, y va
   antes que todo lo demás, una prueba de la interfaz incluida: el arreglo es el bueno. El agente
   lee el paso y el valor: «el Entonces «…» recibe «12» del escenario y no lo usa».

   Un valor que el parser extrae y la función no declara solo se cuenta (`unreceived`), sin vetar:
   un grupo de alternativa de una expresión regular (`tiene|cuesta`) es un test correcto (FR-007).
3. **Un valor leído.** Para lo que la interfaz no prueba, se cambia cada valor que se puede
   cambiar, solo, una vez por modo: un número ±1 en su formato, una fecha ±1 día, un texto o una
   lista, la última celda de una tabla. Con la interfaz en control, cada ejecución corre solo esos
   tests: el plugin deselecciona los demás por sus etiquetas, `targets`. El valor se lee si el test
   falla por una aserción del driver en un Entonces en los dos modos.
4. **Un valor que es entrada.** Si, al cambiarlo, cambian los argumentos de alguna llamada del
   driver a la interfaz, el valor es una entrada y no cuenta. Lo dice la traza de la interfaz
   (ADR-0011, enmienda de los objetos de valor), argumento por argumento. Un segundo control dice
   qué argumentos cambian solos entre dos ejecuciones (la hora), y esos no cuentan.
5. **La regla**, en orden:

   | Orden | Condición | Desenlace | Reintenta |
   |:--|:--|:--|:--|
   | 1 | Un valor que el Entonces recibe y no lee | `ignora_el_valor` | sí |
   | 2 | Una excepción provocada (ADR-0011) | `provocada` | no |
   | 3 | La interfaz lo prueba | probado | — |
   | 4 | La interfaz da `sobrevive`, `no_aserta`, `sin_alterar` o `a_traves`; se lee cada valor que no es entrada, al menos uno; y el Cuando o el Entonces llamaron a la interfaz (directa, por una librería o entregada) | `solo_el_valor` | no |
   | 5 | Lo demás | lo que diga la interfaz | como hoy |

   `fuera_del_entonces` no pasa a `solo_el_valor`. Una aserción del driver cazó el resultado, en
   otro paso: llevarla al Entonces lo prueba, así que el reintento es el bueno. Un perfil sin
   `gherkin` se queda en 2, 3 y 5.
6. **Los mensajes.** El de `ignora_el_valor`, al agente. En `solo_el_valor`, el agente solo lee que
   no cambie el test, como con `sin_alterar`. La persona lee solo hechos (`only_the_value_note`):
   con lo que devuelve la interfaz alterado, el test sigue pasando o falla sin una aserción de un
   Entonces; con otro valor del escenario, falla; y dónde lo lee, con la librería si pasó por una.
   El rechazo final no invita a `lictor register` (`_READ_THEM_HINT`): un test que compara el valor
   con lo que guardó un Dado se ve igual.
7. **En los stubs (ADR-0012)**, el camino a la interfaz pasa por la misma regla. `ignora_el_valor`
   se rechaza con su mensaje. `solo_el_valor` se rechaza como `a_traves`, con `sin_codigo`: pasa sin
   el código nuevo, y nada prueba que compare lo de la interfaz.
8. **Lo medido.** `lictor.driver.measured.v1` guarda, por escenario, el desenlace de la interfaz
   sola (`interface`), los modos con una excepción provocada (`provoked`) y lo que vio el Gherkin
   (`gherkin`: `values`, `unread`, `unreceived`, `read`, `inputs`, `survived`, `errors`, `place`).
   «Medidas del driver» de `report.md` tiene una columna «Gherkin» («3 de 4 leídos, 1 entrada»), y
   la de la interfaz dice lo que dijo sola si la regla lo cambió. La evidencia va a
   `NNN-red/gherkin-control-bis/` y `NNN-red/gherkin-<modo>-<n>/`, con `stubs-` delante en los
   stubs.

## Consecuencias

En los 317, con el código implementado y medido igual que el prototipo:

| Desenlace | Escenarios | Hoy (0.17.0) |
|:--|--:|:--|
| probado | 307 | 284 |
| `solo_el_valor` | 9 | 5 reintentaban con un mensaje falso, 4 eran finales |
| `ignora_el_valor` | 1 (VER-005) | probado |
| Tests buenos vetados | 0 | — |

ORD-003 queda probado. En los casos de Fable no hay vetos en falso. De los falsos probados que
señaló, quedan dos, documentados como límites: FP-004 y DEB-016.

**El coste.** G solo corre sobre lo que la interfaz no prueba, y el veto no necesita ejecuciones.
Para esos tests son un control más y dos ejecuciones por valor. En `pyverifactu` (8 tests, 4
valores): 7,9 s de la interfaz y 11,6 s del Gherkin, 2,5 veces la interfaz sola. Donde la interfaz
lo prueba todo, el coste no cambia.

**Criterio de parada.** Los impostores prueban dos hechos: que el Entonces lee lo que devuelve la
interfaz y que lee el valor del escenario. Se abre una F nueva en los impostores solo si una pasada
real muestra un falso verde, o un test correcto rechazado con reintento. Un test correcto que va a
la persona con un mensaje cierto no abre nada mientras sea menos del 5 % de los «ya se cumplía»
medidos (hoy 9 de 317, un 2,8 %). Lo semántico que quede va a la fase 2c.

**Límites conocidos:**

- Un literal con el parámetro quitado de la firma (DEB-016) no se puede vetar sin rechazar FR-007,
  y queda probado, como hoy.
- Un valor comparado con lo que guardó el driver, junto a otro bien comparado (FP-004), queda
  probado: la interfaz prueba el otro.
- Un valor leído solo en el mensaje de la aserción cuenta como leído.
- Las formas adversarias (`(120 if esperado == 121 else esperado)`, comparar el parámetro con una
  constante) quedan fuera, por la premisa.
- Los efectos (ficheros, dobles, la salida de una orden) van a la persona como `solo_el_valor`.
  Alterar lo que escribe el código sería otro impostor.
- El veto es estático y podría ir en el rojo de todo driver nuevo, no solo en el de los que ya se
  cumplen. Es el paso siguiente, no esta decisión.
- Otros parsers y versiones de pytest-bdd: las dos funciones internas existen en 8.1 y 9.0.

## Enmienda del Entonces que no puede leer lo alterado (0.21.0, 2026-11-26)

En la 4a de pyverifactu con la 0.20.0, COR-006 fue a la persona como `solo_el_valor` y hubo que
registrarlo a mano, aunque su test es correcto. Su Entonces compara un texto del XML que devuelve el
código (`assert _texts(record, "Subsanacion") == [correction]`). En `mas`, el impostor alargó los
bytes del XML (`+ b"\0"`), y el Entonces falló al parsearlo, con un error, no con su aserción
(`no_aserta`). En `menos` quitó el `\n` final, que no cambia nada de lo que se lee. Diseño y medida:
`.audits/qa/0-21-0/diseno.md`, F85; revisión: `revision-fable-f85.md`.

**El criterio de parada** se mide sobre los «ya se cumplía» de todas las pasadas medidas, no de una:
con los 317 de esta ADR y los 16 de la 4a fueron 11 de 333 (3,3 %). F85 se abre por el caso: un
test correcto, final y registrado a mano, con otros tres de la misma forma en los datos (COR-005,
ENR-001, ENR-002) y, en el corpus del revisor, un test correcto rechazado con reintento y un mensaje
falso (OK-014).

**Decisión.** Un modo en que un Entonces no pudo leer lo alterado cuenta como cazado si se rompió en
la sentencia que compara un valor del escenario o en una que la alimenta. Así se prueba que esa
aserción depende de los dos: del resultado y del valor.

1. **La sentencia.** `lictor_outcome` da `lictor.statement`: una sentencia por marco, desde el de la
   función del paso mientras esté en el proyecto, con las líneas de su función que cargan lo que ella
   liga (definición y uso, transitivo, solo nombres simples). El dominio toma la parte del driver.
2. **Unidas.** Una cadena es prefijo de la otra (la misma sentencia, evaluándose), o, donde se
   separan, la sentencia rota alimenta a la que lee el valor (`root = parse(r.xml)` y, debajo,
   `assert root.findtext(…) == v`). Un ayudante que parsea en una línea y compara en otra el valor de
   un Dado no se une.
3. **La regla del punto 5 gana dos filas** tras la 3. La 3b: `no_aserta` en el que todos los modos que
   objetan fallaron con un error en un Entonces del driver, con cada valor leído y cada sentencia rota
   unida a una lectura, se da por probado. La 3c: si el Gherkin no tiene nada que pesar, el desenlace
   es `ilegible`, final y para la persona (en los stubs se acepta sin comprobar, como `sin_alterar`).
4. **`menos` en un texto** (ADR-0011) quita el último carácter y, si era un blanco, también el último
   que no lo es.
5. `lictor.frame` y `lictor.origin` ya no toman por del proyecto una ruta relativa (la de la fuente de
   un módulo compilado).

**Medida**, con el commit anterior y con la rama sobre el corpus de esta ADR y los casos del revisor:

| Corpus | Tests | Cambian |
|:--|--:|:--|
| Las pasadas de esta ADR | 318 | ENR-001, ENR-002 y RES-001 ×2: `solo_el_valor` → probado |
| Sintéticos y casos del revisor | 74 | OK-014: `no_aserta` con reintento → `ilegible` |
| La 4a, COR-001 a COR-007 | 7 | COR-005 y COR-006: `solo_el_valor` → probado |

No hay falsos probados nuevos, y los `solo_el_valor` de los 317 pasan de 9 a 5.

**Límites conocidos:**

- Una sentencia que usa el resultado para llegar a otra cosa y compara el valor con lo que guardó
  un Dado (`assert system.invoices[record.number].total == total`) queda probada. La regla 3 ya
  prueba la misma idea escrita con `.get()`.
- La salida de un `with` cae en otra línea en Python 3.9. Dos sentencias en una línea (`a; b`,
  `if x: assert y`) no se nombran, y un marco del proyecto sin nombre deja la cadena vacía. Una
  fixture que lee el resultado no deja marco del paso. El mensaje de un `assert` no cuenta como
  lectura: solo se evalúa si la condición falla.
