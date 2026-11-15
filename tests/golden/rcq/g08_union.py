import cadquery as cq

base = cq.Workplane("XY").box(40, 40, 10)
post = cq.Workplane("XY").circle(5).extrude(30).translate((0, 0, 5))
result = base.union(post)
