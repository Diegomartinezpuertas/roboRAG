# ADR-005: Dashboard web con FastAPI embebido en un nodo ROS 2

**Fecha:** 2026-07-14
**Estado:** Aceptado

## Contexto

Se necesita observabilidad en tiempo real del pipeline cognitivo (goal →
plan → skills → respuesta), acceso a los logs de todos los nodos, y una vía
de entrada de comandos que sirva también para voz. Las opciones evaluadas:
rosbridge_suite + página externa, RViz plugins, o un servidor HTTP embebido.

## Decisión

Nuevo paquete `robot_dashboard` con un único nodo (`dashboard_node`) que:

- Corre FastAPI + uvicorn en un hilo daemon dentro del propio nodo ROS 2.
- Mantiene un ring buffer thread-safe de eventos alimentado por
  suscripciones a `/robot/goal`, `/robot/status`, `/robot/response`,
  `/rosout` y `/map` (metadata).
- Sirve una SPA embebida (HTML en `web_page.py`) que hace polling a
  `GET /api/events?since=<id>` cada ~700ms y publica goals vía
  `POST /api/goal`.
- La entrada de voz usa la Web Speech API del navegador (Chrome/Edge,
  `lang=es-ES`) — el reconocimiento corre en el navegador de Windows, sin
  consumir VRAM del robot ni depender del NPU (inaccesible desde WSL2).

## Razones

- **Polling REST vs WebSocket/rosbridge:** para un panel local de un solo
  usuario, el polling elimina toda la complejidad de threading asyncio↔rclpy
  (un `Lock` basta). rosbridge añadiría un proceso más y protocolo genérico
  que no necesitamos.
- **HTML embebido como string:** evita el manejo de `data_files` y funciona
  directamente con `--symlink-install`.
- **Voz en el navegador:** disponible hoy sin cargar Whisper; la fase de voz
  nativa (NPU + Whisper en Windows) queda como evolución futura y podrá
  publicar en el mismo `/robot/goal`.

## Consecuencias

- El dashboard es solo para uso local (sin auth); no exponer el puerto 8080
  fuera de la máquina.
- Latencia de eventos ≤ ~700ms (intervalo de polling), suficiente para
  observabilidad humana.
- La entrada de voz requiere Chrome/Edge; Firefox no implementa Web Speech.
