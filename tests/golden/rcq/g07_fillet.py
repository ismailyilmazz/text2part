import cadquery as cq

result = cq.Workplane("XY").box(80, 80, 8).edges("|Z").fillet(5)
