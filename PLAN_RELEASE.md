# Plan de entrega — Robot RAG Agent como proyecto GitHub

> Objetivo: repositorio público presentable, con una **evaluación cuantitativa
> de si el RAG mejora la navegación** como pieza central, tests, CI y README
> de calidad. Este documento es el plan; cada fase es una sesión de trabajo
> aproximadamente.

## Estado (2026-07-16)

- ✅ **Fase 0** (fixes pre-benchmark): poses en el contexto RAG, base de
  conocimiento veraz, report post-ejecución, temperature=0, flags de ablación
  `rag_enabled`/`zones_in_prompt`. Ver ADR-012. Validado: con RAG el plan es
  `navigate(x,y)` directo; sin RAG, `explore`.
- ✅ **Fase 1** (limpieza): LangChain eliminado (4 deps menos), TaskPlan.msg,
  /semantic_objects, ZONE_COORDINATES falsas, model-1_4.sdf, cuda-keyring,
  zones.json, qwen-agent/openai de requirements. respawn en agent.launch.py.
- ✅ **Fase 2** (tests + CI): 37 tests pytest de lógica pura (verdes), ruff
  limpio en todo el repo, workflow GitHub Actions (.github/workflows/ci.yml).
- ✅ **Fase 3** (benchmark): hecho. Pivotado a benchmark a nivel de
  planificación (ADR-013) por inestabilidad de la navegación end-to-end en la
  sim WSL2. Harness en `eval/` (dry_run, /robot/plan, SR por tipo de tarea con
  controles). **Resultado (full, 30 runs, temp=0):**
  navegación referida a objeto 9/9 con RAG vs 0/9 sin RAG; control de zona
  conocida 3/3 en ambas; control de alucinación 3/3 en ambas.
- ✅ **Fase 4** (README): `README.md` en inglés, lidera con la tabla del
  benchmark, diagrama mermaid, quickstart, tech stack, limitaciones honestas y
  roadmap. Pendiente opcional: traducir architecture.md/api_reference.md
  (siguen en español; los ADRs también).
- ✅ **Fase 5** (git): repo inicializado en rama `main`, `.gitignore`,
  `LICENSE` (MIT), commit inicial (131 archivos, sin build/venv/datos runtime).
  Pendiente: crear el repo remoto en GitHub y `git push` (lo hace el usuario).

---

## La pregunta científica y por qué hay que arreglar cosas antes de medir

La hipótesis del proyecto es: *"la memoria semántica (RAG) mejora la
capacidad del robot para ejecutar tareas de navegación en lenguaje natural"*.

**Problema detectado (crítico para la hipótesis):** hoy el RAG **no puede**
mejorar la navegación aunque quisiera, porque:

1. `QueryRAG` devuelve solo los textos, **no las poses** — el planner ve
   "mailbox: a black mailbox on a pole" pero NO sus coordenadas. Con ese
   contexto es imposible navegar hasta el objeto.
2. La navegación por zonas (`navigate(zone=...)`) resuelve desde SQLite
   directamente, sin pasar por el RAG. Para ese caso el RAG es redundante.
3. `data/knowledge/environment_rules.md` contiene **coordenadas inventadas**
   del scaffolding inicial ("kitchen centered around (2.0, 1.5)") que NO
   corresponden al mundo `turtlebot3_house` real. Si el RAG recupera eso,
   *empeora* la navegación. Garbage in, garbage out.

La Fase 0 arregla esto para que el experimento tenga sentido.

---

## FASE 0 — Arreglos de arquitectura previos a la evaluación

**F0.1 — Poses en el contexto RAG.** Al indexar objetos/zonas en
`semantic_map`, incluir las coordenadas en el texto del documento
(`"mailbox at (x=-0.74, y=-0.86) in corridor: a black mailbox..."`).
Actualizar el system prompt para que Qwen sepa que puede hacer
`navigate(x, y)` con coordenadas recuperadas del contexto.

**F0.2 — Base de conocimiento veraz.** Reescribir
`data/knowledge/environment_rules.md` con las coordenadas REALES de las
habitaciones del mundo `turtlebot3_house` (medirlas desde Gazebo/el SDF del
mundo). Sin esto, el brazo "con RAG" del experimento parte con desventaja
artificial.

