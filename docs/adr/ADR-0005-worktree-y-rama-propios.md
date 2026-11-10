# ADR-0005: Cada ejecución trabaja en su propio worktree y en su propia rama

## Estado
Aceptada (2026-11-10). Sustituye a la propuesta del 2026-11-04, «Cada ejecución trabaja en su propio clon y en su propia rama».

## Contexto
Hoy `lictor run` trabaja sobre la rama actual, en el checkout de la persona, y supone que nadie más mueve HEAD mientras corre:

- `IntegritySnapshot` trata un commit ajeno durante una llamada al agente como manipulación y devuelve la rama a su sitio con `git reset --keep` (`GitCli.restore`).
- Una reanudación con cambios sin commitear para con exit 10.
- `advance_head` exige un fast-forward desde la cabeza anclada.

Con una persona y una ejecución basta, pero choca con dos usos previsibles:

1. **Trabajo mixto.** Una persona sigue haciendo cambios a mano en el mismo repositorio mientras Lictor implementa escenarios. Hoy la skill tiene que avisar: «mientras dure el run, no hagas commits en ese checkout».
2. **Varias ejecuciones a la vez** sobre el mismo proyecto, cada una con sus escenarios.

La propuesta anterior resolvía los dos con un clon por ejecución. Tenía un coste que este ADR evita: el resultado quedaba en otro repositorio, del que había que traer la rama antes de integrarla. Las herramientas que hoy coordinan varios agentes sobre un repositorio trabajan con worktrees de git: un checkout por tarea, con su rama, dentro del mismo repositorio. Lictor tiene que poder usarse solo y también junto a ellas, sin depender de ninguna.

La propuesta anterior descartaba el worktree **para el agente**, y ese motivo sigue en pie. Un worktree comparte con el repositorio principal la configuración, los hooks, `info/` y las refs, así que lo que el agente hiciera en él alcanzaría al proyecto. Pero el run no es el agente: es Lictor, que ya ejecuta git blindado y ya aísla al agente en una copia sin `.git`.

## Decisión
1. **Una ejecución trabaja en un worktree propio y en una rama propia,** nunca en el checkout de una persona ni en su rama actual.
   - **Lo crea Lictor.** `lictor run` lanzado desde un checkout normal crea el worktree desde HEAD con una rama nueva y ejecuta allí el run entero. La persona no tiene que preparar nada.
   - **Se crea con el mismo blindaje que cualquier llamada de `GitCli`:** sin hooks (un `git worktree add` ejecuta `post-checkout`), sin fsmonitor, sin replace objects y sin las ubicaciones heredadas (`GIT_DIR` y similares).
   - **Lo sin commitear no entra en el run.** El worktree parte del último commit. Si el checkout de la persona tiene cambios sin commitear, Lictor avisa de que se quedan fuera, en vez de exigir un árbol limpio.
2. **Si ya está en un worktree enlazado, lo usa tal cual.** Por ejemplo, uno creado a mano o por otra herramienta. Lo detecta leyendo ficheros, como hace hoy `_git_dir`, sin preguntar a git: el `.git` del checkout es un fichero que apunta a `<común>/worktrees/<nombre>`.
3. **Dónde vive el worktree:** fuera del proyecto, en `~/.lictor/worktrees/<repo>-<hash de su ruta>/<rama>` por defecto, configurable por máquina y no en `lictor.json`. Nunca va en el directorio temporal, porque un reinicio lo borraría y la ejecución no se podría reanudar.
4. **La rama:** `lictor/<nombre>`, con el nombre que se pase en `--branch`, o derivado del id de la ejecución si no se pasa ninguno. Si la rama ya existe, Lictor se niega, salvo que sea la de la ejecución que se reanuda. Git no deja sacar la misma rama en dos worktrees, así que la rama propia la garantiza git.
5. **El estado de la ejecución vive en el worktree.** Su `.lictor/` contiene checkpoint, bitácora, sello, `runs/` y cerrojo, y dentro de él se mantienen las garantías actuales: ancla, `IntegritySnapshot`, fast-forward y `exclusive_run`.
   - El checkout principal guarda solo un índice de las ejecuciones y su worktree. Así `lictor run`, para reanudar, y `lictor status`, lanzados desde él, encuentran las ejecuciones sin tener que entrar en el worktree.
   - El índice es solo un puntero: lo que decide una reanudación sigue siendo el ancla del checkpoint de esa ejecución.
