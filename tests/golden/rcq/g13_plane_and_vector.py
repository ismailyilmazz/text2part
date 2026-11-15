import cadquery as cq

part = cq.Workplane("XZ", origin=(0, 0, 5)).box(2, 4, 6)
result = part.translate(cq.Vector(1, 2, 3))
