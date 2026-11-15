import cadquery as cq

base = cq.Workplane("XY").box(40, 40, 10)
post = cq.Workplane("XY").circle(5).extrude(30)
result = cq.Workplane("XY").add(base).union(post)
