import cadquery as cq

result = cq.Workplane("XY").transformed(offset=(0, 0, 10), rotate=(0, 0, 45)).rect(10, 20).extrude(5)
