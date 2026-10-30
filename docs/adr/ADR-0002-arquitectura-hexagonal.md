# ADR-0002: Arquitectura Hexagonal (Puertos y Adaptadores)

## Estado
Aceptada

## Contexto
Lictor es un sistema intensivo en operaciones de entrada/salida (agentes CLI, git, procesos del sistema, archivos de configuración, reportes JUnit/XML, bases de datos SQLite y terminal). Además, debe ser completamente agnóstico respecto al lenguaje del proyecto bajo prueba y al modelo o proveedor del agente de IA, creciendo modularmente a lo largo de once incrementos funcionales.

## Decisión
Estructurar Lictor siguiendo el patrón de **Arquitectura Hexagonal (Ports and Adapters)**:
1. **Dominio puro (`lictor.domain`):** contiene modelos de datos inmutables (dataclasses frozen), reglas de negocio (regla del rojo, mapeo de IDs, condensación de errores) y funciones puras de enrutado. Prohibido importar frameworks o módulos de E/S.
2. **Capa de aplicación (`lictor.application`):** define los puertos (`ports.py` vía `typing.Protocol`) y coordina casos de uso individuales.
3. **Adaptadores (`lictor.adapters.*`):** implementan los puertos de forma independiente y sin conocerse entre sí (`agents`, `vcs`, `runner`, `readers`, `spec`, `store`, `config`).
4. **Orquestación (`lictor.orchestration`):** gestiona el grafo de estados de LangGraph recibiendo adaptadores inyectados.
5. **Composición (`lictor.bootstrap` / `lictor.cli`):** única raíz donde se instancian y enlazan las dependencias.

Las dependencias apuntan estrictamente hacia dentro y se verifican de forma automatizada mediante contratos de `import-linter` en cada verificación de calidad.

## Consecuencias
- Requiere una estructura de directorios y módulos más extensa desde el inicio del proyecto.
- Permite probar el 100% de la lógica de dominio en milisegundos sin llamadas externas, red ni consumo de cuotas de LLM.
- La incorporación de nuevos agentes (Claude, OpenAI Codex, Antigravity) o formatos de reporte se realiza añadiendo adaptadores aislados sin alterar el núcleo.
- Los contratos de arquitectura impiden que agentes autónomos degraden la modularidad durante las fases de auto-implementación.

