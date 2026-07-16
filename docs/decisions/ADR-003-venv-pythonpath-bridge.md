# ADR-003: Puentear agent_env con PYTHONPATH en lugar de activar el venv

**Fecha:** 2026-07-14
**Estado:** Aceptado

## Contexto

Los nodos ROS 2 (`rclpy`) están instalados para el Python de sistema
(`/usr/bin/python3`, el que usan `colcon`/`ros2`), mientras que las
dependencias del agente (`chromadb`, `langchain`, `ollama`, `cv_bridge`
indirectamente vía `numpy`) viven en el venv `agent_env`. Al construir
paquetes `ament_python` con `colcon build --symlink-install`, el
`console_scripts` generado queda con el shebang `#!/usr/bin/python3`
apuntando siempre al Python de sistema, sin importar si `agent_env` está
activado en la shell que ejecuta `colcon build`.

Activar `agent_env` (que reemplaza `python3` en el `PATH`) antes de
`ros2 run <pkg> <node>` no soluciona nada porque el shebang ya está fijado; y
si además se usa `agent_env`'s `python3` para invocar el nodo directamente,
faltan `rclpy` y los paquetes `cv2`/`cv_bridge` del sistema.

## Decisión

No activar `agent_env` para build ni para ejecución de nodos ROS 2. En su
lugar, `setup_env.sh` exporta:

```bash
export PYTHONPATH="/home/diego/robot_ws/agent_env/lib/python3.12/site-packages:${PYTHONPATH}"
```

de forma que el Python de sistema (el que ROS 2 y los `console_scripts`
esperan) también resuelve `chromadb`, `langchain`, `ollama`, etc.

## Razones

- Evita reconstruir el workspace con un intérprete no soportado por `rclpy`.
- Mantiene `cv_bridge`/`cv2` (instalados vía apt para el Python de sistema)
  funcionando sin conflicto de ABI.

## Consecuencias

- `numpy` en `agent_env` debe mantenerse en la serie 1.x (`numpy<2`) para
  coincidir con el ABI del `cv2` de sistema (compilado contra NumPy 1.x). Si
  `agent_env` sube a NumPy 2.x, `cv_bridge` falla con
  `ModuleNotFoundError: numpy.core.multiarray failed to import`.
- Cualquier terminal nueva debe hacer `source ~/robot_ws/setup_env.sh` (en vez
  de activar `agent_env` a secas) antes de compilar o lanzar nodos.
