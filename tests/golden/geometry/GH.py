import cadquery as cq
result = cq.Workplane("XY").box(60.0, 40.0, 5.0, centered=(True, True, False))
result = result.cut(cq.Workplane("XY").workplane(offset=-1.0).pushPoints([(24.0, 14.0), (-24.0, 14.0), (24.0, -14.0), (-24.0, -14.0)]).circle(3.0).extrude(7.0))
result = result.translate((5, 0, 2.5))
