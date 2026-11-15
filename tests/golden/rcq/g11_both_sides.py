import cadquery as cq

result = cq.Workplane("XY").rect(10, 20).extrude(4, both=True)
