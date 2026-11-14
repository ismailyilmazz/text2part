import math
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

SCHEMA_VERSION = "1.0"
MIN_SIZE = 0.5
MAX_SIZE = 2000.0
MAX_HOLE_DIAMETER = 500.0
MIN_WALL = 0.5
MIN_DETAIL = 0.1
MIN_FEATURE_VOLUME = 0.05
MAX_HOLES = 200
MAX_POCKETS = 20
MAX_SCHEMA_ISSUES = 20
MAX_ISSUES = 50
EPS = 1e-9

Size = Annotated[float, Field(strict=True, ge=MIN_SIZE, le=MAX_SIZE)]
Positive = Annotated[float, Field(strict=True, gt=0, le=MAX_SIZE)]
NonNegative = Annotated[float, Field(strict=True, ge=0, le=MAX_SIZE)]
Coordinate = Annotated[float, Field(strict=True, ge=-MAX_SIZE, le=MAX_SIZE)]
Count = Annotated[int, Field(strict=True, ge=1, le=MAX_HOLES)]
Point = tuple[Coordinate, Coordinate]


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)


class Body(Model):
    length: Size
    width: Size
    height: Size


class ExplicitCenters(Model):
    kind: Literal["explicit"]
    centers: Annotated[tuple[Point, ...], Field(min_length=1, max_length=MAX_HOLES)]


class Corners(Model):
    kind: Literal["corners"]
    inset: Positive


class Grid(Model):
    kind: Literal["grid"]
    nx: Count
    ny: Count
    pitch_x: Positive
    pitch_y: Positive
    center: Point = (0.0, 0.0)


class HoleGroup(Model):
    diameter: Annotated[float, Field(strict=True, ge=MIN_SIZE, le=MAX_HOLE_DIAMETER)]
    depth: Positive | Literal["through"]
    placement: Annotated[ExplicitCenters | Corners | Grid, Field(discriminator="kind")]


class Pocket(Model):
    center: Point
    length: Size
    width: Size
    depth: Positive
    corner_radius: NonNegative = 0.0


class Spec(Model):
    schema_version: Literal["1.0"]
    units: Literal["mm"]
    family: Literal["plate", "block"]
    body: Body
    corner_radius: NonNegative = 0.0
    holes: Annotated[tuple[HoleGroup, ...], Field(max_length=MAX_HOLES)] = ()
    pockets: Annotated[tuple[Pocket, ...], Field(max_length=MAX_POCKETS)] = ()
    manufacturing_tolerance: Literal["f", "m", "c", "v"] | None = None


UnsupportedName = Literal[
    "thread",
    "counterbore",
    "countersink",
    "chamfer",
    "slot",
    "side_hole",
    "hole_in_pocket",
    "through_pocket",
    "boss",
    "edge_fillet",
    "non_rectangular_outline",
    "inch_units",
    "text",
    "assembly",
    "other",
]


class UnsupportedFeature(Model):
    feature: UnsupportedName
    detail: Annotated[str, Field(max_length=200)] = ""


class Question(Model):
    id: Annotated[str, Field(min_length=1, max_length=40, pattern=r"^[a-z0-9_]+$")]
    text: Annotated[str, Field(min_length=1, max_length=300)]
    options: Annotated[tuple[Annotated[str, Field(min_length=1, max_length=120)], ...], Field(max_length=6)] = ()


class SpecProposal(Model):
    spec: Spec | None = None
    unsupported: Annotated[tuple[UnsupportedFeature, ...], Field(max_length=10)] = ()
    questions: Annotated[tuple[Question, ...], Field(max_length=5)] = ()


class Hole(Model):
    x: float
    y: float
    diameter: float
    depth: float | None


class Issue(Model):
    code: str
    severity: Literal["invalid", "unsupported"]
    path: str
    message: str


Status = Literal["VALID", "INVALID", "NEEDS_ANSWER", "UNSUPPORTED"]


class SpecReport(Model):
    status: Status
    issues: tuple[Issue, ...] = ()
    spec: Spec | None = None
    holes: tuple[Hole, ...] = ()
    questions: tuple[Question, ...] = ()
    unsupported: tuple[UnsupportedFeature, ...] = ()


def expand_holes(spec: Spec) -> tuple[Hole, ...]:
    half_x, half_y = spec.body.length / 2, spec.body.width / 2
    holes = []
    for group in spec.holes:
        placement = group.placement
        if isinstance(placement, ExplicitCenters):
            centers = list(placement.centers)
        elif isinstance(placement, Corners):
            x, y = half_x - placement.inset, half_y - placement.inset
            centers = [(x, y), (-x, y), (x, -y), (-x, -y)]
        else:
            cx, cy = placement.center
            centers = [
                (
                    cx + (i - (placement.nx - 1) / 2) * placement.pitch_x,
                    cy + (j - (placement.ny - 1) / 2) * placement.pitch_y,
                )
                for i in range(placement.nx)
                for j in range(placement.ny)
            ]
        depth = None if group.depth == "through" else float(group.depth)
        holes.extend(Hole(x=float(x), y=float(y), diameter=group.diameter, depth=depth) for x, y in centers)
    return tuple(holes)


