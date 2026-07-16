# Arquitectura — Robot RAG Agent

## Visión general

Cinco paquetes ROS 2 colaboran para llevar una instrucción en lenguaje
natural hasta acciones físicas del robot en simulación:

```
robot_interfaces  → msgs/srvs compartidos (compilar primero)
robot_zones       → almacén SQLite compartido de zonas nombradas
robot_rag         → memoria semántica (ChromaDB) + RAG
robot_skills      → ejecución de skills físicos/de percepción
robot_brain       → planificación LLM + orquestación
robot_dashboard   → dashboard web: observabilidad + goals (texto/voz)
robot_bringup     → launch files e integración
```

## Nodos y responsabilidades

### `rag_node` (robot_rag)

Expone `/rag/query` y `/rag/update_map`. Mantiene tres colecciones ChromaDB
(`semantic_map`, `knowledge_base`, `task_history`), cada una con
`hnsw:space: cosine`. Ingiere `data/knowledge/*.md` en `knowledge_base`
(chunking por sección markdown — headers agrupados con su contenido, no
separados) y `data/logs/*.json` en `task_history` al arrancar. También se
suscribe a `/semantic_objects` para ingestión pasiva de observaciones.

Embeddings vía `nomic-embed-text` (Ollama, 768 dims). Ver
[ADR-001](decisions/ADR-001-chromadb.md) y
[ADR-011](decisions/ADR-011-rag-mejoras-y-zonas-sqlite.md) (chunking,
task_history real, filtro de relevancia).

### `robot_zones` (paquete compartido, sin nodo propio)

`ZoneStore` — CRUD en SQLite (`data/zones.db`) para zonas rectangulares
nombradas creadas en el dashboard. Lo escribe `dashboard_node`, lo leen
`skills_executor_node` (resolución de `navigate`/`explore` por zona) y
`llm_planner_node` (lista de zonas conocidas en el prompt). Ver
[ADR-011](decisions/ADR-011-rag-mejoras-y-zonas-sqlite.md) (supersede
[ADR-008](decisions/ADR-008-zonas-usuario.md), que usaba JSON plano).

### `skills_executor_node` (robot_skills)

Expone `/skills/execute`, que despacha por `skill_name` a cuatro
implementaciones:

- **navigate** (`nav_skill.py`): Nav2 `BasicNavigator` (SimpleCommander API).
  Acepta zona nombrada (`ZONE_COORDINATES`) o pose `(x, y, theta)` explícita.
- **explore** (`explore_skill.py`): frontier exploration básica — busca la
  celda libre más cercana adyacente a espacio desconocido en el
  `OccupancyGrid` de `/map`, navega hacia ella, repite hasta agotar
  `duration_sec` o quedarse sin frontiers.
- **perceive** (`perceive_skill.py`): envía el último frame de
  `/camera/image_raw` a Qwen2.5-VL-7B (Ollama) pidiendo JSON estructurado de
  objetos detectados; cada objeto se registra en `semantic_map` vía
  `/rag/update_map`.
- **report** (`report_skill.py`): publica `/robot/response` y escribe un log
  JSON en `data/logs/<task_id>.json`.

La pose del robot se obtiene por TF (`map -> base_link`), no por AMCL — ver
[ADR-004](decisions/ADR-004-slam-toolbox-sin-amcl.md).

### `llm_planner_node` (robot_brain)

Se suscribe a `/robot/goal`. Por cada goal:

1. Consulta `/rag/query` sobre `knowledge_base` y `semantic_map`.
2. Construye el prompt (`prompts.py`) y pide un plan JSON a Qwen2.5-7B
   (`qwen_client.py`).
3. Ejecuta los pasos del plan secuencialmente contra tools de LangChain
   (`langchain_agent.py`) que llaman `/skills/execute`.
4. Publica progreso en `/robot/status`; si el plan no termina en un paso
   `report`, genera uno de cierre automáticamente.

## Flujo de datos

