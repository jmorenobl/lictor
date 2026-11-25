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
