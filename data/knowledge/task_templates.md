## Template: go to a zone and list objects

Goal pattern: "Ve a <zone> y dime qué objetos hay" / "Go to <zone> and tell me
what objects are there".
Plan: navigate(zone) -> perceive(query="list all objects") -> report(format="natural_language").

## Template: explore and count objects

Goal pattern: "Explora el entorno y dime cuántos <object_class> hay" / "Explore
the environment and tell me how many <object_class> there are".
Plan: explore(duration_sec) -> perceive(query="count <object_class>") -> report(format="natural_language").

## Template: find a specific object

Goal pattern: "Busca <object_class>" / "Find <object_class>".
Plan: query RAG semantic_map for <object_class> first; if found, navigate to
its stored pose; if not found, explore(duration_sec) -> perceive(query="find <object_class>").

## Template: describe current location

Goal pattern: "Dime dónde estás" / "Describe your current location".
Plan: perceive(query="describe surroundings") -> report(format="natural_language").
