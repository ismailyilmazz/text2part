import cadquery as cq

result = cq.Workplane("XY").rect(10, 10).extrude(5, taper=10)