**F0.3 — Report post-ejecución.** Hoy Qwen redacta el mensaje del `report`
*al planificar* ("se encontraron: [objetos descritos]"). Añadir una segunda
llamada al LLM tras ejecutar los pasos, con los resultados reales, para
generar la respuesta final. Sin esto, las tareas tipo "dime qué hay" no son
evaluables.

**F0.4 — Reproducibilidad del LLM.** Fijar `temperature=0` (+ `seed`) en
`qwen_client.py` como opción/parámetro. Sin esto no hay benchmark serio.

**F0.5 — Flag de ablación.** Parámetro `rag_enabled` en `llm_planner_node`
que desactiva `_retrieve_context` (y opcionalmente `zones_in_prompt`). Es el
interruptor del experimento A/B.

**F0.6 — Percepción oráculo para el benchmark.** El VL (Qwen2.5-VL) sobre
renders de Gazebo con llvmpipe funciona mal (confirmado por el usuario, y
esperable: los VLM sufren con renders sintéticos de baja fidelidad). Para
que la evaluación de navegación no quede contaminada por errores de
percepción: modo "oracle perception" que lee las poses reales de los
modelos desde Gazebo (servicio/topic de gz) cuando el robot está cerca.
El VL queda como feature de demo best-effort, no como base del benchmark.
Diagnóstico VL aparte: guardar N frames de la cámara, evaluar offline
contra los objetos reales visibles, probar prompt con catálogo cerrado +
temperature 0; si sigue mal, documentarlo honestamente como limitación
(o probar un detector convencional tipo YOLOv8n como alternativa).

---

## FASE 1 — Limpieza: qué sobra (eliminar) y deuda menor

| Elemento | Acción | Motivo |
|---|---|---|
| `qwen-agent[rag]` en requirements | Eliminar | No se usa en ningún sitio |
| `openai` en requirements | Eliminar | No se usa (cliente `ollama` directo) |
| `TaskPlan.msg` | Eliminar | Definido y jamás usado |
| Suscripción `/semantic_objects` en rag_node | Eliminar | Nada publica ahí (se usa el servicio UpdateMap) |
| `ZONE_COORDINATES` hardcodeadas en nav_skill | Eliminar | Coordenadas falsas; las zonas reales viven en SQLite |
| `models/turtlebot3_waffle/model-1_4.sdf` | Eliminar | Copia legacy no usada |
| `cuda-keyring_1.1-1_all.deb` en la raíz | Eliminar | Artefacto de instalación |
| `data/zones.json` | Eliminar tras verificar migración | Superseded por SQLite (ADR-011) |
| **LangChain** | **Decidir** (ver abajo) | Uso vestigial |
| Evento "goal" duplicado en dashboard | Arreglar | Cosmético pero visible |
| `respawn=True` en nodos del agente (launch) | Añadir | El launch se cae en cascada al morir un hijo |

**Decisión LangChain:** hoy solo se usan los decoradores `@tool` y un
lookup por nombre — no hay agent loop real; es peso muerto que "decora" el
CV. Dos opciones honestas: (a) quitarlo y llamar a los skills
directamente (más simple, menos deps), o (b) adoptar un agent loop real de
LangChain (tool-calling iterativo con feedback) como evolución del
plan-then-execute. Recomendación: (a) para la entrega, (b) como línea de
"future work" en el README. Un revisor técnico que abra el código y vea
LangChain sin usar resta más que la keyword suma.

---

## FASE 2 — Tests unitarios + lint + CI

Estructura: pytest puro para la lógica sin ROS (rápido, corre en CI sin
instalar ROS), y los tests `ament_flake8`/`ament_pep257` ya scaffolded
deben pasar (hoy probablemente no pasan; arreglarlo).

