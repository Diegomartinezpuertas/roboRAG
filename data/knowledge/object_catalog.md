## Known object classes

The robot's vision model (Qwen2.5-VL) is expected to recognize the following
object classes in the simulated apartment: chair, table, sofa, bed, door,
window, bottle, cup, plant, television, lamp, bookshelf, refrigerator, sink,
stove.

## Object description format

When perceive_skill reports an object, the description should include the
object's approximate color, size (small/medium/large), and any notable state
(open/closed for doors, on/off for lamps).

## Common object-to-room associations

Kitchen typically contains: refrigerator, stove, sink, table, chair.
Living room typically contains: sofa, television, bookshelf, lamp, plant.
Bedroom typically contains: bed, lamp, bookshelf, window.
Corridor typically contains: door.

These associations are heuristics only — always report what is actually
perceived, never assume an object is present without a detection.
