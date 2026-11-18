import cadquery as cq
result = cq.Workplane("XY").moveTo(-25.0, -15.0).lineTo(25.0, -15.0).lineTo(25.0, 15.0).lineTo(-25.0, 15.0).close().extrude(20.0)
result = result.faces(">Z").workplane().pushPoints([(0.0, 0.0)]).hole(8.0, 10.0)
