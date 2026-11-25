# ADR-0017: Un control de la API pública, comparada con el commit en que empezó el run

## Estado
Propuesta (2026-11-25), borrador para la 0.19.0. Sale de F80 (el primer uso real: la PR #1 de
verifactu-py, fase 4a de pyverifactu) y del diseño `.audits/qa/0-19-0/diseno.md`, revisado por su
cuenta en `.audits/qa/0-19-0/revision-fable.md` (hallazgos #1–#5, que cambiaron la definición y la
base). Sigue el patrón de las ADR-0013 y 0014: un módulo del perfil da hechos, el dominio da el
veredicto (ADR-0009) y el refactor no puede empeorar lo medido (ADR-0010).

## Contexto
En la fase 4a de pyverifactu el run dejó en verde todos sus escenarios y su rama no se podía
integrar tal cual: había quitado cuatro nombres públicos (`BlockchainChain.add_invoice`,
`BlockchainChain.verify_chain`, `storage.save_chain`, `storage.load_chain`) y la orden `register`
había perdido `--blockchain`. Ningún control lo veía: los tests de aceptación llaman a lo que el
escenario pide, no a lo que ya existía, y `CHANGELOG.md` y cinco ficheros de `docs/` seguían
citando lo quitado. En la misma rama hubo siete cambios de firma compatibles (parámetros nuevos con
valor por defecto).

