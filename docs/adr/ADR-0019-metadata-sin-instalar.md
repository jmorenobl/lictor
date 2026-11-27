# ADR-0019: La metadata del proyecto, sin instalarlo

## Estado
Aceptada (2026-11-27), en la 0.22.0. Sale de F82 (agentix y pyverifactu) y del diseño
`.audits/qa/0-22-0/diseno.md`, §2, revisado por Fable en `.audits/qa/0-22-0/revision-fable-f82.md`
(el bloqueante B1 y los importantes I1 a I6 cambiaron cuándo se registra, cómo se lee la versión y
que lo que no se registra se vea). Para la versión que sale de git, la opción A: no se registra nada.

## Contexto
El entorno de verificación no instala el proyecto, a propósito: una instalación editable importaría
el árbol del agente, no el del clon, y una normal, el de la base. El código se importa desde `src`,
que `lictor_project_path` pone al final del path dentro de pytest.

Pero `importlib.metadata` no lo encuentra:

| Proyecto | Qué hace | En la verificación |
|:--|:--|:--|
| agentix | `__version__ = version("agentix")` en `src/agentix/__init__.py` | 24 errores de recogida (`PackageNotFoundError`): «Los controles pasan en HEAD» en ✗, y el proyecto no se adopta sin tocar su código |
| pyverifactu | lee su grupo `verifactu_py.certificate_providers` con `entry_points()` | el grupo sale vacío, y nada lo dice |

De los 14 proyectos Python de `~/code`, Lictor incluido, 13 declaran una versión estática (4 sin
`[build-system]`), 1 la lee de un fichero (hatch) y ninguno la saca de git.

## Decisión

1. **El plugin que ya pone `src` en el path registra también la distribución del proyecto, en
   memoria.** Un `DistributionFinder` al final de `sys.meta_path`, el punto de extensión que documenta
   `importlib.metadata`, da una distribución con `METADATA` (nombre y versión) y `entry_points.txt`.
   No hay ficheros: ni un `.dist-info` en el entorno, ni en el clon, ni en un temporal. Se registra en
   `pytest_load_initial_conftests`, después de `src` y del `pythonpath` del proyecto y antes de
   cualquier `conftest.py`, en cada proceso de pytest: los workers de xdist y un
   `sys.executable -m pytest` lanzado por un test cargan el plugin por `PYTEST_PLUGINS`, que heredan.
2. **Lo lee `lictor_metadata.py`, un módulo del perfil sin pytest, del `pyproject.toml` del clon y
   sin ejecutar nada** (`tomllib` o `tomli`, `re`, `ast`; `packaging` si está). Lo usan el plugin y
   el survey. Sale del clon, no de la base: si el agente cambia el fichero de la versión, se verifica
   lo que se confirma.

   | Campo | De dónde sale |
   |:--|:--|
   | `Name` | `[project].name` |
   | `Version` | `[project].version`, o la versión dinámica de la tabla de abajo, normalizada con `packaging` como hacen los backends (`1.0.0-dev` es `1.0.0.dev0`); una inválida no se registra |
   | Entry points | `[project.scripts]`, `[project.gui-scripts]` y cada grupo de `[project.entry-points]`, **menos `pytest11`** |

   | Versión dinámica | Cómo se lee |
   |:--|:--|
   | hatchling, `tool.hatch.version.path` con `source` ausente o `regex` | su `pattern` o el de hatchling 1.33.0: `(?i)^(__version__\|VERSION) *= *(['"])v?(?P<version>.+?)\2`, con `MULTILINE` |
   | setuptools, `dynamic.version = {attr = …}` | `ast`: la primera asignación de ese nombre a un literal entre las sentencias del módulo (una tupla se une con puntos), siguiendo `package-dir`, o en `src/` y en la raíz |
   | setuptools, `dynamic.version = {file = "…"}` | el contenido del fichero, sin blancos; una lista no se lee |
   | flit | `__version__` del módulo (`tool.flit.module.name` o el nombre del proyecto), con el mismo lector |
   | git (`source = "vcs"`, `setuptools_scm`, pdm `scm`), hatch `source = "code"`, pdm, cualquier otro | **no se registra nada** |

3. **Solo se registra lo que `uv` instalaría:** hay `[build-system]` y `tool.uv.package` no es
   `false`, o `tool.uv.package = true`. Sin eso `version()` funcionaría en la verificación y fallaría
   en la máquina de la persona y en su CI: un falso verde. La metadata existe en la verificación
   cuando existiría al instalar el proyecto. Un proyecto así no es un paquete, y la verificación es
   fiel sin ella: `doctor` lo dice con ✓ («sin metadata del proyecto: …»), sin nada que arreglar.
4. **Gana la instalada.** Si `distribution(<nombre>)` ya responde, no se registra nada: con las dos,
   `distributions()` las daría ambas en todas las versiones, y `entry_points()` duplicaría el grupo en
   3.9.
5. **Un solo buscador por proyecto.** Lo que ya se encuentra gana, también el buscador de un
   `pytest.main()` de fuera del mismo proyecto: el de dentro no añade otro, y uno de otro proyecto, o
   de ninguno, deja el de fuera como estaba. El buscador solo responde a una búsqueda que mira el
   `src` del proyecto (`distributions(path=[…])` sin él no lo encuentra).
