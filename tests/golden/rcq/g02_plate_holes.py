import cadquery as cq

plate = cq.Workplane("XY").box(60, 40, 5, centered=(True, True, False))
result = plate.faces(">Z").workplane().pushPoints([(24, 14), (-24, 14), (24, -14), (-24, -14)]).hole(6)
