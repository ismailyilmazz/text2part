import cadquery as cq
plate = cq.Workplane("XY").box(40, 60, 5, centered=(True, True, False))
result = plate.faces(">Z").workplane().pushPoints([(14, 24), (-14, 24), (14, -24), (-14, -24)]).hole(6)