| Paquete | Tests |
|---|---|
| robot_rag | `chunk_markdown` (headers+cuerpo, archivos sin headers, vacíos); conversión distancia→score con clamp; `TaskHistoryStore.ingest` con tmpdir (idempotencia, JSON corrupto); `ChromaManager` CRUD con embeddings fake |
| robot_zones | `ZoneStore` CRUD completo, migración desde JSON, concurrencia básica (2 conexiones) |
| robot_skills | `find_nearest_frontier` con grids sintéticos (bounds, exclusión, min_distance, sin frontiers); `_strip_code_fence`; `_resolve_zone` con store en tmpdir |
| robot_brain | `_parse_plan` (JSON limpio, con fences, inválido); `build_user_prompt`; `execute_plan` con tools fake (orden, corte al primer error, skill desconocido); `_summarize_results` |
| robot_dashboard | `EventBuffer` (ids monotónicos, evicción, since); render de mapa con grid 3×3; endpoints con `fastapi.TestClient` y nodo stub (POST /api/goal vacío→400, POST/DELETE zonas) |

CI (GitHub Actions):
- Job 1 (rápido, siempre): ruff/flake8 + pytest de lógica pura (sin ROS).
- Job 2 (opcional, en contenedor `osrf/ros:jazzy-desktop`): `colcon build` +
  `colcon test`. Si complica, se deja para v0.2 — el job 1 ya da badge.

---

## FASE 3 — Benchmark: ¿el RAG mejora la navegación? (la pieza central)

### Diseño experimental

**Condiciones (ablación):**
- **C1 — RAG completo**: contexto de las 3 colecciones + zonas en prompt.
- **C2 — Sin RAG**: `rag_enabled:=false` (solo lista de zonas).
- **C3 — Sin RAG ni zonas**: el LLM a ciegas (control inferior).

**Suite de tareas** (~15-20 tareas en YAML, 5 categorías):
- **A. Navegación por zona conocida** ("Ve a la cocina") — control: debería
  funcionar igual en C1 y C2 (la resolución es por SQLite).
- **B. Navegación referida a objeto** ("Ve a donde está el buzón") — solo
  resoluble con semantic_map + poses (F0.1). Núcleo de la hipótesis.
