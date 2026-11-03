# ADR-0004: El oráculo se valida antes de sellarse y un verde vale para la especificación de su commit

## Estado
Aceptada

## Contexto
El driver sellado decide qué escenario está en verde. La auditoría de la v0.1 mostró cuatro huecos en ese ciclo:

1. **Rojo sin valor (A2).** Cualquier fallo contaba como rojo: un driver sin aserciones (`f30`) o con un error propio (`d10`, `iva.gnral`) se sellaba, y la culpa caía en el escenario.
2. **Verdes que no caducan (M3).** Un verde se derivaba de cualquier trailer `Scenario:`; cambiar lo que pedía el escenario no lo reabría, y el driver no sabía qué escenarios funcionaban.
3. **Verdes colaterales (M2).** Un escenario que otro commit ya hacía pasar volvía al agente con un intento vacío, y `--new` lo rechazaba.
4. **Controles que dependen de una ruta (A7).** Sin `tests/unit`, el control `unit` salía con 4 y bloqueaba a un agente honesto.

El informe JUnit de pytest no dice la clase de la excepción, solo su texto, que controla el código bajo test.

## Decisión
1. **El perfil declara el oráculo** en un bloque `oracle`, que `lictor.json` no puede declarar:
   - `link: "scenario"`: cada test tiene que ejecutar su escenario de Gherkin desde el fichero de ese escenario, no basta con llevar su id en el nombre. Una copia con el mismo tag en otro fichero no vale, aunque esté bajo `features/`: `pytest-bdd` la lee aunque no sea `.feature`, y la especificación solo carga `*.feature`.
   - `red_kinds`: un pendiente vale como rojo si falla por una aserción o por `NotImplementedError` lanzado desde `paths.writable`, fuera del driver.
   - `impostors`: antes de sellar un driver nuevo, la aceptación se repite con cada stub sustituido por `return None` y por `return True`. Cada pendiente nuevo tiene que fallar con alguno. Un impostor que cae, no mapea o conserva un stub no prueba nada. Cualquier fallo cuenta como detectado, así que solo atrapan un test que ignora el valor devuelto: uno sin aserción que lee `calc(...).total` falla con los dos (`AttributeError`) y los supera.
2. **Los hechos vienen de un plugin del perfil** (`lictor_outcome`), copiado al entorno hasheado. Deja en el JUnit la clase y el origen del fallo, por la clase de la excepción, y el escenario que ejecutó cada test. La decisión es una regla pura del dominio.
3. **Un verde vale para la especificación de su propio commit.** Su huella es una forma canónica de lo que exige el escenario, calculada del `.feature` en el árbol del commit verde más reciente. Si difiere de la actual, el escenario se reabre. No hay trailer nuevo ni migración. Un pendiente que pasa en el rojo se juzga según el driver:
   - Con el driver sellado, su test quedó probado al sellarlo: un reabierto se vuelve a registrar sin agente con un commit vacío (pasa, por ejemplo, si el escenario no se encontró en el árbol de su commit verde, porque cambiar `features/` invalida el sello) y uno nuevo es un verde colateral.
   - Con un driver nuevo, nada distingue un test que pasa de uno vacío. Un nuevo se rechaza como reintentable (`sin_prueba`): en un proyecto nuevo `src/` solo tiene stubs, así que casi siempre es un test que el agente puede arreglar. Solo al agotar los intentos para con exit 10, y el mensaje dice primero que se corrija el test y después cómo registrarlo con un trailer `Scenario:` si un commit anterior ya lo implementaba.
   - Un reabierto que pasa con un driver nuevo para con exit 10 sin reintentar (`especificacion_cambiada`): el paso puede conservar el valor antiguo y la implementación antigua lo pasa. Solo una persona sabe si el cambio fue de redacción; si la implementación ya cumple la nueva especificación, lo registra con el trailer, y si no, su test no detecta el cambio.

   Así, antes del sello el rojo no registra nada: la rama solo avanza al commit del driver.
4. **Los colaterales se registran sin agente.** En la verificación, con un commit vacío encadenado al candidato (`Lictor-Collateral`), al que la rama avanza en un solo paso. En el rojo, con el driver sellado. El ancla acepta ese commit solo con el rojo pendiente, sobre la cabeza anclada y con su mismo árbol.
5. **`commit_tree` rechaza un commit vacío** salvo que se pida: un intento sin cambios falla en `L0-sin-cambios`.
6. **`when_exists`** permite omitir un control cuya ruta no existe ni en HEAD ni en el intento. Eliminarla hace fallar el control.

## Consecuencias
- Un driver nuevo cuesta dos ejecuciones más de la aceptación, solo si tiene escenarios nuevos pendientes.
- El driver de `python-uv` tiene que usar `pytest-bdd`, con cada `raise NotImplementedError` en una sola línea. Un perfil sin plugin no declara `oracle` y conserva las reglas anteriores.
- El plugin corre en el proceso del código bajo test, que puede forjar sus propiedades como ya podía forjar el informe entero (`c6`, incremento 4).
- Quedan sin cerrar: un test que llama a la interfaz y después falla siempre supera clase e impostores, y uno que solo falla porque no puede leer el resultado de un impostor también; un driver reconstruido puede debilitar el test de un verde, que solo tiene que pasar. Si todos los intentos fallan con un error del driver, la cuarentena lo señala como posible driver defectuoso, sin afirmarlo; no señala un driver con una aserción imposible de cumplir ni con un `pytest.fail` en un paso, porque los dos fallan como `assertion`.
- `Scenario Outline` sigue sin admitirse: cada escenario necesita exactamente un test.
- Registrar un escenario que ya pasa con un driver nuevo, nuevo o reabierto, exige hoy un commit vacío con el trailer, a mano.
