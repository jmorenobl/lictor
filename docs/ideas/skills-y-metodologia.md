# Skills y metodología

**Estado:** en estudio, 2026-11-21. No es una decisión ni está en el roadmap.

Dos preguntas que salen de querer usar Lictor en un proyecto real que hoy se desarrolla con
spec-kit (kitlegal: un binario en Go y, sobre todo, skills medidas con evals de modelo):

1. ¿Puede Lictor verificar una skill, que no es código determinista?
2. ¿Qué le falta a Lictor para que no hagan falta los pasos de spec-kit (especificar, aclarar,
   planificar, tareas)?

Hoy no se puede: Lictor solo tiene el perfil `python-uv`, su oráculo es un test que da siempre lo
mismo, y en un proyecto grande cada intento pasaría la batería entera.

## 1. Una skill como entregable

La idea de Lictor sirve tal cual: el oráculo se congela antes de implementar, se prueba que puede
fallar (el rojo), se implementa hasta el verde y lo comprueba una máquina. Lo que cambia es que el
resultado de una skill no es determinista.

| Pieza | Con código | Con una skill |
|:--|:--|:--|
| Escenario | Gherkin con valores | El mismo Gherkin: «Dado que preguntan por el art. 21 de la LPAC, Entonces cita BOE-A-2015-10565 a21 y no da plazos de memoria» |
| Driver | Un test que llama al código | Una sesión del agente con la skill instalada, y comprobaciones sobre lo que hizo: herramientas llamadas, citas, expresiones prohibidas y, para el significado, un juez con modelo |
| Rojo | Los stubs hacen fallar el test | **Sin la skill, o con su versión anterior, el escenario falla.** Prueba que la eval mide algo |
| Verde | Pasa o no pasa | **Una tasa:** pasa en al menos k de n sesiones, como una puntuación con `min_score` |
| Coste de verificar | Segundos | n sesiones de modelo |

Lo que habría que construir:

1. **Veredictos estadísticos.** El dominio ya separa el hecho del veredicto (ADR-0009); faltaría
   un resultado muestreado, con su intervalo, y la regla de cuándo decide.
2. **Parar en cuanto se sepa.** Parar las sesiones en cuanto el intervalo decide, en vez de lanzar
   siempre n. Es la mayor ganancia de tiempo.
3. **Medir otra vez solo lo afectado.** Guardar cada sesión con el hash de lo que ejecutó (la
   skill, la eval, el modelo y, si llama a un binario, el binario) y repetir solo lo que cambió.
4. **El juez entra en el sello.** Su prompt y su modelo, en la huella. Un juez sin calibrar es un
   oráculo sin probar.
5. **Escenarios que el agente no ve.** Con código, aprender el test no rinde: el test comprueba el
   comportamiento. Con una skill, el agente puede ajustar el texto a las preguntas de la eval sin
   querer. Haría falta separar las evals que ve de las evals ocultas, que solo usa la verificación.
6. **El modelo fijado.** Cambiar el modelo de las sesiones reabre los verdes, como hoy cambiar un
   escenario.

## 2. Un proyecto mezcla las dos cosas

No basta con un perfil de skills al lado de uno de Go: hay proyectos solo de Go, solo de skills y
de las dos cosas, y en el tercero dependen una de otra (la skill llama al binario). Hoy un proyecto
tiene un perfil, un entorno de verificación y una aceptación. Eso tendría que separarse en dos ejes:

| Eje | Qué decide | Ejemplos |
|:--|:--|:--|
| **Cadena de herramientas** | Cómo se construye, se lintea y se testea una parte del proyecto, con sus rutas y controles | `go`, `python-uv`, ninguna (solo skills) |
| **Clase de oráculo** | Cómo se juzga un escenario: rojo, verde y coste | determinista (un test), muestreado (sesiones y tasa) |

Un proyecto declararía una o varias cadenas, y cada escenario su clase de oráculo, por ejemplo por
el directorio de su feature o por una etiqueta. Los tres casos:

