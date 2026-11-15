import cadquery as cq

block = cq.Workplane("XY").box(20, 20, 10)
result = block.faces(">Z").workplane(centerOption="CenterOfMass", offset=0).circle(2).cutBlind(-3, True)