Un primer script de firmas (funciones, clases y métodos definidos en módulos sin `_`) se probó
contra un repositorio sintético de diez cambios (Fable, #1): dio dos roturas falsas (una función
movida de `__init__.py` a un módulo y reexportada; un método subido a la clase base) y no vio ocho
reales (un reexporte quitado, la API definida en `_impl.py`, una constante, un miembro de `Enum`, un
campo de dataclass, un parámetro que pasa a solo por nombre, un método que pasa a propiedad, `def`
que pasa a `async def`). Las dos falsas son justo lo que `refactor_hints` pide («un módulo por
responsabilidad; `__init__.py` solo reexporta»): con esa definición el refactor que Lictor pide se
descartaría (#2).

## Decisión

1. **La API es lo alcanzable por nombre público, como dice PEP 8.** Un módulo es público si ninguna
   parte de su nombre empieza por `_`. Con un `__all__` estático (lista o tupla literal, también
   `+=`, `extend` y `append`) exporta exactamente eso; sin él, lo que define sin `_`, lo que toma con
   `from x import *` y, en el `__init__.py` de un paquete, lo que importa de sus propios
   subpaquetes: así expone un paquete lo que definen sus módulos. Cualquier otro import es un
   detalle de implementación (en pyverifactu, el `Invoice` que `cli/__init__.py` importaba de
   `models` no es API de `cli`). Cada nombre se sigue por sus `import` hasta su definición, también
   en módulos `_`, así que mover una definición y reexportarla conserva el nombre. Lo que solo
   importa `if TYPE_CHECKING:` no existe al ejecutar y no cuenta.

2. **Qué cuenta de cada nombre.** Funciones, clases, métodos, propiedades (`property`,
   `cached_property`; su setter no es otro nombre), métodos estáticos y de clase, constantes de
   módulo, atributos de clase, campos (nombres anotados de la clase, salvo `ClassVar`) y miembros de
   `Enum`; de una clase, sus miembros propios y los que hereda de clases del proyecto. De cada
   invocable, cada parámetro con su clase (solo posicional, posicional o por nombre, solo por
   nombre, `*args`, `**kwargs`) y si tiene valor por defecto, y si es `async`. El constructor de una
   clase es su firma: su `__init__`; el que construye una dataclass de sus campos (con `field(init=
   False)`, `default_factory`, `kw_only` y `KW_ONLY`), un `NamedTuple` o un modelo de pydantic (por
   nombre; `= ...` y `Field(...)` sin valor son obligatorios); o el más cercano que hereda del
   proyecto. Una clase cuya firma decide una base de fuera (una excepción, una librería) no tiene
   firma que comparar. Los parámetros de una orden de typer son parámetros: `--blockchain` sale.

3. **La mide un módulo del perfil.** `python_uv/lictor_api.py`, solo stdlib (`ast`), válido desde
   Python 3.9, en `verification.files`: no importa ni ejecuta el código del proyecto, no lee
   configuración. El control es `api` (L1, `measure`, `report: "api"`):
   `uv run python -I -B -m lictor_api src --out {out}`. Su informe son hechos (`ApiReport`, que
   lee `ApiReader` con `json_report`): cada nombre con su clase, su origen (módulo y nombre
   cualificado donde se define), fichero y línea, `async` y parámetros. Dos nombres con el mismo
   origen son un objeto alcanzado de dos maneras.

4. **Una sola base: el commit en que empezó el run** (Fable, #3). `preflight` guarda en el estado
   su SHA (`api_base`) y nada más. El informe de la base se calcula ejecutando el mismo control en
   un clon limpio de ese commit (`ApiBaseline`, `application/api_baseline.py`), dentro de
   `IntegritySnapshot` como cualquier control porque un proyecto puede cambiar su orden, y se
   guarda en memoria por commit: un run lo mide una vez (#4). Nunca se lee de `runs/`, que ninguna
   decisión lee cuando su paso ha terminado; la evidencia (`api-base.json`, `api-base.log`) queda
   en la verificación que lo midió. Una base que no se puede medir no es una comparación: el
   control falla diciéndolo, y se vuelve a medir en la verificación siguiente. Un run reanudado
   conserva su `api_base`; lo que cambie el driver (que escribe `src` y cuyo rojo no ejecuta
   controles) se compara con la misma base.

5. **Qué es una rotura** (`domain/api.py`, `all_breaks`). Un nombre de la base que deja de ser
   alcanzable (`removed`); que pasa a ser otra cosa (`kind`: función → propiedad, método → método
   estático, clase → función); que pasa a ser o deja de ser `async`; y en una llamada: un parámetro
   que desaparece o cambia de nombre, uno que pierde una forma de pasarse (posicional o por nombre →
   solo por nombre), uno posicional que cambia de sitio, uno que pierde su valor por defecto, uno
   nuevo sin valor, y un `*args` o `**kwargs` que desaparece. Ganar una forma de pasarse, o un valor
   por defecto, no rompe; un solo posicional que cambia de nombre tampoco. Lo que cuelga de un nombre
   roto (los miembros de una clase quitada, o que dejó de ser clase) no se cuenta otra vez. Los
   nombres de un mismo objeto que se rompen igual son una rotura, contada por su nombre más corto,
   con los otros como alias. Mover una definición y reexportarla, o subir un método a la base, no
   rompe nada.

6. **`measure` en el perfil, `block` por proyecto.** En una aplicación la API es interna; en una
   librería con usuarios, no. El veredicto (`api_verdict`) es el mismo en los dos modos: falla
   nombrando lo roto que la persona no aceptó, y sin base (un `check` sin `--base`) solo da hechos y
   pasa. En `block`, el mensaje al implementador nombra lo roto y le dice que, si el escenario no se
   puede cumplir sin romperlo, declare `LICTOR: SPEC_CONFLICT`: el intento se verifica igual y, si
   falla, la cuarentena es `spec_conflict` (F54).

7. **La persona acepta roturas en `lictor.json`** (Fable, #5). `api.accepted`: nombres
   cualificados cuya rotura acepta, el suyo o el de lo que lo contiene (`pkg.storage` acepta
   `pkg.storage.save_chain`), también por un alias. Es una decisión del proyecto, que cambia
   veredictos: entra en `config_fingerprint`. Qué rotura pide el escenario no se distingue ni se
   intenta: lo decide la persona.

8. **El refactor no puede romper nada que su verde no rompía.** `refactor_regressions` compara el
   **conjunto** de roturas respecto a la base del refactor con el del verde (`Measure.breaks`), no
   un recuento (Fable, #2): un refactor que repara una y rompe otra se descarta en `L0-regresión`.
   Vale en los dos modos, porque un refactor no cambia comportamiento. Su prompt lo dice
   (`REFACTOR_API`) cuando el verde se comparó con su base.

9. **Lo que oye cada uno.** El implementador, con el control activo, una frase fija en
   `{medidas}` («no quites ni cambies de forma incompatible la API pública que ya existía; lo que
   rompas sale en el informe»), en los dos modos: en `measure` no oiría nada y la persona se
   enteraría al final del run, que es el coste de F80. `RunProgress` da ⚠ en el verde que rompe
   algo. `verify.finished` guarda `api: {symbols, base, broken, accepted, problem}`
   (`RecordedApi`). `report.md` tiene una sección «API pública» con lo roto respecto a la base en la
   última verificación que pasó y, por cada nombre, dónde se cita fuera de `paths.writable`
   (`TextSearch.cited_in`: la palabra entera, como cadena fija, en los ficheros de HEAD).
   `lictor check` da los hechos (cuántos nombres públicos) sin base y sin veredicto; con
   `--base <rev>` compara, con la misma medida en un clon de esa revisión.

## Consecuencias
- Un control más, sin dependencias; cambia el perfil (control y `verification.files`) y
  `lictor.json` gana `api`: va al CHANGELOG, y un run a medias no se reanuda.
- Cada run mide una vez más, en el primer paso que verifica: un clon de la base y el módulo, que
  en `src/lictor` (2320 nombres) tarda unas décimas.
- Ve firmas, no comportamiento: `register_invoice` roto sin cambiar de firma (G1 de la revisión
  de la PR #1) no lo ve, ni el formato de lo que se guarda (`.json` → `.jsonl`).
- Ve lo que el código dice, no lo que hace al ejecutarse: un `__all__` calculado, un
  `setattr(módulo, …)` o un decorador que cambia la firma no se siguen. Un `__all__` que no es
  literal se trata como si no estuviera.
- Lo que una dependencia decide (la firma de una clase con base de fuera) no se compara.
- Solo protege lo que existía al empezar: lo que crea el propio run (los stubs del driver, lo que
  implementa cada escenario) es nuevo y puede cambiar. Y lo que el driver rompe de la base cuenta
  igual: en la plantilla de los casos de repro, sus stubs sustituyen a `iva.hello`. En `block`, una
  rotura del driver, que está sellado, hace fallar todos los intentos de todos los escenarios: la
  persona la acepta en `api.accepted` o corrige el driver.

## Cómo se comprueba
`tests/unit/test_lictor_api.py` fija la definición con una tabla de casos: los diez del repositorio
sintético de Fable (8 roturas reales vistas y las 2 falsas, que no salen) y la fase 4a de
pyverifactu reducida a lo que decidió: exactamente los cuatro nombres quitados y la rotura de
`cli.register` por `blockchain`, con los siete parámetros añadidos con valor por defecto como
compatibles. Sobre los árboles reales (`main` frente a `lictor/fase-4a`) el módulo da lo mismo, en
Python 3.12 y 3.9. `tests/unit/test_api.py` fija cada regla del dominio, el diario, el punto de
control y el refactor; `tests/unit/test_api_baseline.py`, la base medida una vez en un clon de su
commit y lo que hace con ella una verificación del implementador y del refactor.

Queda por medir en una pasada real: una pasada de pyverifactu con `api` en `block` y
`api.accepted` vacío, para ver si el implementador declara el conflicto o busca una forma
compatible, y cuántos refactors descarta este control.
