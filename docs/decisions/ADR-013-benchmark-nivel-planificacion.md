# ADR-013: Benchmark de RAG a nivel de planificación (no end-to-end)

**Fecha:** 2026-07-16
**Estado:** Aceptado

## Contexto

La hipótesis del proyecto es "el RAG mejora la navegación en lenguaje
natural". El plan original (PLAN_RELEASE Fase 3) medía SR/SPL end-to-end:
conducir el robot a cada landmark, sembrar la pose real, y ejecutar tareas
midiendo si el robot llega.

Al intentarlo se chocó con la realidad de la simulación en WSL2 con render por
software:

- El robot se **encaja** contra las paredes/puertas estrechas de
  `turtlebot3_house` y el `collision_monitor` + recuperación Spin no lo
  desatascan (todas las poses alcanzadas colapsaban al mismo punto).
- El skill `navigate` a veces **reporta éxito mientras el robot está en otro
  sitio**, y las puertas estrechas hacen la navegación entre puntos poco
  fiable.

Con esa inestabilidad, un SR/SPL end-to-end no produce números con sentido
estadístico sin muchas horas de sim-wrangling y mejor hardware.

## Decisión

Medir la hipótesis en su **mecanismo causal**, a nivel de la decisión de
planificación, que es reproducible y no depende de la navegación de bajo
nivel:

> ¿El planner con RAG navega DIRECTO a las coordenadas del landmark recordado,
> frente a caer en EXPLORE a ciegas sin RAG?

Implementación:
- Modo `dry_run` en `llm_planner_node`: produce y publica el plan pero no
  ejecuta (no conduce el robot).
- Topic `/robot/plan` con el JSON crudo del plan por goal.
- `eval/run_benchmark.py` recorre condiciones × tareas × repeticiones,
  captura el plan y lo clasifica: `direct_nav` (paso navigate a ±0.75 m del
  landmark), `explore`, u `other`.
- `temperature=0` → reproducible; el RAG (retrieval real vía `/rag/query`) es
  la única variable entre condiciones.

Los landmarks se siembran directamente en `semantic_map` en coordenadas libres
distintas del mapa (no conduciendo), porque para medir la decisión solo hacen
falta coordenadas plausibles y distintas.

## Consecuencias

- El resultado headline es defendible y reproducible: cuantifica que el RAG
  cambia la decisión de navegación (directo vs explorar). Es el mecanismo de
  la hipótesis, no un proxy lejano.
- La ejecución end-to-end (SR/SPL) queda como demostración cualitativa y como
  **trabajo futuro** sobre mejor hardware/mundo con menos cuellos de botella
  de navegación. El harness end-to-end (reset-a-home, integración de odometría,
  SPL) queda escrito en el historial de `run_benchmark.py` para reactivarlo.
- Honestidad: el README debe presentar esto como "medido a nivel de
  planificación" y explicar por qué, no venderlo como SR físico.
