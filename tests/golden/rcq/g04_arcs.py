import cadquery as cq

profile = cq.Workplane("XY").moveTo(-20, -10).lineTo(20, -10).threePointArc((30, 0), (20, 10)).lineTo(-20, 10)
result = profile.threePointArc((-30, 0), (-20, -10)).close().extrude(8)
