# ADR-0003: LangGraph como Adaptador de Orquestación, no Núcleo del Sistema

## Estado
Aceptada, revisada (ver «Revisión» al final)

## Contexto
Lictor requiere una máquina de estados para dirigir el ciclo de vida de los escenarios (preflight, driver, red_check, implement, verify, commit, quarantine, pause, report). Se requiere la capacidad de evolucionar o reemplazar el motor de orquestación en el futuro (p. ej., ADK, workflows puros de Python o sistemas distribuidos) sin reescribir la lógica de negocio ni los casos de uso.

## Decisión
Tratar a LangGraph como un adaptador dentro de `src/lictor/orchestration/`:
1. **Nodos delgados:** cada nodo del grafo contiene entre 3 y 5 líneas de código dedicadas exclusivamente a traducir el estado plano del grafo a tipos del dominio, invocar un caso de uso de `lictor.application.usecases` y registrar el resultado de vuelta en el estado.
2. **Enrutado desacoplado:** las funciones de decisión condicional residen en `lictor.domain.routing` como funciones puras basadas únicamente en datos, y el grafo de LangGraph solo actúa como invocador.
3. **Persistencia nativa:** el checkpointer (`SqliteSaver`) pertenece a LangGraph y se utiliza directamente en la capa de orquestación sin crear una capa de abstracción artificial innecesaria (principio YAGNI).
4. **Estado plano y serializable:** el estado del grafo (`TypedDict`) almacena tipos serializables simples y resúmenes, convirtiendo hacia y desde los modelos del dominio en las fronteras.

## Consecuencias
- Si se reemplaza LangGraph por otra biblioteca de flujos de trabajo, solo se modifica el paquete `lictor.orchestration`, manteniendo intactos el dominio y los casos de uso.
- Facilita las pruebas unitarias directas de las reglas de enrutado sin necesidad de instanciar un grafo completo ni checkpointers en memoria.
- Reduce la complejidad cognitiva de cada nodo, facilitando la depuración y auditoría del flujo de ejecución.

## Revisión (2026-11, plan de corrección)
Siguen en pie el enrutado puro en `lictor.domain.routing`, el checkpointer nativo de LangGraph y el estado plano y serializable. **No se cumple la promesa de nodos de 3 a 5 líneas**, y se deja de prometer.

Los nodos miden hoy entre 5 y 80 líneas (`implement` y `verify`, las más largas). Además de traducir el estado y llamar a un caso de uso, hacen cuatro cosas que la decisión original no preveía:

1. Emiten los eventos de la ejecución (`run.started`, `scenario.reopened`, `spec.loaded`…).
2. Fijan el motivo y el código de salida (`_finish`), para que toda salida se explique a sí misma.
3. Retienen el cerrojo del checkpoint mientras corre código del agente o del proyecto (`_holding_checkpoint_writes`).
4. Traducen errores de dominio a señales del grafo (`blocked`, `rejected`).

Las decisiones siguen fuera de los nodos: el enrutado y las reglas (`classify_agent`, `keeps_attempt`, `precondition_exit_code`…) están en el dominio, con sus tests y con mutación. Reemplazar LangGraph sigue tocando solo `lictor.orchestration`, pero cuesta más que reescribir unas cuantas líneas por nodo.

**Deuda explícita:** adelgazar los nodos, moviendo los eventos y la traducción de resultados a los casos de uso. No se hizo en el plan de corrección porque toca el comportamiento ante una muerte del proceso, que comprueba la matriz `kill -9`, y no cerraba ningún hallazgo.

