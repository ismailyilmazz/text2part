import cadquery as cq
plate = cq.Workplane("XY").box(60, 40, 5, centered=(True, True, False))
part = plate.faces(">Z").workplane().pushPoints([(24, 14), (-24, 14), (24, -14), (-24, -14)]).hole(6)
result = part.rotate((0, 0, 0), (0, 0, 1), 45)