def hole_count(spec: Spec) -> int:
    total = 0
    for group in spec.holes:
        placement = group.placement
        if isinstance(placement, ExplicitCenters):
            total += len(placement.centers)
        elif isinstance(placement, Corners):
            total += 4
        else:
            total += placement.nx * placement.ny
    return total


def rounded_rect_distance(px: float, py: float, half_x: float, half_y: float, radius: float) -> float:
    qx = abs(px) - (half_x - radius)
    qy = abs(py) - (half_y - radius)
    return math.hypot(max(qx, 0.0), max(qy, 0.0)) + min(max(qx, qy), 0.0) - radius


def rounded_rect_area(length: float, width: float, radius: float) -> float:
    return length * width - (4 - math.pi) * radius**2


def hole_volume(hole: Hole, height: float) -> float:
    return math.pi * (hole.diameter / 2) ** 2 * (height if hole.depth is None else hole.depth)


def pocket_volume(pocket: Pocket) -> float:
    return rounded_rect_area(pocket.length, pocket.width, pocket.corner_radius) * pocket.depth


def nominal_volume(spec: Spec) -> float:
    body = spec.body
    volume = rounded_rect_area(body.length, body.width, spec.corner_radius) * body.height
    volume -= sum(hole_volume(hole, body.height) for hole in expand_holes(spec))
    volume -= sum(pocket_volume(pocket) for pocket in spec.pockets)
    return volume


def invalid(code: str, path: str, message: str) -> Issue:
    return Issue(code=code, severity="invalid", path=path, message=message)


def unsupported(code: str, path: str, message: str) -> Issue:
    return Issue(code=code, severity="unsupported", path=path, message=message)


def pocket_inner(pocket: Pocket) -> tuple[float, float]:
    return pocket.length / 2 - pocket.corner_radius, pocket.width / 2 - pocket.corner_radius


def radius_problem(radius: float, shorter_side: float) -> str | None:
    if radius == 0:
        return None
    if radius < MIN_DETAIL - EPS:
        return f"Corner radius must be 0 or at least {MIN_DETAIL} mm."
    if 2 * radius > shorter_side - MIN_DETAIL + EPS:
        return f"Corner radius must leave at least {MIN_DETAIL} mm of straight edge on the shorter side."
    return None


