# ADR-007: MultiThreadedExecutor + callback groups para llamadas bloqueantes

**Fecha:** 2026-07-14
**Estado:** Aceptado

## Contexto

`llm_planner_node` y `skills_executor_node` hacen llamadas a servicios ROS 2
*desde dentro de* callbacks (el planner llama a `/skills/execute` desde el
callback de `/robot/goal`; skills llama a `/rag/update_map` desde el callback
de `/skills/execute`). Con un executor single-threaded esto no puede
resolverse esperando dentro del callback, porque el único hilo que podría
procesar la respuesta está ocupado en el propio callback.

Se intentaron dos aproximaciones fallidas antes de la definitiva:

1. `rclpy.spin_until_future_complete(node, ...)` anidado →
   `RuntimeError: Executor is already spinning`.
2. Executor temporal (`SingleThreadedExecutor()` + `add_node/remove_node`
   alrededor de cada espera) → **el nodo queda sordo** al terminar el
   callback: `add_node` transfiere la propiedad de las entidades
   (suscripciones incluidas) al executor temporal y el executor principal
   deja de despacharlas. Este fue el fallo real detrás de los goals
   "perdidos" que inicialmente se atribuyó al discovery DDS.

## Decisión

- Cada nodo con llamadas bloqueantes corre en su **propio
  `MultiThreadedExecutor`** (creado en `main()`, nunca el global).
- Los **clientes de servicio** (y las suscripciones que deben seguir vivas
  durante un callback largo, como `/map` y la cámara durante `explore`) van
  en un **callback group separado** (`MutuallyExclusiveCallbackGroup` para
  clientes del planner, `ReentrantCallbackGroup` para el grupo de I/O de
  skills). El resto queda en el grupo por defecto, que serializa los skills.
- Las esperas usan **`threading.Event`** sobre `future.add_done_callback`
  (con timeout y `future.cancel()`), sin tocar ningún executor.
- El **executor global del proceso queda libre** para
  `nav2_simple_commander.BasicNavigator`, que internamente hace
  `rclpy.spin_until_future_complete(self, ...)` sobre él.

## Consecuencias

- Prohibido en este repo: `rclpy.spin_*` dentro de callbacks y el patrón de
  executor temporal.
- Los skills siguen ejecutándose de uno en uno (grupo por defecto
  mutuamente exclusivo) — el comportamiento observable no cambia.
- `TransformListener` usa `spin_thread=True` (hilo propio) para que TF siga
  actualizándose durante skills largos.
