import cadquery as cq

outline = cq.Workplane("XY").moveTo(-30, -20).lineTo(30, -20).lineTo(30, 20).lineTo(-30, 20).close()
plate = outline.extrude(5)
holes = cq.Workplane("XY").pushPoints([(24, 14), (-24, 14), (24, -14), (-24, -14)]).circle(3).extrude(5)
result = plate.cut(holes)
