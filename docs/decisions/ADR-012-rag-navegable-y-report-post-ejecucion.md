# ADR-012: RAG navegable, report post-ejecución, y ablación (prep. del benchmark)

**Fecha:** 2026-07-16
**Estado:** Aceptado

## Contexto

La hipótesis del proyecto es que la memoria semántica (RAG) mejora la
navegación en lenguaje natural. Al preparar la evaluación cuantitativa se
detectó que, tal como estaba, **el RAG no podía mejorar la navegación aunque
funcionase**:

1. Los documentos de `semantic_map` se indexaban como
   `"{label} in {zone}: {desc}"` — **sin coordenadas**. Y `QueryRAG` devuelve
   solo el texto del documento (no la metadata). Así que el planner nunca
   recibía la pose de un objeto recordado y no podía navegar a él.
2. El mensaje del paso `report` lo redactaba el LLM **al planificar**, antes
   de ejecutar nada ("se encontraron: [objetos]"), así que las tareas de tipo
   "dime qué hay" no reflejaban la realidad.
3. No había forma de desactivar el RAG para un experimento A/B.
4. El LLM corría con temperatura por defecto → no reproducible.

## Decisión

**Poses en el texto del documento (Fase 0.1).** `SemanticMap.upsert_object`
ahora indexa `"{label} at (x=.., y=..) in {zone}: {desc}"`. Es el único canal
por el que el planner puede aprender coordenadas (QueryRAG no expone
metadata). El system prompt instruye: si el contexto trae coordenadas,
`navigate(x, y)` directo; si no, explorar. Verificado: con contexto el plan es
`navigate(x, y)`; sin contexto, `explore`.

**Base de conocimiento veraz (Fase 0.2).** `environment_rules.md` contenía
coordenadas de habitaciones **inventadas** (scaffolding) que no corresponden
al mundo `turtlebot3_house` (que es un mesh único, sin poses de mobiliario
extraíbles). Reescrito sin coordenadas fabricadas: las coordenadas reales
viven en `semantic_map`/zonas, medidas conduciendo el robot.

**Report post-ejecución (Fase 0.3).** El planner ya no emite pasos `report`
(se descartan si aparecen). Tras ejecutar navigate/explore/perceive, una
**segunda llamada al LLM** recibe los resultados reales y redacta la respuesta
al usuario. Responde en el idioma del goal (detección por heurística de
palabras clave, porque qwen2.5:7b ignora la instrucción "same language" cuando
el resto del prompt está en inglés).

**Reproducibilidad (Fase 0.4).** `QwenClient` acepta `temperature` (default
0.0) y `seed`. Verificado: dos llamadas idénticas producen salida idéntica.

**Flag de ablación (Fase 0.5).** `llm_planner_node` tiene `rag_enabled`
(default true) y `zones_in_prompt` (default true). Son los interruptores de
las 3 condiciones del benchmark (con RAG / sin RAG / a ciegas).

**Eliminación de LangChain.** El único uso era el decorador `@tool` para un
registro nombre→callable que el LLM nunca "tool-callea" (se parsea el JSON del
plan a mano). Se quitó (4 dependencias pesadas menos) y los skills se despachan
directamente en `toolkit.py` (antes `langchain_agent.py`). Un agent loop real
de LangChain queda como *future work*.

## Consecuencias

- La evaluación A/B ya es posible y con sentido (el RAG puede de verdad
  cambiar el plan de navegación).
- El ground truth de objetos para el benchmark debe medirse manualmente
  (conduciendo el robot), porque el mundo es un mesh sin poses de mobiliario.
- Cambiar `temperature`/`seed`/`rag_enabled` altera resultados del benchmark:
  fijar en el YAML de cada condición.
- `toolkit.py` ya no depende de langchain; si en el futuro se adopta un agent
  loop real, se reintroduce de forma deliberada, no vestigial.
