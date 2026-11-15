import cadquery as cq

result = cq.Workplane("XY").box(10, 10, 10).edges("|Z and >X").fillet(1)
