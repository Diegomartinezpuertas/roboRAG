# ADR-010: Redimensionar el canvas del mapa en cada frame

**Fecha:** 2026-07-14
**Estado:** Aceptado

## Contexto

La selección de áreas en el mapa del dashboard ("va mal") aterrizaba en
coordenadas del mundo incorrectas. `resizeCanvas()` solo se llamaba una vez
al cargar la página y en el evento `window.resize`, pero `canvas.width` /
`canvas.height` (el tamaño interno del buffer de píxeles, usado en toda la
matemática `w2c`/`c2w`) se desincronizaba del tamaño real en pantalla cada
vez que el layout cambiaba por otros motivos: aparecían chips de zonas,
crecía el timeline, cargaba la imagen del mapa — nada de eso dispara
`resize` de `window`, que solo reacciona a cambios del tamaño de la
ventana del navegador.

## Decisión

`resizeCanvas()` se llama en cada frame de `draw()` (via
`requestAnimationFrame`), comparando `canvas.width/height` contra el
`getBoundingClientRect()` actual y actualizando solo si difieren (evita
relimpiar el canvas cuando no hace falta).

## Consecuencias

- Corrección de coste ~cero (una comparación de dos números por frame).
- Cualquier otro elemento interactivo que se añada al dashboard en canvas
  debe seguir el mismo patrón, no fiarse de `window.resize`.