def check_spec(spec: Spec) -> tuple[Issue, ...]:
    issues: list[Issue] = []
    body = spec.body
    half_x, half_y = body.length / 2, body.width / 2

    problem = radius_problem(spec.corner_radius, min(body.length, body.width))
    if problem:
        return (invalid("R6", "corner_radius", problem),)

    def clearance(px: float, py: float) -> float:
        return -rounded_rect_distance(px, py, half_x, half_y, spec.corner_radius)

    count = hole_count(spec)
    if count > MAX_HOLES:
        return (invalid("R9", "holes", f"Too many holes: {count}, the limit is {MAX_HOLES}."),)

    for index, group in enumerate(spec.holes):
        if isinstance(group.placement, Corners) and group.placement.inset >= min(half_x, half_y) - EPS:
            issues.append(
                invalid(
                    "R1",
                    f"holes.{index}.placement.inset",
                    "Corner inset must be smaller than half of the shorter side.",
                )
            )
        if group.depth != "through":
            if group.depth < MIN_DETAIL - EPS:
                issues.append(
                    invalid("R3", f"holes.{index}.depth", f"Blind hole depth must be at least {MIN_DETAIL} mm.")
                )
            elif group.depth >= body.height - EPS:
                issues.append(
                    invalid(
                        "R3",
                        f"holes.{index}.depth",
                        'Blind hole depth must be smaller than the body height; use "through" for a through hole.',
                    )
                )
            elif body.height - group.depth < MIN_WALL - EPS:
                issues.append(
                    invalid(
                        "R3",
                        f"holes.{index}.depth",
                        f"Material left under a blind hole must be at least {MIN_WALL} mm.",
                    )
                )

    holes = expand_holes(spec)
    for index, hole in enumerate(holes):
        radius = hole.diameter / 2
        if clearance(hole.x, hole.y) - radius < MIN_WALL - EPS:
            issues.append(
                invalid(
                    "R1",
                    f"hole {index}",
                    f"Hole at ({hole.x:g}, {hole.y:g}) must stay at least {MIN_WALL} mm inside the body outline.",
                )
            )
        if hole_volume(hole, body.height) < MIN_FEATURE_VOLUME:
            issues.append(invalid("R8", f"hole {index}", "Hole is too small to be verified."))

    for first in range(len(holes)):
        for second in range(first + 1, len(holes)):
            a, b = holes[first], holes[second]
            if math.hypot(a.x - b.x, a.y - b.y) - (a.diameter + b.diameter) / 2 < MIN_WALL - EPS:
                issues.append(
                    invalid(
                        "R2",
                        f"hole {first}, hole {second}",
                        f"Holes at ({a.x:g}, {a.y:g}) and ({b.x:g}, {b.y:g}) overlap or are closer than {MIN_WALL} mm.",
                    )
                )

    for index, pocket in enumerate(spec.pockets):
        path = f"pockets.{index}"
        problem = radius_problem(pocket.corner_radius, min(pocket.length, pocket.width))
        if problem:
            issues.append(invalid("R5", f"{path}.corner_radius", problem))
            continue
        if pocket.depth < MIN_DETAIL - EPS:
            issues.append(invalid("R4", f"{path}.depth", f"Pocket depth must be at least {MIN_DETAIL} mm."))
        elif pocket.depth >= body.height - EPS:
            issues.append(
                unsupported(
                    "U_THROUGH_POCKET", f"{path}.depth", "A pocket that cuts through the whole body is not supported."
                )
            )
        elif body.height - pocket.depth < MIN_WALL - EPS:
            issues.append(
                invalid("R4", f"{path}.depth", f"Material left under a pocket must be at least {MIN_WALL} mm.")
            )
        inner_x, inner_y = pocket_inner(pocket)
        cx, cy = pocket.center
        corners = [(cx + sx * inner_x, cy + sy * inner_y) for sx in (-1, 1) for sy in (-1, 1)]
        if min(clearance(px, py) for px, py in corners) - pocket.corner_radius < MIN_WALL - EPS:
            issues.append(invalid("R4", path, f"Pocket must stay at least {MIN_WALL} mm inside the body outline."))
        if pocket_volume(pocket) < MIN_FEATURE_VOLUME:
            issues.append(invalid("R8", path, "Pocket is too small to be verified."))
        for hole_index, hole in enumerate(holes):
            gap = (
                rounded_rect_distance(
                    hole.x - cx, hole.y - cy, pocket.length / 2, pocket.width / 2, pocket.corner_radius
                )
                - hole.diameter / 2
            )
            if gap < -EPS:
                issues.append(
                    unsupported(
                        "U_HOLE_IN_POCKET",
                        f"hole {hole_index}, {path}",
                        "A hole that intersects a pocket is not supported.",
                    )
                )
            elif gap < MIN_WALL - EPS:
                issues.append(
                    invalid(
                        "R7", f"hole {hole_index}, {path}", f"A hole and a pocket must be at least {MIN_WALL} mm apart."
                    )
                )

    for first in range(len(spec.pockets)):
        for second in range(first + 1, len(spec.pockets)):
            a, b = spec.pockets[first], spec.pockets[second]
            if radius_problem(a.corner_radius, min(a.length, a.width)) or radius_problem(
                b.corner_radius, min(b.length, b.width)
            ):
                continue
            (ax, ay), (bx, by) = pocket_inner(a), pocket_inner(b)
            dx = max(abs(a.center[0] - b.center[0]) - (ax + bx), 0.0)
            dy = max(abs(a.center[1] - b.center[1]) - (ay + by), 0.0)
            if math.hypot(dx, dy) - a.corner_radius - b.corner_radius < MIN_WALL - EPS:
                issues.append(
                    invalid(
                        "R10",
                        f"pockets.{first}, pockets.{second}",
                        f"Pockets overlap or are closer than {MIN_WALL} mm.",
                    )
                )

    issues.sort(key=lambda issue: issue.severity != "unsupported")
    return tuple(issues[:MAX_ISSUES])


def check_proposal(data: object) -> SpecReport:
    try:
        proposal = data if isinstance(data, SpecProposal) else SpecProposal.model_validate(data)
    except ValidationError as error:
        issues = tuple(
            invalid("SCHEMA", ".".join(str(part) for part in item["loc"]), item["msg"])
            for item in error.errors()[:MAX_SCHEMA_ISSUES]
        )
        return SpecReport(status="INVALID", issues=issues)

    issues = [
        unsupported("U_DECLARED", f"unsupported.{index}", f"Not supported: {item.feature}.")
        for index, item in enumerate(proposal.unsupported)
    ]
    holes: tuple[Hole, ...] = ()
    if proposal.spec is None:
        if not proposal.unsupported and not proposal.questions:
            issues.append(invalid("EMPTY", "spec", "The proposal has no specification."))
    else:
        issues.extend(check_spec(proposal.spec))
        if not any(issue.severity == "invalid" for issue in issues):
            holes = expand_holes(proposal.spec)

    if any(issue.severity == "unsupported" for issue in issues):
        status: Status = "UNSUPPORTED"
    elif proposal.questions:
        status = "NEEDS_ANSWER"
    elif issues:
        status = "INVALID"
    else:
        status = "VALID"
    return SpecReport(
        status=status,
        issues=tuple(issues),
        spec=proposal.spec,
        holes=holes,
        questions=proposal.questions,
        unsupported=proposal.unsupported,
    )
