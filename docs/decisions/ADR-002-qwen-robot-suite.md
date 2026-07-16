# ADR-002: Estado de integración de Qwen Robot Suite

**Fecha:** 2026-07-14
**Estado:** Aceptado (revisar cuando se liberen pesos)

## Contexto

Alibaba anunció la familia Qwen-RobotNav / Qwen-RobotManip / Qwen-RobotWorld,
modelos especializados para navegación y manipulación robótica que aceptarían
imágenes de cámara + instrucción y devolverían waypoints directamente,
sustituyendo la combinación actual de Qwen2.5-VL + Nav2.

## Decisión

No integrar Qwen-RobotNav/Manip/World todavía: sus pesos no están públicamente
liberados. Usar la combinación disponible ahora:

| Modelo | Pesos públicos | Integrable ahora | Alternativa actual |
|--------|---------------|------------------|--------------------|
| Qwen-RobotNav-4B | No liberados | No | Qwen2.5-VL-7B + Nav2 |
| Qwen-RobotManip | No liberados | No | N/A (no manipulación) |
| Qwen-RobotWorld | No liberados | No | N/A |
| Qwen2.5-VL-7B | Disponible | Sí | — |
| Qwen2.5-7B | Disponible | Sí | — |

## Razones

- `perceive_skill.py` usa Qwen2.5-VL-7B vía Ollama para describir la escena y
  `nav_skill.py` usa Nav2 SimpleCommander para el movimiento — combinación
  funcional hoy sin depender de pesos no publicados.

## Consecuencias

- Cuando Qwen-RobotNav-4B publique pesos (seguir
  https://github.com/QwenLM/Qwen-RobotNav), reemplazar `perceive_skill.py` +
  `nav_skill.py` por llamadas directas al modelo, que aceptaría imagen +
  instrucción y devolvería waypoints (latencia estimada ~200ms en Jetson Thor
  según los datos de despliegue publicados).
- Mientras tanto, el pipeline perceive → update_map → navigate se mantiene
  como dos pasos separados en vez de uno solo.