6. **La integridad cubre el directorio común.** En un worktree, `config`, `hooks/` e `info/` viven en el directorio común del repositorio, no en el del worktree. El ancla, la huella de los ficheros de control y `IntegritySnapshot` tienen que leer los dos:
   - del directorio del worktree, `HEAD` y `config.worktree`;
   - del directorio común, `config`, `hooks/` e `info/`.

   Las refs que usa Lictor tampoco pueden ser compartidas. `refs/lictor/verified` es hoy una sola para todo el repositorio, y dos ejecuciones en paralelo se la pisarían: pasa a ser de cada worktree (`refs/worktree/lictor/verified`) o de cada ejecución.
7. **El resultado es una rama, y la integración es un paso explícito y separado**: un merge o una pull request que hace la persona. Lictor nunca integra en la rama de destino. Si esa rama ha avanzado, se vuelve a verificar antes de integrar.
8. **El agente sigue trabajando en una copia sin `.git`,** exportada del worktree de la ejecución, como hoy (`export_copy`). Si algún día necesita historia, la recibe como un clon local con su propio `.git`, nunca como un worktree.
9. **La verificación sigue haciéndose en un clon limpio** del commit candidato (`verification_tree`).
10. **Limpieza:** al terminar, el worktree y la rama se quedan, para que la persona integre. Una orden aparte retira el worktree después (`git worktree remove`):
    - nunca borra una rama sin preguntar;
    - se niega si el worktree tiene cambios sin commitear o una ejecución que se puede reanudar.
11. **Varias ejecuciones pueden correr a la vez en una máquina,** cada una con su worktree, su rama, su `.lictor/`, su directorio temporal y su cerrojo. Lo compartido, como la caché de descargas y la cuota del backend, se reparte de forma explícita.
12. **Lictor no depende de ninguna otra herramienta.** Funciona igual si la persona trabaja sola o con un gestor de agentes. Informar del progreso a una herramienta externa queda fuera de este ADR: sería un adaptador opcional que lee la bitácora, como `RunProgress`, y que solo se activa si detecta que corre dentro de ella.
13. **Sin sandbox (incremento 4), Lictor no debe ejecutarse desatendido** sobre especificaciones o entradas escritas por terceros. Ese texto llega al prompt de un agente que corre como el usuario.

## Consecuencias
- **Desaparece el conflicto con el trabajo manual.** La persona sigue en su checkout, con commits y cambios sin commitear, y la ejecución trabaja en su worktree. Deja de hacer falta la regla «no hagas commits durante el run», y la skill y el README la quitan.
- **La rama del resultado ya está en el repositorio de la persona:** se integra sin traer nada. Con el clon de la propuesta anterior había que hacer un fetch.
- **La dificultad se desplaza a la integración.** Dos ramas que salen del mismo commit no pueden entrar las dos por fast-forward: hay que rebasar o fusionar y volver a verificar.
- **Cambia el arranque, no el ciclo de cada escenario.** Cambian dónde se arranca, dónde vive `.lictor/` y cómo se reanuda (por worktree). `preflight`, la reanudación y `status` cambian; `implement`, `verify` y `commit` no.
- **Hay que decidir qué hacen las demás órdenes.** Por defecto, siguen en el checkout donde se lanzan:
  - `init`, `doctor`, `skill` y `check` son de la persona;
  - `register` mueve la rama del checkout donde se lanza, que es la de la que parten las ejecuciones siguientes.

  `status` y `report` aceptan además una ejecución por rama.
- **La huella del repositorio cambia de forma.** Un checkpoint de la 0.2.0 anclado con la huella antigua no se puede reanudar con la nueva: la reanudación lo dice y pide empezar de nuevo, en vez de leerlo como una manipulación.
- **Más disco:** el checkout del worktree, la copia del agente y el clon de verificación escriben el árbol entero, igual que con el clon por ejecución, pero el worktree no duplica los objetos del repositorio. Reutilizar espacios entre intentos, o usar copy-on-write, es una optimización posterior.
- **Lo caro de cambiar era una suposición, no un adaptador:** que «Lictor commitea en la rama actual de una persona». Con este ADR deja de ser cierta, y no hay que construir nada más encima de ella.

## Alternativas descartadas
- **Un clon por ejecución**, la propuesta del 2026-11-04. Aísla lo mismo, pero deja el resultado fuera del repositorio de la persona, duplica los objetos y no encaja con las herramientas que ya organizan el trabajo por worktrees.
- **El agente en un worktree.** Comparte configuración, hooks y refs con el repositorio, y el agente es justo de quien hay que protegerlo. Además no ahorra nada: escribe todos los ficheros igual que una copia.
- **Que la persona cree el worktree.** Funciona, y sigue funcionando por el punto 2 de la decisión, pero no puede ser la única vía: traslada a la persona un paso que Lictor hace mejor, con el blindaje de git incluido.
