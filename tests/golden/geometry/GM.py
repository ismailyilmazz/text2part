import cadquery as cq
plate = cq.Workplane("XY").box(60, 40, 5, centered=(True, True, False))
part = plate.faces(">Z").workplane().pushPoints([(24, 14), (-24, 14), (24, -14), (-24, -14)]).hole(6)
result = part.union(cq.Workplane("XY").box(5, 5, 5).translate((100, 0, 0)))
