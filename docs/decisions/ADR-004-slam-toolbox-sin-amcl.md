# ADR-004: Localización vía SLAM Toolbox, sin AMCL

**Fecha:** 2026-07-14
**Estado:** Aceptado

## Contexto

El stack de navegación (ver CLAUDE.md) usa Nav2 + SLAM Toolbox con mapa
persistente, no un mapa estático pre-construido. `skills_executor_node`
necesita conocer la pose actual del robot para asociar objetos detectados
(`perceive`) y frontiers (`explore`) a coordenadas del mapa.

## Decisión

`skills_executor_node` obtiene la pose del robot mediante lookup de la
transformada `map -> base_link` (publicada por SLAM Toolbox mientras hace
scan matching), en vez de suscribirse a `/amcl_pose`.

`simulation.launch.py` lanza `nav2_bringup/navigation_launch.py` (controller,
planner, behaviors, bt_navigator — sin `map_server` ni `amcl`), ya que SLAM
Toolbox ya provee el mapa y la transformada map->odom.

## Razones

- AMCL requiere un mapa estático ya construido (`map_server` sirviendo un
  `.yaml`/`.pgm`); este proyecto mapea en vivo con SLAM Toolbox, por lo que
  `/amcl_pose` nunca se publicaría.

## Consecuencias

- Si en el futuro se cambia a localización sobre un mapa fijo guardado con
  `map_saver_cli`, habría que añadir `nav2_bringup/bringup_launch.py` (con
  AMCL) y decidir si mantener o quitar el lookup por TF.
