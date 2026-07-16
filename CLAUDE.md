# CLAUDE.md — Robot RAG Agent

> Guía de contexto para Claude Code. Léelo completo antes de tocar cualquier archivo.

---

## Qué es este proyecto

Un agente robótico cognitivo que corre en simulación (ROS 2 Jazzy + Gazebo Harmonic).
Recibe instrucciones en lenguaje natural, consulta una memoria semántica (RAG con ChromaDB),
planifica mediante un LLM local (Qwen2.5-7B vía Ollama) y ejecuta acciones en el robot
(TurtleBot3 Waffle) a través de skills ROS 2.

**Propósito:** Proyecto personal de portfolio para CV de ingeniería robótica.
La documentación es tan importante como el código — cada decisión debe quedar registrada.

---

## Hardware y entorno

```
OS:        Windows 11 + WSL2 Ubuntu 24.04
CPU:       Intel Core Ultra 7 HX
GPU:       NVIDIA RTX 5070 8GB (accesible desde WSL2 vía Ollama/CUDA)
NPU:       Intel NPU — NO accesible desde WSL2 (reservado para Windows nativo)
RAM:       32GB
Shell:     bash en WSL2
Python:    ~/robot_ws/agent_env (venv activado automáticamente)
ROS2:      Jazzy Jalisco
Simulador: Gazebo Harmonic (llvmpipe — GPU no accesible para render, sí para inferencia)
```

**Nota GPU:** Gazebo corre en software rendering (llvmpipe). La RTX la usa exclusivamente
Ollama para inferencia LLM. No intentar forzar GPU en Gazebo sin confirmación previa.

---

## Stack tecnológico

| Capa | Tecnología | Versión | Notas |
|------|-----------|---------|-------|
| ROS 2 | Jazzy Jalisco | LTS 2024 | Workspace: ~/robot_ws |
| Simulador | Gazebo Harmonic | gz-sim 8 | Integrado con ros-jazzy |
| Robot | TurtleBot3 Waffle | — | Tiene LIDAR + cámara |
| LLM planner | Qwen2.5-7B-Instruct | Ollama | Puerto 11434 |
| LLM visión | Qwen2.5-VL-7B | Ollama | Solo cuando sea necesario (no cargar junto al 7B) |
| Embeddings | nomic-embed-text | Ollama | Para ChromaDB |
| Agent framework | LangChain 0.3 | pip | + langchain-ollama |
| Vector DB | ChromaDB 0.6 | pip | Persistente en ~/robot_ws/data/chroma_db |
| Navegación | Nav2 | ros-jazzy | SimpleCommander API |
| SLAM | SLAM Toolbox | ros-jazzy | Mapa persistente |
| Middleware | CycloneDDS | ros-jazzy | Fijado a loopback vía cyclonedds.xml (ADR-006) |
| Dashboard | FastAPI + uvicorn | pip | http://localhost:8080 — observabilidad + goals (ADR-005) |

---

## Estructura del proyecto

```
~/robot_ws/
├── src/
│   ├── robot_interfaces/   # Msgs y srvs ROS2 custom (compilar primero)
│   ├── robot_rag/          # ChromaDB + embeddings + mapa semántico
│   ├── robot_skills/       # Nodos ejecutables: nav, explore, perceive, report
│   ├── robot_brain/        # LLM planner + LangChain agent (cerebro)
│   ├── robot_zones/        # Almacén SQLite compartido de zonas nombradas
│   ├── robot_dashboard/    # Dashboard web: observabilidad + goals (texto/voz)
│   └── robot_bringup/      # Launch files del sistema completo
├── data/
│   ├── chroma_db/          # Base vectorial persistente (NO commitear)
│   ├── knowledge/          # Docs estáticos para RAG (SÍ commitear)
│   └── logs/               # Historial de tareas (NO commitear)
├── agent_env/              # venv Python (NO commitear)
├── docs/                   # Documentación del proyecto (SÍ commitear)
│   ├── architecture.md
│   ├── api_reference.md
│   └── decisions/          # ADRs — Architecture Decision Records
└── requirements.txt
```

---

## Convenciones de código

### Python

```python
# Imports: stdlib → third-party → ROS2 → proyecto local
import json
from pathlib import Path

import chromadb
from langchain_ollama import ChatOllama

import rclpy
from rclpy.node import Node

from robot_interfaces.srv import QueryRAG
```

- **Type hints obligatorios** en todas las funciones públicas
- **Docstrings en inglés** — formato Google style
- **Logging:** usar `self.get_logger()` en nodos ROS2, nunca `print()`
- **Nombres de nodos ROS2:** snake_case, sufijo `_node` (ej: `llm_planner_node`)
- **Nombres de topics:** `/robot/<nombre>` para topics propios del proyecto
- **Nombres de servicios:** `/<paquete>/<nombre>` (ej: `/rag/query`, `/skills/execute`)

### Docstring obligatorio en cada nodo ROS2

