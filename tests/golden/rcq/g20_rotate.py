import cadquery as cq

result = cq.Workplane("XY").box(60, 40, 5).rotate((0, 0, 0), (0, 0, 1), 90)
