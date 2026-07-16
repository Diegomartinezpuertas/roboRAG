# ADR-006: CycloneDDS fijado a loopback en WSL2

**Fecha:** 2026-07-14
**Estado:** Aceptado

## Contexto

En WSL2 conviven varias interfaces de red (`lo`, `eth0`, `docker0`). Por
defecto CycloneDDS anuncia cada participante en todas ellas, lo que produjo
descubrimiento pub/sub intermitente entre nodos locales (goals publicados
en `/robot/goal` que a veces no llegaban al suscriptor).

## Decisión

Fijar CycloneDDS a la interfaz `lo` mediante `~/robot_ws/cyclonedds.xml`,
referenciado con `CYCLONEDDS_URI` en `setup_env.sh`. Todo el sistema corre
en una sola máquina, así que loopback es suficiente.

## Consecuencias

- Descubrimiento DDS determinista entre todos los nodos locales.
- Si en el futuro se conecta un robot/PC externo por red, habrá que añadir
  la interfaz correspondiente al XML (o quitar la restricción).

## Nota relacionada (no era DDS)

El síntoma de "nodo duplicado" en `ros2 node list`
(`/skills_executor_node` dos veces) tenía otra causa: el `name:=` del
launch file remapea **todos** los nodos del proceso, incluido el
`BasicNavigator` interno de nav2_simple_commander, renombrándolo también a
`skills_executor_node`. Solución: no usar `name=` en el `Node(...)` de
`skills_executor_node` en `agent.launch.py` (el nodo ya se nombra a sí
mismo, y `BasicNavigator` conserva su nombre `basic_navigator`).
