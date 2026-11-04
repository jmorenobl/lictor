# ADR-0006: Precondiciones comunes, un `lictor check` que significa lo mismo que `run` y el registro manual de escenarios

## Estado
Aceptada

## Contexto
La auditoría de la v0.1 y el plan de corrección dejaron tres problemas en la frontera entre Lictor y quien lo usa:

1. **Fallos que llegaban tarde.** Sin identidad git, el fallo llegaba después de gastar el driver, como «Error interno» y sin el mensaje de git. Un `verification.prepare` roto también se descubría después del driver. `lictor doctor` no comprobaba ninguna de las dos cosas, buscaba los ejecutables en el entorno del usuario y no en el de verificación, y mezclaba los códigos 10 y 40.
2. **Un ✓ de `lictor check` no equivalía al de `run`.** `check` ejecutaba los controles en el árbol de trabajo, con el entorno y la configuración del usuario. `run` verifica un commit en un clon limpio y en un entorno comprobado por hash.
3. **Adoptar un proyecto con código.** Con un driver nuevo, un escenario que ya pasa no se distingue de uno con un test vacío. Lictor lo rechaza, y el mensaje pide al agente que el test falle mientras la interfaz no esté implementada. En un proyecto con código previo, eso empuja a romper un test correcto. La salida era un commit vacío a mano con el trailer `Scenario:`, que nada distinguía de un verde de Lictor.

## Decisión
1. **Unas mismas precondiciones** para `preflight`, `doctor` y la reanudación (`CheckPreconditions`):
   - el árbol limpio (no en la reanudación, que ya lo comprueba a su manera);
   - la identidad git, preguntada con la misma llamada que hace el commit;
   - el ejecutor;
   - el directorio temporal privado;
   - el primer programa de cada control y de la aceptación, buscado con el `PATH` que recibirán, o en HEAD si es una ruta relativa;
   - y el entorno de verificación.

   **Códigos de salida:** el dominio decide el código por la clase de cada problema. Es 10 si hay que cambiar algo del proyecto (la configuración, un comando vacío o el árbol sucio) y 40 si solo hay que arreglar la máquina (la identidad, un programa que falta, el directorio temporal o `prepare`). Si hay problemas de las dos clases, gana el 10.

   **`preflight` construye el entorno de la ejecución** después de las comprobaciones baratas y antes de leer la especificación. No cuesta más que antes: toda ejecución lo necesita. Lo que cambia es que un fallo llega antes de cualquier llamada al agente.

   **`doctor` informa de todo, sin parar en el primer fallo,** y construye un entorno desechable, sin tocar el de una ejecución pausada. Para eso toma el cerrojo del proyecto. Si otra ejecución lo tiene, hace el resto de comprobaciones y deja el entorno como «no comprobado».
2. **`lictor check` verifica como `run`:**
   - en un clon limpio, en el entorno de verificación construido para ese árbol, y comparando su hash antes y después;
   - **lo sin commitear se captura:** si hay cambios, se lee el árbol de trabajo sobre HEAD, igual que la copia del agente, y se verifica un commit candidato fuera de la rama;
   - la identidad git solo se exige cuando hay candidato.

   No queda modo «en el árbol de trabajo»: un mismo ✓ no puede significar dos cosas.

   `check` toma el cerrojo, admite `--keep-going` y `--control`, y deja su evidencia en `.lictor/check/`, que cada `check` sustituye. Códigos: 0, 10 si falla un control bloqueante y 40 si el entorno falla. La autoverificación de Lictor en el CI es `lictor check --keep-going`.
3. **`lictor register <ID>...` registra escenarios como verdes por afirmación de una persona.**
   - Requisitos: toma el cerrojo, no hay ninguna ejecución reanudable, el árbol está limpio y git tiene identidad.
   - Es todo o nada.
   - Explica lo que se afirma según la clase del escenario (nuevo o reabierto) y pide confirmación, o `--yes`.
   - Hace un solo commit vacío, con un `Scenario:` por escenario y `Lictor-Asserted-By: <identidad>`, y emite `scenario.asserted`.

   **No cambia lo que garantiza el rojo.** Lo que Lictor marca en verde por su cuenta sigue exigiendo lo mismo. Este verde lo afirma una persona, como antes con el commit a mano, pero ahora queda dicho.
4. **El informe y `status` marcan como «registrado a mano»** todo verde cuyo commit no lleve `Lictor-Run`, que Lictor pone en todos los verdes que commitea desde su primera versión.
   - **La marca es informativa.** Los trailers los puede escribir cualquiera con git, y un commit hecho a mano puede llevar `Lictor-Run`.
   - Sirve para no presentar como probado por Lictor lo que no lo está, no para detectar una falsificación.

## Consecuencias
- `doctor` y `run` ya no pueden discrepar sobre lo que hace falta para empezar.
- `check` cuesta más que antes: construye un entorno sin caché y clona el árbol. A cambio, su ✓ es el de `run`. Quien quiera una respuesta rápida ejecuta sus herramientas directamente, como el agente con `agent_commands`.
- **Un programa que falta pasa de 10 a 40.** Reintentar tras instalarlo es lo que el 40 significa. Una errata en `lictor.json` también da 40, pero el mensaje nombra el control y el comando.
- Adoptar Lictor en un proyecto existente es un paso explícito antes de la primera ejecución. Para el driver reconstruido, un escenario registrado es un verde, así que solo tiene que pasar, y un test débil también pasa. Es el mismo riesgo que con cualquier verde (ADR-0004), ahora visible en el informe.