6. **La distribución es de la biblioteca que pregunta.** Es un mixin que el buscador une al
   `Distribution` del módulo del `Context` que recibe: la biblioteca estándar recibe una suya, y el
   backport `importlib_metadata` (en 3.9) una del backport, con sus entry points y los métodos que les
   pide (`matches` en la 3.10.1). Define `name` y `_normalized_name`, que la estándar de 3.9 no tiene y
   el backport pide a toda distribución; `locate_file` da un `Path` bajo `src`.
7. **Sin `pytest11`.** La carga automática está apagada y la lista de plugins sale del entorno antes
   del hash, pero con `pytest11` un `pytester.runpytest_inprocess` con la carga encendida cargó el
   plugin del proyecto (Fable lo ejecutó). No se anuncia nunca como plugin algo que es código del
   proyecto.
8. **Lo que no se registra se ve.** El survey da el hecho `metadata`: `{name, version, groups,
   unread}`, `{not_installed}` (punto 3) o `{problem, fix}`, con un `fix` que habla de la versión. `doctor` y un run nuevo lo dicen en «Dependencias de la
   verificación»: ✓ con nombre, versión y grupos, o ⚠ con la razón («la versión sale de git»,
   «pyproject.toml no tiene [build-system]: uv no instala el proyecto…») y qué hacer. Es un aviso, no
   un ✗: un código que no la lee no pierde nada. Con `dynamic = ["scripts"]` o `["entry-points"]` se
   registran nombre y versión sin ellos, y el ⚠ lo dice. La lectura nunca lanza: lo que no entiende
   (un tipo equivocado, un fichero fuera del clon o con un byte nulo, un `pattern` roto, un nombre
   que no cumple la PEP 508) es una razón. Es más estricta que setuptools en la dirección segura: sin
   `build-backend` una versión dinámica no se lee, ni un `__version__` que no sea un texto o una
   tupla; con `version` y `dynamic` a la vez, gana la dinámica.

## Lo que se descarta

| Alternativa | Por qué no |
|:--|:--|
| Instalar el proyecto en el entorno | Importaría el árbol del agente (editable) o el de la base, no el del clon que se verifica |
| Un `.dist-info` en disco | Un directorio por proceso de pytest que hay que borrar, y nada que no dé ya la memoria salvo `pkg_resources`, obsoleto, que ningún proyecto medido usa |
| Un detector en el survey del código que lee su metadata | Con `ast` no ve `version(__name__)` ni lo que pasa por una variable (Fable, #21 de la 0.19.0) |
| Registrar una versión inventada (`0+lictor`) cuando sale de git | Un dato que decide ramas (`version() >= "1.0"`) y deja pasar un test que la compara. El clon no tiene `.git` y esos proyectos suelen importar un `_version.py` generado que tampoco está. Si aparece un caso, la vía es que la persona declare la versión con el mecanismo de su ecosistema (`SETUPTOOLS_SCM_PRETEND_VERSION`, `fallback_version`) |
| `Requires-Dist`, extras y `files()` | Ningún proyecto medido los lee, y escribir bien los marcadores es mucho para nada |

## Límites

- **Un subproceso sin pytest** (`python -c`, un script) no ve la metadata, igual que no ve `src`. En
  agentix, con todo `tests`, 18 casos lanzan `sys.executable -c …` y fallan con
  `ModuleNotFoundError` (3900 pasan): no es F82, es el arranque del intérprete, que el proyecto no
  puede tocar. Su `pre-commit` ejecuta todo `tests`, y el contrato de arranque lo dice.
- `pkg_resources` no la ve; `packages_distributions()` no nombra el paquete; `requires()` y
  `files()` son `None`.
- Un `console_scripts` aparece en `entry_points()`, pero no hay ejecutable en el entorno.
- Un plugin del entorno que importa el proyecto en su `pytest_load_initial_conftests` con
  `tryfirst` (pytest-django) corre antes: el mismo límite que `src`.
- `scripts` o `entry-points` dinámicos no se leen (se avisa).
- Un proyecto que es un plugin de pytest no ve su `pytest11`, como hasta ahora.
- El agente ve otra cosa en su copia: allí `uv run` instala el proyecto de verdad, con su
  ejecutable y su `pytest11`. F82 acerca los dos mundos, no los iguala.
- Lo que carga un entry point del proyecto pasa por el buscador del impostor como cualquier módulo
  de `writable`.
- Cambia `config_fingerprint`, porque cambia el contenido de `verification.files`: un run de antes
  no se reanuda. No cambia el hash del entorno ni el árbol bajo test.

## Consecuencias

- agentix: «Los controles pasan en HEAD» pasa de ✗ (24 errores de recogida en `unit`) a ✓; `unit`
  da 2652 passed y 1 skipped, y `lint`, `format` y `types` pasan.
- pyverifactu: su grupo `verifactu_py.certificate_providers` y su versión se ven dentro de pytest.
- Lictor es su primer caso: en `lictor check` de Lictor, su `__version__` deja de ser
  `0+desconocida`.
- Probado en 3.9 (estándar, backport 3.10.1 y 8.7.1), 3.12 y 3.14, con `-W error`. La CI lo prueba
  con el backport en cada plataforma: `importlib-metadata` está en el grupo de desarrollo de Lictor
  solo para eso.