```python
class LLMPlannerNode(Node):
    """ROS 2 node that receives natural language goals and produces execution plans.

    Subscribes:
        /robot/goal (std_msgs/String): Natural language task description.

    Publishes:
        /robot/response (std_msgs/String): Final response to the user.
        /robot/status (std_msgs/String): Current execution status.

    Services (client):
        /rag/query (QueryRAG): Retrieve context from ChromaDB.
        /skills/execute (ExecuteSkill): Dispatch skills to robot_skills package.

    Parameters:
        ollama_base_url (str): Ollama server URL. Default: http://localhost:11434
        llm_model (str): Model name for task planning. Default: qwen2.5:7b
        max_plan_steps (int): Maximum steps in a single plan. Default: 10
    """
```

### Mensajes y servicios ROS2

Cada `.msg` y `.srv` debe incluir un comentario en cada campo:

```
# QueryRAG.srv
string query_text        # Natural language query to search in ChromaDB
string collection_name   # Target collection: semantic_map | knowledge_base | task_history
int32  top_k             # Number of results to return (default: 5)
---
string[]  contexts       # Retrieved text fragments, ordered by relevance
float32[] scores         # Cosine similarity scores [0.0, 1.0]
bool      success        # False if collection not found or query failed
string    error_msg      # Empty string if success=True
```

---

## Documentación — reglas estrictas

La documentación es **primera clase** en este proyecto. Cada cambio relevante requiere
actualizar los docs correspondientes en el mismo commit.

### Qué documentar siempre

1. **Cada nodo ROS2** → docstring completo con subs/pubs/srvs/params
2. **Cada función pública** → docstring Google style con Args, Returns, Raises
3. **Cada decisión de arquitectura** → ADR en `docs/decisions/`
4. **Cada integración nueva** → sección en `docs/architecture.md`
5. **Cada cambio en el stack** → actualizar este CLAUDE.md

### Formato ADR (Architecture Decision Record)

Crear un archivo por cada decisión importante en `docs/decisions/`:

```markdown
# ADR-001: Usar ChromaDB en lugar de Qdrant

**Fecha:** 2026-07-13
**Estado:** Aceptado

## Contexto
Necesitamos un vector store local para el RAG semántico del robot.

## Decisión
Usar ChromaDB 0.6 con persistencia en disco.

## Razones
- API más simple para prototipos
- Persistencia automática sin servidor externo
- Integración directa con LangChain

## Consecuencias
- Limitado a un solo proceso (no distribuido)
- Migración a Qdrant si se necesita escalar
```

### `docs/api_reference.md` — actualizar con cada srv/msg nuevo

```markdown
## /rag/query (QueryRAG.srv)

Recupera contexto relevante de ChromaDB dado un texto de consulta.

**Request:**
| Campo | Tipo | Descripción |
|-------|------|-------------|
| query_text | string | Consulta en lenguaje natural |
| collection_name | string | Colección destino |
| top_k | int32 | Número de resultados (default: 5) |

**Response:**
| Campo | Tipo | Descripción |
|-------|------|-------------|
| contexts | string[] | Fragmentos recuperados por similitud |
| scores | float32[] | Puntuaciones coseno [0.0, 1.0] |
| success | bool | False si falla la consulta |
```

---

## Comandos frecuentes

```bash
# Preparar shell para compilar o lanzar nodos (NO activar agent_env a mano
# para nodos ROS2 — ver docs/decisions/ADR-003-venv-pythonpath-bridge.md)
source ~/robot_ws/setup_env.sh

# Compilar workspace completo
cd ~/robot_ws && colcon build --symlink-install

# Compilar solo un paquete
colcon build --symlink-install --packages-select robot_rag

# Lanzar simulación completa
ros2 launch robot_bringup full_system.launch.py

# Enviar tarea al robot
ros2 topic pub --once /robot/goal std_msgs/String \
  "data: 'Ve a la cocina y dime qué objetos hay'"

# Ver respuesta del robot
ros2 topic echo /robot/response

# Dashboard web (observabilidad + envío de goals por texto/voz)
# Se lanza automáticamente con agent.launch.py / full_system.launch.py
# Abrir en el navegador de Windows:
#   http://localhost:8080

# Test servicio RAG
ros2 service call /rag/query robot_interfaces/srv/QueryRAG \
  "{query_text: 'where is the kitchen', collection_name: 'knowledge_base', top_k: 3}"

# Estado de Ollama y modelos cargados
ollama ps
curl -s http://localhost:11434/api/tags | python3 -m json.tool

# Ver topics activos
ros2 topic list
ros2 topic hz /scan   # verificar que el LIDAR publica

# Ver nodos activos
ros2 node list

# Logs de un nodo específico
ros2 run robot_brain llm_planner_node --ros-args --log-level DEBUG
```

---

## Variables de entorno importantes

