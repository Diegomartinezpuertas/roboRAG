string   object_id      # Unique identifier for the detected object
string   label          # Object class label, e.g. "chair", "door", "bottle"
float32  confidence     # Detection confidence [0.0, 1.0]
geometry_msgs/Pose pose # 2D/3D pose of the object in the map frame
string   description    # Free-text description: scene descriptor output, zone text, or a seeded landmark
string   room_zone      # Semantic zone the object belongs to, e.g. "kitchen", "corridor"
builtin_interfaces/Time timestamp  # Time the object was last observed
string   group_key      # Observations sharing this key within the merge radius are one memory; empty = never merged
