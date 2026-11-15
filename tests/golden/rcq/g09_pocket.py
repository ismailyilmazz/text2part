import cadquery as cq

block = cq.Workplane("XY").box(60, 40, 15, centered=(True, True, False))
tool = cq.Workplane("XY").workplane(offset=10).center(5, -3).rect(30, 20).extrude(6).edges("|Z").fillet(3)
result = block.cut(tool)
