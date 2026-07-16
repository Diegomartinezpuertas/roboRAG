# ADR-008: Zonas definidas por el usuario (mapa interactivo)

**Fecha:** 2026-07-14
**Estado:** Superseded por [ADR-011](ADR-011-rag-mejoras-y-zonas-sqlite.md)
(el almacén JSON descrito abajo pasó a SQLite; el resto de este documento
sigue vigente como contexto de la decisión original).

## Contexto

El robot necesita un vocabulario espacial compartido con el usuario ("ve a la
cocina", "explora la zona A"). Las coordenadas hardcodeadas de
`ZONE_COORDINATES` en `nav_skill.py` no se corresponden con el mundo real
mapeado y no son editables por el usuario.

## Decisión

Las zonas se crean seleccionando un rectángulo en el mapa SLAM del dashboard
y nombrándolo. Doble persistencia:

1. **`data/zones.json`** — fuente de verdad geométrica
   (`{nombre: {x_min, y_min, x_max, y_max}}`). Lo escribe `dashboard_node`
   (`POST /api/zones`) y lo leen en cada uso `skills_executor_node`
   (resolución de `navigate(zone=...)` y bounds de `explore(zone=...)`) y
   `llm_planner_node` (lista KNOWN ZONES inyectada en el prompt).
2. **ChromaDB `semantic_map`** — cada zona se indexa además como
   `SemanticObject` (`object_id=zone-<nombre>`) vía `/rag/update_map`, de
   forma que las consultas RAG en lenguaje natural ("¿dónde está la cocina?")
   la recuperan como contexto.

`explore(zone=...)` restringe la búsqueda de frontiers al bounding box y, si
el robot está fuera, navega primero al centro de la zona.

## Razones

- JSON plano para la geometría: lectura trivial desde cualquier nodo sin
  acoplarlos al dashboard, editable a mano, y sin depender de embeddings
  para una consulta exacta.
- ChromaDB solo como capa semántica (recuperación difusa por el LLM), nunca
  como fuente de coordenadas.

## Consecuencias

- Borrar una zona (`DELETE /api/zones/<n>`) la quita del JSON pero no de
  ChromaDB (la entrada indexada queda huérfana; inofensiva porque navigate
  valida contra el JSON). Limpieza pendiente si se vuelve molesto.
- Las zonas por defecto de `ZONE_COORDINATES` quedan como fallback si el
  nombre no existe en el JSON.
