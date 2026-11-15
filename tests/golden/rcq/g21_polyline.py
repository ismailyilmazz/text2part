import cadquery as cq

result = cq.Workplane("XY").polyline([(0, 0), (40, 0), (0, 30)]).close().extrude(10)
