import cadquery as cq
result = cq.Workplane("XY").polyline([(-30, -20), (30, 20), (30, -20), (-30, 20)]).close().extrude(5)
