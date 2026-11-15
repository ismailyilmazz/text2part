import cadquery as cq

size = (60, 40, 5)
inset = 6
x = size[0] / 2 - inset
y = size[1] / 2 - inset
corners = [(x, y), (-x, y), (x, -y), (-x, -y)]
plate = cq.Workplane("XY").box(*size)
result = plate.faces(">Z").workplane().pushPoints(corners).hole(2 * 3)
