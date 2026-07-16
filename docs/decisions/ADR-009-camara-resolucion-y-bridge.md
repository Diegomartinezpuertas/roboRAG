# ADR-009: Resolución de cámara y bridge propios (no tocar /opt/ros/jazzy)

**Fecha:** 2026-07-14
**Estado:** Aceptado

## Contexto

`perceive` fallaba siempre con "No camera frame received yet", incluso sin
haber navegado nunca (se descartó por tanto cualquier hipótesis de
inanición de hilos durante `navigate`/`explore`). Diagnóstico con
`ros2 topic bw /camera/image_raw`: el modelo SDF de TurtleBot3 Waffle que
trae `turtlebot3_gazebo` publica la cámara a **1920×1080 (6.2 MB/frame,
~9Hz, ~55 MB/s)**. Con QoS `BEST_EFFORT` y el proceso `skills_executor_node`
compartiendo executor con Nav2, TF, clientes HTTP a Ollama y ChromaDB, los
frames se descartaban en la capa DDS antes de llegar al callback — nunca era
un bug de nuestro código, era volumen de datos.

Además, al reescribir `simulation.launch.py` para usar un modelo propio, se
introdujo un segundo bug real: la ruta al YAML del `parameter_bridge` estaba
mal (`waffle_bridge.yaml` en vez de `turtlebot3_waffle_bridge.yaml`), lo que
tumbaba TODO el bridge Gazebo↔ROS2 (`/odom`, `/tf`, `/scan`, `/cmd_vel`) sin
avisar más que con un `ERROR` de una sola línea fácil de perder entre el
ruido de "frame odom does not exist" — de ahí que tampoco se generase mapa
("Frame [map] does not exist").

## Decisión

- Copiar `turtlebot3_waffle/model.sdf` a `robot_bringup/models/` (nunca
  editar `/opt/ros/jazzy/`, regla de CLAUDE.md) y bajar la cámara a
  **640×480** (~30x menos datos por frame).
- `simulation.launch.py` ya no incluye el `spawn_turtlebot3.launch.py` de
  stock: spawnea directamente con nuestro `model.sdf`, y monta a mano
  `parameter_bridge` + `image_bridge` con la ruta correcta al YAML de stock
  (ese sí se puede seguir referenciando sin copiar, solo son mapeos de
  topics).

## Razones

- 640×480 es sobrado para la entrada de Qwen2.5-VL (que además reescala
  internamente) y elimina el cuello de botella de ancho de banda sin tocar
  el paquete del sistema.

## Consecuencias

- Si se actualiza `turtlebot3_gazebo` del sistema, nuestra copia del modelo
  puede quedar desincronizada de mejoras/fixes upstream — revisar diffs
  ocasionalmente.
- Cualquier otro sensor de alta resolución que se añada en el futuro debe
  vigilarse con `ros2 topic bw` antes de asumir que "no llega nada" es un
  bug de código.
