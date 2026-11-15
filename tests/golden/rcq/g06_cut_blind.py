import cadquery as cq

block = cq.Workplane("XY").box(50, 30, 20)
result = block.faces(">Z").workplane().rect(20, 10).cutBlind(-6)
