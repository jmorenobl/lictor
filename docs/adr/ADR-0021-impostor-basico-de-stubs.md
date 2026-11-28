# ADR-0021: El impostor básico de stubs, para un perfil sin plugin

## Estado
Aceptada (2026-11-28), en la 0.24.0. Sale del diseño `.audits/qa/0-24-0/diseno.md`, revisado dos
veces por Fable (`revision-fable.md`: un bloqueante y ocho importantes en la primera, ninguno en la
segunda). Decisiones D1 a D11 de Jorge, como las recomienda el diseño (§10). Enmienda la ADR-0012
(la regla C es la de un perfil con `red_kinds`) y la ADR-0020 (el nivel intermedio que reservaba).
Trae también el arreglo de G3: el commit del driver pasa los controles que bloquean.

## Contexto
La 0.23.0 sacó el perfil `generic` con un falso verde declarado (ADR-0020, D11): un test que no
compara nada falla en el rojo porque la interfaz no está implementada, y pasa después con cualquier
código. La regla C de la ADR-0012 no vale sin el plugin de `python-uv`: lee la clase del fallo, el
paso y el marco, y sin ellos rechaza también tests correctos (Fable, 0.23.0). Una constante sola no
basta: con dos escenarios de signo contrario, ninguna hace fallar a los dos (B-S1). Y Lictor no sabe
escribir `return false` con el tipo de un lenguaje que no conoce.

En la pasada de Go, además, el driver sellado entró en la rama con 19 avisos del lint (G3): es el
único commit que Lictor hace sin pasar los controles, y uno que no los pasara bloquearía cada
intento sin que el implementador pudiera arreglarlo.

## Decisión

1. **Las constantes las escribe el driver, en la línea del stub.** Un perfil con
   `oracle.declared_impostors` pide que cada stub falle en una línea sola con la marca
   `lictor: no implementado`, y que esa línea acabe con un comentario
   `lictor-impostor: <A> | <B>`: dos sentencias que la sustituyen devolviendo dos valores
   distintos de su tipo, **ninguno su cero** salvo en un booleano, nunca nulos, y que solo usan lo
   que ya usa la firma; una sola si la función no devuelve nada. Lictor solo lee el texto
   (`domain/declared_stubs.py`): lo comprueba en las líneas que añade el driver, antes de correr
   nada, y en la variante *k* sustituye la línea entera. Al implementar, la línea se va con su
   marca: no queda nada en el código.
2. **Sin cero** (D9): con un cero, el débil «no está vacío» y el vacuo que solo indexa fallan con
   él y se aceptarían. Sin cero pasan con las dos y se rechazan, y el correcto que espera el cero
   falla con las dos y se acepta (medido). El precio: un escenario que solo pide que haya
   resultado («recibe un identificador») no se sella; el run para y pide un valor comparable. De
   370 escenarios distintos de todas las pasadas, ninguno era así.
3. **El juicio sin `red_kinds`** (`domain/basic_stub_proof.py`) lee hechos de cada variante: si
   pasó, y si su fallo aún lleva la marca. Por test pendiente nuevo, en este orden: alguna variante
   con la marca → `sigue_sin_implementar`; pasa con todas → `sobrevive`; pasa con una y falla con
   otra → `probado`; falla con todas → `probado` si su rojo llevaba la marca, `sin_stub` (aceptado
   sin comprobar) si no. `sobrevive` y `sigue_sin_implementar` se rechazan, reintentables.
4. **Qué pasada cuenta** (D3): una variante con informe fiable y todo emparejado. Una que no, no
   cuenta, salvo que algún test pase con la otra: entonces el driver se rechaza con la salida de la
   que cayó. Un emparejamiento ambiguo en una variante rechaza siempre: un `t.Run` al que llega una
   constante se rechaza antes del sello (G1).
5. **Un driver sin marcas** se sella sin comprobar sus tests nuevos, y lo dicen el sellado (⚠),
   `status` y `report.md` (D4): un escenario que se cumple con un campo nuevo, sin función nueva,
   es legítimo.
6. **El nivel «resultado leído»** (`proof_level`, D2): con un impostor de stubs (la regla C o las
   constantes del driver) y sin el oráculo entero. «Resultado probado» sigue exigiendo la regla C:
   las constantes del driver nunca lo dan, haya lo que haya además.
7. **`generic@0.2`** declara `declared_impostors`; `generic@0.1` queda retirado con su mensaje
   (D11). Sigue en `alpha` (D7).
8. **El commit del driver pasa los controles que bloquean** (G3, D5), tras el rojo y los
   impostores, en un clon limpio y dentro de `IntegritySnapshot`, como «Los controles pasan en
   HEAD». Sin mínimos de puntuación: los stubs están sin cubrir a propósito. Un control que un
   driver correcto no puede pasar lo exime la persona con `"driver": false` en `lictor.json`. La
   orden del driver pide sus arreglos, como la del implementador, y un solo rechazo junta los
   motivos del impostor y de los controles (`max_driver_attempts` sigue en 2, D10).
9. **La fuente y el juicio van aparte**: un núcleo futuro puede traer un reescritor de stubs por
   tipos y usar este juicio, o la regla C si trae `red_kinds`.

## Consecuencias
- `generic` deja de dar verde a un test vacuo o débil que llega a un stub. Los dos
  `@known_limitation(until="0.24.0")` se convierten en casos que se rechazan.
- Lo que no cierra lo dice el nivel: el test que copia el valor del código, la comparación más
  débil que la igualdad que las dos constantes rompen, una constante cero o nula escrita por
  error, y un fallo con todas que no es la comparación.
- El rojo de un driver con stubs cuesta dos pasadas más de la aceptación (0,11 s cada una en el
  juguete de Go, con la caché caliente) y una verificación de los controles que bloquean.
- Un driver que no pasa el lint del proyecto ya no se sella: se rechaza, reintentable, con la
  salida del control; al agotar los intentos la nota dice qué cambiar.

## Cómo se comprueba
- Las tablas de `test_declared_stubs.py` y `test_basic_stub_proof.py`, y `test_proof_level.py`.
- Los casos `toolchain` de `test_v0_24_0_toolchains.py` (Go y Node con el agente simulado y un
  oráculo que no usa Lictor), y `test_v0_23_2_toolchains.py` para el `t.Run`.
- Una pasada pequeña con el modelo real antes de la PR (D8).