```
/robot/goal (String)
    │
    ▼
llm_planner_node ──/rag/query──► rag_node ──► ChromaDB
    │
    ▼ (prompt + contexto)
Qwen2.5-7B (Ollama) → plan JSON {reasoning, steps[]}
    │
    ▼ execute_plan()
LangChain tools ──/skills/execute──► skills_executor_node
                                          │
                    ┌─────────────────────┼─────────────────────┐
                    ▼                     ▼                     ▼
              nav_skill            perceive_skill          report_skill
              (Nav2)               (Qwen2.5-VL)            /robot/response
                                        │                  data/logs/*.json
                                        ▼
                                  /rag/update_map ──► rag_node
```

### `dashboard_node` (robot_dashboard)

Sirve un dashboard web en `http://localhost:8080` (FastAPI + uvicorn en un
hilo dentro del nodo). Muestra en vivo la línea temporal de planificación
(`/robot/goal`, `/robot/status`, `/robot/response`), un visor filtrable de
`/rosout`, y las dimensiones/límites del mapa SLAM. Permite enviar goals por
texto o por voz (Web Speech API del navegador, es-ES). Ver
[ADR-005](decisions/ADR-005-dashboard-fastapi.md).

## Modelo de concurrencia

Los nodos que hacen llamadas a servicios desde dentro de callbacks
(`llm_planner_node`, `skills_executor_node`) corren sobre
`MultiThreadedExecutor` con los clientes/suscripciones críticas en callback
groups separados, y esperan futuros con `threading.Event` — nunca con spins
anidados ni executors temporales. Ver
[ADR-007](decisions/ADR-007-executors-callback-groups.md) para el porqué
(dos patrones fallidos incluidos).

## Entorno de ejecución (WSL2 + venv)

Los nodos ROS 2 se compilan y ejecutan con el Python de sistema (el que trae
`rclpy`). Las dependencias del agente (ChromaDB, LangChain, Ollama client)
viven en `agent_env`. Ambos mundos se puentean vía `PYTHONPATH` en
`setup_env.sh`, no activando el venv — ver
[ADR-003](decisions/ADR-003-venv-pythonpath-bridge.md). Cualquier terminal
nueva debe hacer `source ~/robot_ws/setup_env.sh` antes de compilar o lanzar
nodos.

## Limitaciones conocidas

- **DDS en WSL2:** CycloneDDS está fijado a loopback vía `cyclonedds.xml` +
  `CYCLONEDDS_URI` (ver [ADR-006](decisions/ADR-006-cyclonedds-loopback.md));
  sin esto, el descubrimiento pub/sub entre nodos locales era intermitente
  por las múltiples interfaces (`eth0`, `docker0`).
- **Zonas fuera del mapa:** las coordenadas de `ZONE_COORDINATES`
  (nav_skill) asumen el mapa completo; con SLAM recién iniciado el planner
  de Nav2 rechaza goals fuera de los límites actuales
  ("outside bounds"). Hay que explorar primero para expandir el mapa — el
  system prompt del planner ya instruye "if unknown, explore first".
- **`navigate`/`explore` bloquean indefinidamente** si Nav2 no está activo
  (`wait_until_active()` no tiene timeout) — comportamiento estándar de
  `nav2_simple_commander`, asumido correcto mientras Nav2 se lance siempre
  junto con la simulación vía `full_system.launch.py`.
- **`task_history`** se crea como colección pero no se re-ingiere
  automáticamente tras cada tarea; los logs en `data/logs/*.json` quedan
  disponibles para una futura carga batch si se necesita RAG sobre
  histórico de tareas.
- **Cámara de alta resolución = mensajes descartados en silencio.** La
  cámara del TurtleBot3 Waffle de stock publica a 1920×1080 (~55 MB/s);
  con QoS BEST_EFFORT y el proceso compartiendo executor con Nav2/TF/Ollama,
  los frames se perdían en la capa DDS sin ningún error visible — parecía
  un bug de código y era volumen de datos. Ver
  [ADR-009](decisions/ADR-009-camara-resolucion-y-bridge.md) (bajada a
  640×480 con un modelo SDF propio, sin tocar `/opt/ros/jazzy/`).
