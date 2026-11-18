import math
from dataclasses import dataclass

from OCP.Bnd import Bnd_OBB
from OCP.BRepAdaptor import BRepAdaptor_Surface
from OCP.BRepBndLib import BRepBndLib
from OCP.BRepGProp import BRepGProp, BRepGProp_Face
from OCP.BRepTools import BRepTools
from OCP.gp import gp_Pnt, gp_Vec
from OCP.GProp import GProp_GProps

FULL_TURN = 2 * math.pi
TURN_TOLERANCE = 1e-6
AXIS_TOLERANCE = 1e-9
LEVEL_TOLERANCE = 1e-6


@dataclass(frozen=True)
class Box:
    xmin: float
    ymin: float
    zmin: float
    xmax: float
    ymax: float
    zmax: float

    @property
    def size(self) -> tuple:
        return (self.xmax - self.xmin, self.ymax - self.ymin, self.zmax - self.zmin)

    @property
    def center(self) -> tuple:
        return ((self.xmin + self.xmax) / 2, (self.ymin + self.ymax) / 2, (self.zmin + self.zmax) / 2)


@dataclass(frozen=True)
class Cylinder:
    x: float
    y: float
    radius: float
    zmin: float
    zmax: float
    span: float
    concave: bool
    vertical: bool


@dataclass(frozen=True)
class Hole:
    x: float
    y: float
    diameter: float
    zmin: float
    zmax: float


@dataclass(frozen=True)
class Floor:
    box: Box


@dataclass(frozen=True)
class Arc:
    x: float
    y: float
    radius: float
    zmin: float
    zmax: float


@dataclass(frozen=True)
class Measurement:
    solids: int
    valid: bool
    volume: float
    box: Box
    oriented_size: tuple
    holes: tuple
    bosses: tuple
    floors: tuple
    concave_arcs: tuple
    convex_arcs: tuple
    unexpected: tuple


def volume_of(shape: object) -> float:
    if shape is None or shape.IsNull():
        return 0.0
    properties = GProp_GProps()
    BRepGProp.VolumeProperties_s(shape, properties)
    return properties.Mass()


def center_of(shape: object) -> tuple:
    properties = GProp_GProps()
    BRepGProp.VolumeProperties_s(shape, properties)
    point = properties.CentreOfMass()
    return (point.X(), point.Y(), point.Z())


def box_of(shape: object) -> Box:
    box = shape.BoundingBox()
    return Box(box.xmin, box.ymin, box.zmin, box.xmax, box.ymax, box.zmax)


def oriented_size(shape: object) -> tuple:
    oriented = Bnd_OBB()
    BRepBndLib.AddOBB_s(shape.wrapped, oriented, False, True, False)
    return tuple(sorted(2 * value for value in (oriented.XHSize(), oriented.YHSize(), oriented.ZHSize())))


def surface_normal(face: object) -> tuple:
    umin, umax, vmin, vmax = BRepTools.UVBounds_s(face.wrapped)
    point, normal = gp_Pnt(), gp_Vec()
    BRepGProp_Face(face.wrapped).Normal((umin + umax) / 2, (vmin + vmax) / 2, point, normal)
    length = normal.Magnitude()
    return (point.X(), point.Y(), point.Z()), tuple(item / length for item in (normal.X(), normal.Y(), normal.Z()))


def cylinder_of(face: object) -> Cylinder:
    surface = BRepAdaptor_Surface(face.wrapped).Cylinder()
    axis = surface.Axis()
    origin, direction = axis.Location(), axis.Direction()
    umin, umax, _, _ = BRepTools.UVBounds_s(face.wrapped)
    point, normal = surface_normal(face)
    base = (origin.X(), origin.Y(), origin.Z())
    along = (direction.X(), direction.Y(), direction.Z())
    offset = tuple(p - b for p, b in zip(point, base))
    reach = sum(o * a for o, a in zip(offset, along))
    radial = tuple(o - reach * a for o, a in zip(offset, along))
    concave = sum(n * r for n, r in zip(normal, radial)) < 0
    vertical = abs(abs(along[2]) - 1) <= AXIS_TOLERANCE
    x, y = base[0], base[1]
    if vertical:
        x, y = base[0] - base[2] * along[0] / along[2], base[1] - base[2] * along[1] / along[2]
    box = box_of(face)
    return Cylinder(x, y, surface.Radius(), box.zmin, box.zmax, umax - umin, concave, vertical)


