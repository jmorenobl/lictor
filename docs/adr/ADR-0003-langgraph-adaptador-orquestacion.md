# ADR-0003: LangGraph como Adaptador de Orquestación, no Núcleo del Sistema

## Estado
Aceptada

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