- **C. Conocimiento del entorno** ("Ve a la habitación donde estaría el
  frigorífico") — requiere knowledge_base veraz (F0.2).
- **D. Memoria de tareas** ("Vuelve a la última zona que exploraste") —
  requiere task_history.
- **E. Controles negativos** ("Ve al garaje" — no existe): la respuesta
  correcta es explorar o declinar, no alucinar coordenadas. Mide si el RAG
  reduce alucinación.

**Métricas** (estándar de embodied AI, defendibles en una entrevista):
- **SR** (Success Rate): llegar a <0.75 m del ground truth (+ respuesta
  correcta donde aplique).
- **SPL** (Success weighted by Path Length): SR ponderado por
  camino_óptimo / camino_recorrido (óptimo = plan global de Nav2 desde el
  origen; recorrido = integración de /odom).
- Secundarias: tiempo hasta éxito, % planes JSON válidos, tasa de zonas/
  coordenadas alucinadas, nº de errores de skill por tarea.
- **n = 3 repeticiones** por tarea×condición (con temperature 0 la varianza
  viene de la sim), media ± desviación.

**Infraestructura** (`robot_eval/` o `eval/`):
- `seed_memory.py`: puebla zones.db + semantic_map con ground truth del
  mundo house (poses reales de los modelos del SDF).
- `tasks.yaml`: suite con criterio de éxito por tarea (pose objetivo +
  radio, keywords esperadas en la respuesta, timeout).
- `run_benchmark.py`: por condición → relanza el sistema headless, ejecuta
  cada tarea vía /robot/goal, monitoriza /robot/status + pose TF, puntúa,
  escribe `results/<condición>.json`.
- `report.py`: genera la tabla markdown para el README.

**Entregable:** tabla en el README tipo:

| Condición | SR | SPL | Alucinaciones |
|---|---|---|---|
| Con RAG | X% | X | X% |
| Sin RAG | Y% | Y | Y% |

Si el resultado es que el RAG NO mejora (posible en categoría A), eso
también es un resultado presentable — el análisis honesto de *dónde* ayuda
(B/C/D) y dónde no (A) es exactamente lo que da credibilidad al proyecto.

---

## FASE 4 — Auditoría de documentación + README

**README.md (inglés, en la raíz)** — no existe hoy. Estructura:
1. Título + badges (CI, license, ROS 2 Jazzy) + **GIF de demo** (dashboard
   + RViz + robot navegando; grabar con OBS/peek).
2. Qué es (3 líneas) + diagrama de arquitectura (mermaid, renderiza nativo
   en GitHub).
3. **Resultados del benchmark** (la tabla de F3) + metodología en 1 párrafo.
4. Quickstart reproducible (requisitos, instalación, `setup_env.sh`,
   un comando de launch, un comando de benchmark).
5. Stack técnico y decisiones (enlace a los 11 ADRs — son un plus de
   seniority, mantenerlos).
6. Limitaciones honestas (VL sobre renders sintéticos, cross-lingual del
   embedder, WSL2) + roadmap (voz con Whisper+NPU, agent loop, Qwen-RobotNav
   cuando liberen pesos).

**Auditoría de docs existentes:**
- `docs/architecture.md` y `api_reference.md`: traducir a inglés (los ADRs
  pueden quedarse en español como decision log interno, o traducir los 3
  más importantes: 007, 009, 011).
- Verificar que cada nodo tiene su docstring completo (convención
  CLAUDE.md) — hacer pasar `ament_pep257`.
- `PLAN.md` original → mover a `docs/history/` (es un artefacto de diseño,
  no confundir al visitante).

---

## FASE 5 — Higiene de repositorio GitHub

- `git init` (¡el workspace aún no es repo!), primer commit limpio.
- `.gitignore`: `agent_env/`, `build/`, `install/`, `log/`,
  `data/chroma_db/`, `data/logs/`, `data/zones.db`, `__pycache__`.
  (`data/knowledge/` SÍ se commitea — CLAUDE.md.)
- `LICENSE` (MIT, coherente con package.xml).
- Commits en inglés estilo convencional (regla ya en CLAUDE.md).
- Tag `v0.1.0` cuando el benchmark esté en el README. Topics del repo:
  `ros2`, `rag`, `llm`, `robotics`, `nav2`, `slam`, `ollama`.
- Opcional: `CONTRIBUTING.md` corto + plantilla de issue (señal de
  proyecto cuidado; 15 min).

---

## Trabajo futuro sobre el ground truth del benchmark

El benchmark v1 mide el ground truth **conduciendo el robot** a cada landmark
y registrando su pose (self-consistent: el punto almacenado es alcanzable
porque el robot estuvo allí). Ampliación futura: cuando se use un mundo con
modelos de mobiliario individuales (no un mesh único), leer las poses reales
de los objetos desde Gazebo para un ground truth independiente de la
trayectoria, y añadir landmarks fuera de la ruta de exploración inicial.

## Qué se puede AÑADIR (más allá de la entrega, priorizado)

1. **Agent loop real** (replanificación con feedback de resultados) — el
   salto de "plan-then-execute" a agente de verdad; mejora esperable en el
   benchmark y gran sección de README.
2. **Fase de voz nativa** (Whisper en Windows con el NPU, publicando en
   /robot/goal) — ya previsto en CLAUDE.md.
3. **Detector convencional** (YOLOv8n) como alternativa/comparativa al VL —
   otra tabla comparativa barata.
4. Guardado/carga de mapa SLAM (map_saver) para escenarios reproducibles.
5. Docker/devcontainer para reproducibilidad total (elimina el "en mi
   WSL2 funciona").

## Qué NO añadir (alcance contenido)

- Multi-robot, Qdrant, microservicios: el ADR-001 ya justifica lo simple.
- Fine-tuning de modelos: fuera de alcance del portfolio.
- Frontend framework para el dashboard: la SPA vanilla es una feature, no
  una carencia (cero build steps).

---

## Orden de ejecución y estimación

| Fase | Contenido | Estimación |
|---|---|---|
| F0 | Fixes pre-benchmark (poses, knowledge veraz, report post-ejecución, temp 0, flag ablación, oráculo) | 1-2 sesiones |
| F1 | Limpieza (tabla de "sobra") | 0.5 sesión |
| F2 | Tests + lint + CI | 1-2 sesiones |
| F3 | Benchmark + ejecución + análisis | 2 sesiones |
| F4 | README + docs inglés + GIF | 1 sesión |
| F5 | Git/GitHub | 0.5 sesión |