def close(first: tuple, second: tuple) -> bool:
    return all(abs(a - b) <= LEVEL_TOLERANCE for a, b in zip(first, second))


def full_groups(cylinders: list) -> tuple:
    groups: list = []
    for cylinder in cylinders:
        signature = (cylinder.x, cylinder.y, cylinder.radius)
        for members in groups:
            if close(signature, (members[0].x, members[0].y, members[0].radius)):
                members.append(cylinder)
                break
        else:
            groups.append([cylinder])
    full, partial = [], []
    for members in groups:
        if sum(member.span for member in members) >= FULL_TURN - TURN_TOLERANCE:
            full.append(members)
        else:
            partial.extend(members)
    return full, partial


def axis_aligned(normal: tuple) -> bool:
    return max(abs(item) for item in normal) >= 1 - AXIS_TOLERANCE


def measure(shape: object) -> Measurement:
    box = box_of(shape)
    concave, convex, unexpected, floors = [], [], [], []
    for face in shape.Faces():
        kind = face.geomType()
        if kind == "CYLINDER":
            cylinder = cylinder_of(face)
            if not cylinder.vertical:
                unexpected.append("CYLINDER (not vertical)")
            elif cylinder.concave:
                concave.append(cylinder)
            else:
                convex.append(cylinder)
        elif kind == "PLANE":
            _, normal = surface_normal(face)
            if not axis_aligned(normal):
                unexpected.append("PLANE (slanted)")
                continue
            level = box_of(face)
            inside = box.zmin + LEVEL_TOLERANCE < level.zmin < box.zmax - LEVEL_TOLERANCE
            if normal[2] > 0.5 and inside:
                floors.append(Floor(level))
            elif normal[2] < -0.5 and inside:
                unexpected.append("PLANE (facing down inside the part)")
        else:
            unexpected.append(kind)

    full_concave, partial_concave = full_groups(concave)
    full_convex, partial_convex = full_groups(convex)
    holes = tuple(
        Hole(
            members[0].x,
            members[0].y,
            2 * members[0].radius,
            min(member.zmin for member in members),
            max(member.zmax for member in members),
        )
        for members in full_concave
    )
    bosses = tuple(
        Hole(
            members[0].x,
            members[0].y,
            2 * members[0].radius,
            min(member.zmin for member in members),
            max(member.zmax for member in members),
        )
        for members in full_convex
    )
    pocket_floors = []
    for floor in floors:
        x, y, _ = floor.box.center
        size = floor.box.size
        disc = (x, y, size[0], size[1], floor.box.zmin)
        if any(close(disc, (hole.x, hole.y, hole.diameter, hole.diameter, hole.zmin)) for hole in holes):
            continue
        pocket_floors.append(floor)
    return Measurement(
        solids=len(shape.Solids()),
        valid=bool(shape.isValid()),
        volume=volume_of(shape.wrapped),
        box=box,
        oriented_size=oriented_size(shape),
        holes=tuple(sorted(holes, key=lambda hole: (hole.x, hole.y, hole.diameter))),
        bosses=bosses,
        floors=tuple(pocket_floors),
        concave_arcs=tuple(Arc(c.x, c.y, c.radius, c.zmin, c.zmax) for c in partial_concave),
        convex_arcs=tuple(Arc(c.x, c.y, c.radius, c.zmin, c.zmax) for c in partial_convex),
        unexpected=tuple(sorted(set(unexpected))),
    )
