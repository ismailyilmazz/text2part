import cadquery as cq
result = cq.Workplane("XY").moveTo(-30.0, -20.0).lineTo(30.0, -20.0).lineTo(30.0, 20.0).lineTo(-30.0, 20.0).close().extrude(15.0)
result = result.faces(">Z").workplane().moveTo(-12.0, -10.0).lineTo(12.0, -10.0).threePointArc((14.121320343559642, -9.121320343559642), (15.0, -7.0)).lineTo(15.0, 7.0).threePointArc((14.121320343559642, 9.121320343559642), (12.0, 10.0)).lineTo(-12.0, 10.0).threePointArc((-14.121320343559642, 9.121320343559642), (-15.0, 7.0)).lineTo(-15.0, -7.0).threePointArc((-14.121320343559642, -9.121320343559642), (-12.0, -10.0)).close().cutBlind(-5.0)
