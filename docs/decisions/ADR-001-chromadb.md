# ADR-001: Usar ChromaDB en lugar de Qdrant

**Fecha:** 2026-07-14
**Estado:** Aceptado

## Contexto

Necesitamos un vector store local para el RAG semántico del robot: memoria de
objetos detectados (`semantic_map`), documentos estáticos del entorno
(`knowledge_base`) e historial de tareas (`task_history`).

## Decisión

Usar ChromaDB 1.5.x con `PersistentClient` y persistencia en disco en
`data/chroma_db/`. Cada colección se crea con `hnsw:space: cosine`.

## Razones

- API más simple para prototipos (`get_or_create_collection`, `upsert`, `query`).
- Persistencia automática sin servidor externo — encaja con el requisito de
  correr todo localmente en WSL2 sin dependencias de red.
- Integración directa con embeddings generados vía Ollama (`nomic-embed-text`),
  sin necesitar un adaptador adicional.

## Consecuencias

- Limitado a un solo proceso (no distribuido). Aceptable: `rag_node` es el
  único proceso que abre el `PersistentClient`.
- Migración a Qdrant si en el futuro se necesita escalar a múltiples robots
  o acceso concurrente desde varios procesos.
