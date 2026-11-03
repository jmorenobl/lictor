# ADR-0005: Cada ejecución trabaja en su propio clon y en su propia rama

## Estado
Propuesta. Dirección de diseño, sin implementar.

## Contexto
Hoy `lictor run` trabaja sobre la rama actual, en el checkout de la persona, y supone que nadie más mueve HEAD mientras corre:

- `IntegritySnapshot` trata un commit ajeno durante una llamada al agente como manipulación y devuelve la rama a su sitio con `git reset --keep`.
- Una reanudación con cambios sin commitear para con exit 10.
- `advance_head` exige un fast-forward desde la cabeza anclada.

Con una persona y una ejecución basta, pero choca con dos usos previsibles:

1. **Trabajo mixto.** Una persona sigue haciendo cambios pequeños a mano en el mismo repositorio mientras Lictor implementa escenarios.
2. **Varias ejecuciones a la vez** sobre el mismo proyecto, cada una con sus escenarios.

Además, el agente trabaja en una copia sin `.git` y no puede consultar la historia (`git log`, `git blame`), que es contexto útil en proyectos con historia.

Se valoró dar al agente un worktree y se descarta por dos razones:

- **Seguridad.** Un worktree comparte hooks, configuración y refs con el repositorio principal, así que lo que el agente hace en él alcanza al proyecto.
- **Coste.** No ahorra nada: escribe todos los ficheros igual que un export.

## Decisión
1. **Una ejecución trabaja en un clon propio del proyecto y en una rama propia,** nunca en el checkout de una persona ni en su rama actual. Dentro de ese clon se mantienen las garantías actuales (ancla, `IntegritySnapshot`, fast-forward). Vuelven a cumplirse sin esfuerzo, porque el clon es exclusivo de la ejecución.
2. **El resultado es una rama.** Integrarla en la rama de destino es un paso explícito y separado (merge o pull request). Si la rama de destino ha avanzado, se vuelve a verificar.
3. **Varias ejecuciones pueden correr a la vez en una máquina,** cada una con su clon, su `.lictor/`, su directorio temporal y su cerrojo. Lo compartido (caché de descargas, cuota del backend) se reparte de forma explícita.
4. **El agente sigue trabajando en un espacio aparte del clon de la ejecución.** Si necesita historia, la recibe como un clon local con su propio `.git` (objetos por hardlink), no como un worktree: puede leerla, e incluso hacer commits, sin alcanzar el repositorio de la ejecución. Lictor sigue capturando solo el árbol.
5. **La verificación sigue haciéndose en un clon limpio** del commit candidato.
6. **Sin sandbox (incremento 4), Lictor no debe ejecutarse desatendido** sobre especificaciones o entradas escritas por terceros. Ese texto llega al prompt de un agente que corre como el usuario.

## Consecuencias
- **Desaparece el conflicto con el trabajo manual:** la persona trabaja en su checkout y la ejecución en el suyo.
- **La dificultad se desplaza a la integración.** Dos ramas que salen del mismo commit no pueden entrar las dos por fast-forward: hay que rebasar o fusionar y volver a verificar.
- **Cambia el arranque, no el ciclo de cada escenario.** Cambia dónde se arranca (la ejecución crea o recibe su clon y su rama), dónde vive `.lictor/` y cómo se reanuda (por clon).
- **Lo caro de cambiar es una suposición, no un adaptador.** Que el agente trabaje en una copia o en un clon con historia es un detalle de adaptador (`Vcs.export_tree`, `Vcs.capture`). Lo caro es la suposición de que «Lictor commitea en la rama actual de una persona»: no hay que seguir construyendo encima de ella.
- **Coste en repositorios grandes.** El clon de la ejecución, el espacio del agente y el clon de verificación escriben el árbol entero. Reutilizar espacios entre intentos, o usar copy-on-write, es una optimización posterior que vale igual para cualquiera de las opciones.