```bash
# ROS 2
ROS_DOMAIN_ID=0                              # Dominio DDS (cambiar si hay conflictos)
RMW_IMPLEMENTATION=rmw_cyclonedds_cpp        # Mejor middleware para WSL2
CYCLONEDDS_URI=file://~/robot_ws/cyclonedds.xml  # DDS fijado a loopback (ADR-006)
TURTLEBOT3_MODEL=waffle                      # Modelo con LIDAR + cámara

# Gazebo
DISPLAY=:0                                   # WSLg
LIBGL_ALWAYS_SOFTWARE=0                      # NO forzar software (aunque Gazebo acabe usándolo)

# Ollama
OLLAMA_KEEP_ALIVE=-1                         # No descargar modelos de VRAM nunca
OLLAMA_NUM_GPU=1                             # Forzar GPU explícitamente

# Proyecto
ROBOT_WS=/home/diego/robot_ws
CHROMA_DB_PATH=${ROBOT_WS}/data/chroma_db
KNOWLEDGE_DIR=${ROBOT_WS}/data/knowledge
```

---

## Limitaciones conocidas y workarounds

| Problema | Causa | Workaround |
|----------|-------|------------|
| Gazebo usa llvmpipe | RTX 5070 WSL2 GPU passthrough incompleto | GUI de Gazebo desactivada por defecto (RTF 0.15→~1.0); visualización vía RViz (use_rviz:=true) o dashboard. use_gz_gui:=true si hace falta |
| Cerrar la GUI de Gazebo tumbaba todo | on_exit_shutdown:true en el gzclient del launch de turtlebot3 | simulation.launch.py propio lanza server/GUI por separado, GUI sin shutdown |
| NPU no accesible en WSL2 | WSL2 no expone dispositivo NPU | Reservar para Windows nativo (fase voz: Whisper) |
| qwen2.5:7b + qwen2.5vl:7b no caben juntos | 8GB VRAM total | Cargar solo el modelo necesario según el task step |
| SLAM drift en simulación larga | Gazebo sin GPU | Guardar mapa cada N minutos con map_saver_cli |
| Discovery DDS intermitente | WSL2 multi-NIC (eth0/docker0) | CycloneDDS fijado a lo — cyclonedds.xml + CYCLONEDDS_URI (ADR-006) |
| Ventana Gazebo no aparece | msrdc.exe (bridge WSLg) muerto | `wsl --shutdown` desde PowerShell y relanzar |
| Goals fuera del mapa SLAM | Mapa crece con la exploración | Explorar primero; Nav2 rechaza goals "outside bounds" |

---

## Flujo de trabajo para nuevas features

1. Crear rama: `git checkout -b feature/nombre-descriptivo`
2. Implementar el código
3. Escribir/actualizar docstrings y docs
4. Crear ADR si hay decisión de arquitectura
5. Test manual con `ros2 service call` o `ros2 topic pub`
6. Actualizar `docs/api_reference.md` si hay nuevos srv/msg
7. Commit con mensaje descriptivo en inglés:
   ```
   feat(robot_rag): add semantic_map collection with pose indexing

   - SemanticObject messages now stored with 2D pose in ChromaDB metadata
   - Enables spatial queries like "objects near kitchen"
   - Updates QueryRAG.srv response to include pose field
   ```

---

## Lo que NO hacer

- **No usar `print()`** en nodos ROS2 — usar `self.get_logger().info()`
- **No hardcodear paths** — usar parámetros ROS2 o variables de entorno
- **No cargar qwen2.5vl:7b y qwen2.5:7b a la vez** — se agotan los 8GB de VRAM
- **No commitear** `data/chroma_db/`, `data/logs/`, `agent_env/`
- **No modificar** `/opt/ros/jazzy/` — es instalación del sistema
- **No olvidar** `source ~/robot_ws/install/setup.bash` tras `colcon build`
- **No** dejar funciones públicas sin docstring

---

## Qwen Robot Suite — estado de integración

> Ver `docs/decisions/ADR-002-qwen-robot-suite.md` para el análisis completo.

**Resumen ejecutivo:**

| Modelo | Pesos públicos | Integrable ahora | Alternativa actual |
|--------|---------------|------------------|--------------------|
| Qwen-RobotNav-4B | ❌ No liberados | ❌ No | Qwen2.5-VL-7B + Nav2 |
| Qwen-RobotManip | ❌ No liberados | ❌ No | N/A (no manipulación) |
| Qwen-RobotWorld | ❌ No liberados | ❌ No | N/A |
| Qwen2.5-VL-7B | ✅ Disponible | ✅ Sí | — |
| Qwen2.5-7B | ✅ Disponible | ✅ Sí | — |

**Cuando liberen los pesos** (seguir https://github.com/QwenLM/Qwen-RobotNav):
- Reemplazar `perceive_skill.py` + `nav_skill.py` con llamadas a Qwen-RobotNav-4B
- El modelo acepta imágenes de cámara + instrucción → devuelve waypoints directamente
- Latencia estimada: ~200ms por inferencia (dato de despliegue en Jetson Thor)