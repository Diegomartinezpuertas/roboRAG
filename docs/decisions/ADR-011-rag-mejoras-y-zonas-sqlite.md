# ADR-011: Mejoras de calidad del RAG y zonas en SQLite (supersede ADR-008)

**Fecha:** 2026-07-14
**Estado:** Aceptado

## Contexto

Un análisis con queries reales contra el `semantic_map`/`knowledge_base`
existentes reveló tres problemas concretos (no solo debilidad general):

1. **Chunking rompía headers de su contenido.** `knowledge_base.py` partía
   los `.md` por línea en blanco, así que `"## Safety rules"` y el párrafo
   de debajo quedaban en chunks separados — preguntar por "reglas de
   seguridad" devolvía el título vacío.
2. **`task_history` era una colección fantasma.** Existía en el esquema
   (`collections` en `agent_params.yaml`) pero nada la llenaba nunca —
   0 documentos siempre, pese a que `report_skill` escribe un JSON por
   tarea en `data/logs/`.
3. **Sin filtro de relevancia.** Todo lo recuperado por `/rag/query` se
   inyectaba al prompt de Qwen sin comprobar el score — una consulta con
   mal match (frecuente por el desajuste de idioma: docs en inglés, queries
   en español) metía ruido activo en vez de contexto útil.

Además, el usuario pidió persistir las zonas marcadas en el dashboard en
SQL en vez de JSON plano (ADR-008), manteniendo la opción de borrarlas.

## Decisión

### Chunking
`knowledge_base.chunk_markdown()` agrupa cada header (`#`) con todo el
texto hasta el siguiente header, en vez de partir por línea en blanco.

### `task_history` real
Nuevo `robot_rag/task_history.py::TaskHistoryStore`, análogo a
`KnowledgeBase` pero **sin** el guard de "solo si la colección está vacía"
— los logs de tareas se acumulan con el tiempo, así que se re-escanea
`data/logs/*.json` en cada arranque de `rag_node` y se hace upsert
(idempotente, IDs derivados del nombre de archivo).

### Filtro de relevancia
`RobotToolkit.call_rag_with_scores()` devuelve pares `(contexto, score)`.
`llm_planner_node._retrieve_context()` descarta los que caen por debajo de
`rag_score_threshold` (parámetro, default 0.45) antes de construir el
prompt. También se añade `task_history` como tercera colección consultada
en cada goal.

### Zonas en SQLite (supersede ADR-008)
Nuevo paquete `robot_zones` (`ZoneStore`, stdlib `sqlite3`, tabla
`zones(name, x_min, y_min, x_max, y_max, created_at)`) como dependencia
compartida de `robot_dashboard` (escritor), `robot_skills` y `robot_brain`
(lectores). `dashboard_node` migra automáticamente `data/zones.json` (si
existe y la DB está vacía) al arrancar. La indexación en ChromaDB
`semantic_map` para búsqueda en lenguaje natural se mantiene sin cambios.

## Razones

- Conexión SQLite nueva y corta por operación (sin conexión persistente
  compartida entre hilos/procesos) — evita cualquier problema de
  concurrencia para este volumen de escrituras, sin añadir un servidor.
- `rag_score_threshold=0.45` se calibró con los scores reales observados:
  los matches correctos en `semantic_map` puntuaban 0.57–0.62, el peor
  match incorrecto en `knowledge_base` puntuaba 0.40.

## Consecuencias

- El desajuste de idioma (docs en inglés, queries en español) sigue
  presente — no se ha traducido la base de conocimiento. Con el umbral
  activo, queries en español contra `knowledge_base` a veces no recuperan
  nada (mejor eso que ruido, pero es una limitación real pendiente).
- `zones.json` queda como artefacto legado; no se borra automáticamente
  tras la migración, por si hay que auditar el traspaso.