| Proyecto | Cadenas | Oráculos | Lo que cambia |
|:--|:--|:--|:--|
| Solo Go | `go` | determinista | Lo de hoy con otro perfil |
| Solo skills | ninguna | muestreado | No hay stubs: el rojo es «sin la skill» |
| Go y skills | `go` | los dos | Las dependencias entre partes |

Lo difícil es el tercero:

- **Un escenario de skill puede pedir un cambio en Go.** El intento toca las dos partes y la
  verificación corre las dos: primero lo determinista, que es barato, y las sesiones solo sobre un
  binario en verde.
- **Un cambio en Go puede romper una skill.** Las skills que usan lo que cambió hay que medirlas
  otra vez. Sin el hash del punto 3 del apartado anterior, cualquier cambio de Go relanzaría todas
  las sesiones.
- **El orden dentro de un hito.** Lo determinista primero, y las skills encima de lo ya verde.
- **El verde de un hito.** Mezcla verdes exactos y tasas: el informe tiene que contarlos aparte.

## 3. Sin los pasos de spec-kit

| Paso de spec-kit | Con Lictor | ¿Hace falta algo nuevo? |
|:--|:--|:--|
| specify | **Los escenarios son la especificación** | Sí: pasar del hito a los escenarios |
| clarify | Se resuelve hablando con la persona al escribir los escenarios: la ambigüedad aparece ahí y la decide ella | Sí: la skill tiene que preguntar |
| juez del spec | `doctor` comprueba la forma, no el fondo (valores concretos, sin contradicciones, que se puedan probar) | Sí: un juez de escenarios, primo del juez del driver de la fase 2c |
| plan y tareas | **Sobran:** cada escenario es una tarea y el agente planifica en su turno | No |
| juez del plan y revisión | Lo cubren los controles, el refactor y el oráculo sellado | No |
| decisiones de diseño | Lictor no las toma: sin plan, la arquitectura sale escenario a escenario | Sí: el hito lleva sus decisiones y llegan al prompt, como hoy `hints` |
| cierre (PR, CI) | Lictor deja una rama y dice cómo integrarla | Poco: abrir la PR |
| evals | — | El apartado 1 |

## 4. La metodología, aparte del motor

Lictor sigue siendo el motor determinista: escenarios → código verificado. La metodología es una
capa encima, una skill que guía la conversación:

1. Lo que se quiere construir.
2. Los hitos.
3. Los escenarios de un hito, que aprueba la persona. Es la única pausa humana, y corta.
4. `lictor run`.
5. Leer `report.md`.
6. El siguiente hito.

Así el motor no se llena de cosas blandas, y la metodología puede cambiar sin tocar el oráculo. Lo
más delicado es el paso 3: unos escenarios malos dan código malo bien verificado, así que el juez
de escenarios vale más que cualquier otra pieza de esta capa.

## Preguntas abiertas

- ¿Qué regla estadística decide el verde de una tasa, y con qué coste máximo por escenario?
- ¿Quién escribe las evals ocultas, y cómo se mantienen ocultas si el agente lee el repositorio?
- ¿Cómo se calibra el juez antes de sellarlo, y qué pasa si cambia su modelo?
- ¿Cómo declara un proyecto sus cadenas y qué clase de oráculo tiene cada escenario?
- ¿Dónde viven los hitos: en el proyecto, como en kitlegal, o en Lictor?
- ¿Qué decisiones de un hito necesitan una persona antes del run, y cuáles puede tomar el agente?

## Un orden posible

1. **La metodología, de hitos a escenarios.** Lo más barato y lo que más se nota; se puede probar
   ya en un proyecto Python.
2. **El perfil de Go.** Abre la parte determinista de kitlegal.
3. **Perfiles componibles y el oráculo muestreado.** Lo más ambicioso: necesita la fase 3 (solo lo
   afectado) y una ADR para los veredictos estadísticos y las dependencias entre partes.
