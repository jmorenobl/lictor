# ADR-0022: `.git/config` se vigila por claves, y lo de otras ramas no es de Lictor

## Estado
Aceptada (2026-11-28), en la 0.25.0. Sale de la fricción S22 de un proyecto real
(`.audits/qa/0-25-0/fricciones.md`), revisada por Fable (`revision-fable.md`, B1 e I1). Decisión F2
de Jorge, en su opción (a′).

## Contexto
`IntegritySnapshot` y el ancla de reanudación comparaban `.git/config` entero, como bytes. Pero ese
fichero es común al checkout y a todos los worktrees, y lo escriben cosas que no son Lictor ni el
agente: la extensión de git de un editor añade `branch.<rama>.vscode-merge-base` segundos después de
crear una rama (justo cuando Lictor crea la suya), y la persona prepara sus ramas (`git switch -c`,
`git push -u`) mientras un run va en su worktree. Cada una de esas escrituras fallaba la
verificación en curso en `L0-estado`, deshacía en silencio lo que había escrito la persona, negaba
una reanudación, y el mensaje solo decía «`.git/config`».

## Decisión
1. **Lictor vigila las entradas de `.git/config`, no sus bytes** (`domain/git_config.py`). Una
   clave es inerte si está en la sección de una rama que no es la vigilada, o en la de la vigilada
   pero no es una que git lea de ella (`remote`, `pushRemote`, `merge`, `mergeOptions`, `rebase`,
   `description`: `mergeOptions` alcanza a `merge --ff-only`). Todo lo demás se vigila como antes:
   `core.*`, `remote.*`, `include.*`, filtros, ganchos.
2. **Un cambio solo de claves inertes no es manipulación ni se restaura**: lo que lo escribió lo
   conserva. Un cambio de una clave vigilada restaura el fichero entero, como antes, y se nombra por
   sus claves (`.git/config (core.hookspath)`, hasta cinco y cuántas más).
3. **La misma regla en el ancla**: la huella hashea las entradas vigiladas, no los bytes. Un
   comentario o el orden de las líneas no cambian la huella; el formato del ancla pasa a `3`.
4. **Las entradas las lee git, no Lictor**: `git config --file <copia> --no-includes --list -z`,
   sobre una copia, fuera del repositorio y sin configuración global ni del sistema, así que nada
   del repositorio corre al leerlas. Un fichero que git no lee como configuración cuenta como
   cambiado entero.

## Consecuencias
- Un editor con git abierto y las ramas propias de la persona dejan de romper verificaciones y
  reanudaciones.
- Una clave nueva que git lea de una rama, si git la añade, no estará en la lista hasta que se añada:
  se dice de menos solo para la rama vigilada, cuyas claves conocidas siguen vigiladas.
- La comprobación del ancla ejecuta `git config` (antes no ejecutaba ningún git): solo sobre una
  copia, fuera del repositorio.

## Cómo se comprueba
- La tabla de `test_git_config.py`; en `test_git_cli.py`, que una clave inerte ni cuenta ni se
  deshace, que una vigilada se nombra y cambia el ancla, y que un fichero ilegible cuenta entero.
- `test_v0_25_0_git_config.py`: con el agente simulado, una clave de editor no falla el intento y
  `core.hooksPath` sí, nombrada.
