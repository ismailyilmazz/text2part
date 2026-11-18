import math
import time
from dataclasses import dataclass

import cadquery as cq
from OCP.BOPAlgo import BOPAlgo_BOP, BOPAlgo_Operation
from OCP.BRepCheck import BRepCheck_Analyzer

from text2part.core.builder import BuildError, build_reference
from text2part.core.measure import box_of, center_of, measure, oriented_size, volume_of
from text2part.core.spec import Spec, check_spec, expand_holes
from text2part.core.status import FAIL, PASS, UNSUPPORTED, UNVERIFIED, VERIFIED

DIFFERENCE_LIMIT = 1e-3
IDENTITY_LIMIT = 1e-4
DIAGNOSTIC_TOLERANCE = 0.01
EMPTY_VOLUME = 1e-9
MAX_PROBLEMS = 20


@dataclass(frozen=True)
class Check:
    name: str
    status: str
    code: str = ""
    message: str = ""
    problems: tuple = ()
    data: tuple = ()

    def as_dict(self) -> dict:
        return {
            "name": self.name,
            "status": self.status,
            "code": self.code,
            "message": self.message,
            "problems": list(self.problems),
            "data": {key: value for key, value in self.data},
        }


@dataclass(frozen=True)
class Comparison:
    reliable: bool
    reason: str = ""
    difference: float | None = None
    residual: float | None = None
    missing: float | None = None
    missing_center: tuple | None = None
    extra: float | None = None
    extra_center: tuple | None = None


@dataclass(frozen=True)
class Report:
    status: str
    code: str
    checks: tuple
    translation: tuple | None = None
    seconds: float = 0.0

    def check(self, name: str) -> Check | None:
        return next((item for item in self.checks if item.name == name), None)

    def as_dict(self) -> dict:
        return {
            "status": self.status,
            "code": self.code,
            "checks": [item.as_dict() for item in self.checks],
            "translation": list(self.translation) if self.translation is not None else None,
            "seconds": self.seconds,
        }


def run_boolean(operation: object, first: object, second: object) -> tuple:
    builder = BOPAlgo_BOP()
    builder.AddArgument(first)
    builder.AddTool(second)
    builder.SetOperation(operation)
    builder.Perform()
    if builder.HasErrors():
        return None, "the Boolean operation reported errors"
    shape = builder.Shape()
    if shape.IsNull():
        return None, "the Boolean operation returned a null shape"
    if not BRepCheck_Analyzer(shape).IsValid():
        return None, "the Boolean operation returned an invalid shape"
    return shape, ""


def compare(reference: object, candidate: object) -> Comparison:
    first = reference.wrapped
    second = candidate.wrapped
    if first.IsNull() or second.IsNull():
        return Comparison(False, "an operand is a null shape")
    results = []
    for operation, a, b in (
        (BOPAlgo_Operation.BOPAlgo_CUT, first, second),
        (BOPAlgo_Operation.BOPAlgo_CUT, second, first),
        (BOPAlgo_Operation.BOPAlgo_COMMON, first, second),
    ):
        shape, reason = run_boolean(operation, a, b)
        if shape is None:
            return Comparison(False, reason)
        results.append(shape)
    missing_shape, extra_shape, common_shape = results
    missing, extra, common = (volume_of(shape) for shape in results)
    residual = max(
        abs(missing - (volume_of(first) - common)),
        abs(extra - (volume_of(second) - common)),
    )
    if not math.isfinite(residual) or residual > IDENTITY_LIMIT:
        return Comparison(False, f"the volume identity does not hold (residual {residual:.3g} mm3)", residual=residual)
    return Comparison(
        True,
        difference=missing + extra,
        residual=residual,
        missing=missing,
        missing_center=center_of(missing_shape) if missing > EMPTY_VOLUME else None,
        extra=extra,
        extra_center=center_of(extra_shape) if extra > EMPTY_VOLUME else None,
    )


def close(first: float, second: float) -> bool:
    return abs(first - second) <= DIAGNOSTIC_TOLERANCE + 1e-9


def point(x: float, y: float) -> str:
    return f"({x:.2f}, {y:.2f})"


def summarize(name: str, problems: list, data: tuple = ()) -> Check:
    if not problems:
        return Check(name, PASS, data=data)
    code = problems[0][0]
    messages = tuple(message for _, message in problems[:MAX_PROBLEMS])
    if len(problems) > MAX_PROBLEMS:
        messages += (f"{len(problems) - MAX_PROBLEMS} more problem(s) not shown.",)
    return Check(name, FAIL, code, messages[0], messages, data)


def pair_up(expected: list, found: list, reach: object) -> tuple:
    candidates = []
    for i, want in enumerate(expected):
        for j, have in enumerate(found):
            distance = math.hypot(want[0] - have[0], want[1] - have[1])
            if distance <= reach(i):
                candidates.append((distance, i, j))
    used_expected, used_found, pairs = set(), set(), []
    for _, i, j in sorted(candidates):
        if i not in used_expected and j not in used_found:
            used_expected.add(i)
            used_found.add(j)
            pairs.append((i, j))
    missing = [i for i in range(len(expected)) if i not in used_expected]
    extra = [j for j in range(len(found)) if j not in used_found]
    return pairs, missing, extra


def hole_check(spec: Spec, measured: object) -> Check:
    height = spec.body.height
    expected = list(expand_holes(spec))
    clusters: list = []
    for hole in measured.holes:
        for members in clusters:
            if close(hole.x, members[0].x) and close(hole.y, members[0].y):
                members.append(hole)
                break
        else:
            clusters.append([hole])
    primaries = [min(members, key=lambda hole: hole.diameter) for members in clusters]
    problems = []
    if len(primaries) != len(expected):
        problems.append(("hole_count", f"Expected {len(expected)} hole(s), found {len(primaries)}."))
    for members in clusters:
        if len(members) > 1:
            sizes = ", ".join(f"{hole.diameter:.2f}" for hole in sorted(members, key=lambda hole: hole.diameter))
            where = point(members[0].x, members[0].y)
            problems.append(
                ("coaxial_feature", f"Hole at {where} has stepped diameters ({sizes} mm); only plain holes are wanted.")
            )
    pairs, missing, extra = pair_up(
        [(hole.x, hole.y) for hole in expected],
        [(hole.x, hole.y) for hole in primaries],
        lambda i: 2 * expected[i].diameter + 1.0,
    )
    for i, j in pairs:
        want, have = expected[i], primaries[j]
        where = point(want.x, want.y)
        if not (close(want.x, have.x) and close(want.y, have.y)):
            problems.append(("hole_position", f"Hole expected at {where} is at {point(have.x, have.y)}."))
        if not close(want.diameter, have.diameter):
            problems.append(
                (
                    "hole_diameter",
                    f"Hole at {where} has diameter {have.diameter:.2f} mm, expected {want.diameter:.2f} mm.",
                )
            )
        if not close(have.zmax, height):
            problems.append(
                ("hole_depth", f"Hole at {where} does not open on the top face (it ends at z = {have.zmax:.2f}).")
            )
        elif want.depth is None and not close(have.zmin, 0.0):
            problems.append(
                ("hole_depth", f"Hole at {where} should go through the part; it is {height - have.zmin:.2f} mm deep.")
            )
        elif want.depth is not None and not close(height - have.zmin, want.depth):
            problems.append(
                ("hole_depth", f"Hole at {where} is {height - have.zmin:.2f} mm deep, expected {want.depth:.2f} mm.")
            )
    for i in missing:
        want = expected[i]
        problems.append(
            ("missing_hole", f"Missing hole of diameter {want.diameter:.2f} mm at {point(want.x, want.y)}.")
        )
    for j in extra:
        have = primaries[j]
        problems.append(
            ("unexpected_hole", f"Unexpected hole of diameter {have.diameter:.2f} mm at {point(have.x, have.y)}.")
        )
    return summarize("holes", problems, (("expected", len(expected)), ("found", len(primaries))))


def arcs_in(arcs: tuple, floor_box: object, bottom: float, top: float) -> list:
    return [
        arc
        for arc in arcs
        if floor_box.xmin - DIAGNOSTIC_TOLERANCE <= arc.x <= floor_box.xmax + DIAGNOSTIC_TOLERANCE
        and floor_box.ymin - DIAGNOSTIC_TOLERANCE <= arc.y <= floor_box.ymax + DIAGNOSTIC_TOLERANCE
        and close(arc.zmin, bottom)
        and close(arc.zmax, top)
    ]


def pocket_check(spec: Spec, measured: object) -> tuple:
    height = spec.body.height
    expected = list(spec.pockets)
    floors = list(measured.floors)
    problems = []
    if len(floors) != len(expected):
        problems.append(("pocket_count", f"Expected {len(expected)} pocket(s), found {len(floors)}."))
    pairs, missing, extra = pair_up(
        [pocket.center for pocket in expected],
        [floor.box.center[:2] for floor in floors],
        lambda i: max(expected[i].length, expected[i].width),
    )
    used_arcs = set()
    for i, j in pairs:
        want, floor = expected[i], floors[j]
        x, y, _ = floor.box.center
        length, width, _ = floor.box.size
        where = point(*want.center)
        if not (close(want.center[0], x) and close(want.center[1], y)):
            problems.append(("pocket_position", f"Pocket expected at {where} is centred at {point(x, y)}."))
        if not (close(want.length, length) and close(want.width, width)):
            note = (
                " (it looks rotated by 90 degrees)" if close(want.length, width) and close(want.width, length) else ""
            )
            problems.append(
                (
                    "pocket_size",
                    f"Pocket at {where} is {length:.2f} x {width:.2f} mm, expected {want.length:.2f} x {want.width:.2f} mm"
                    f"{note}.",
                )
            )
        depth = height - floor.box.zmin
        if not close(depth, want.depth):
            problems.append(
                ("pocket_depth", f"Pocket at {where} is {depth:.2f} mm deep, expected {want.depth:.2f} mm.")
            )
        corners = arcs_in(measured.concave_arcs, floor.box, floor.box.zmin, height)
        used_arcs.update(corners)
        radii = sorted({round(arc.radius, 4) for arc in corners})
        if want.corner_radius == 0 and corners:
            problems.append(
                ("pocket_corner_radius", f"Pocket at {where} has rounded corners ({radii} mm); sharp corners expected.")
            )
        elif want.corner_radius > 0 and (
            len(corners) != 4 or not all(close(arc.radius, want.corner_radius) for arc in corners)
        ):
            problems.append(
                (
                    "pocket_corner_radius",
                    f"Pocket at {where} has corner radius {radii} mm, expected {want.corner_radius:.2f} mm.",
                )
            )
    for i in missing:
        want = expected[i]
        problems.append(
            ("missing_pocket", f"Missing pocket {want.length:.2f} x {want.width:.2f} mm at {point(*want.center)}.")
        )
    for j in extra:
        floor = floors[j]
        length, width, _ = floor.box.size
        problems.append(
            (
                "unexpected_pocket",
                f"Unexpected recess {length:.2f} x {width:.2f} mm at {point(*floor.box.center[:2])}, "
                f"floor at z = {floor.box.zmin:.2f}.",
            )
        )
    return summarize("pockets", problems, (("expected", len(expected)), ("found", len(floors)))), used_arcs


def corner_check(spec: Spec, measured: object) -> tuple:
    radius = spec.corner_radius
    body = spec.body
    corners = [arc for arc in measured.convex_arcs if close(arc.zmin, 0.0) and close(arc.zmax, body.height)]
    problems = []
    radii = sorted({round(arc.radius, 4) for arc in corners})
    if radius == 0 and corners:
        problems.append(("corner_radius", f"Body corners are rounded ({radii} mm); sharp corners expected."))
    elif radius > 0:
        spots = [(sx * (body.length / 2 - radius), sy * (body.width / 2 - radius)) for sx in (1, -1) for sy in (1, -1)]
        found = all(
            any(close(arc.x, x) and close(arc.y, y) and close(arc.radius, radius) for arc in corners) for x, y in spots
        )
        if not found or len(corners) != 4:
            problems.append(("corner_radius", f"Body corner radius is {radii} mm, expected {radius:.2f} mm."))
    return summarize("corners", problems), set(corners)


def surface_check(measured: object, used_concave: set, used_convex: set) -> Check:
    problems = [("unexpected_surface", f"Unexpected surface: {kind}.") for kind in measured.unexpected]
    for boss in measured.bosses:
        problems.append(
            (
                "unexpected_boss",
                f"Unexpected cylindrical boss of diameter {boss.diameter:.2f} mm at {point(boss.x, boss.y)}.",
            )
        )
    for arc in measured.concave_arcs:
        if arc not in used_concave:
            problems.append(("unexpected_rounding", f"Unexpected inner rounding of radius {arc.radius:.2f} mm."))
    for arc in measured.convex_arcs:
        if arc not in used_convex:
            problems.append(("unexpected_rounding", f"Unexpected outer rounding of radius {arc.radius:.2f} mm."))
    return summarize("surfaces", problems)


def diagnose(spec: Spec, measured: object) -> tuple:
    holes = hole_check(spec, measured)
    pockets, used_concave = pocket_check(spec, measured)
    corners, used_convex = corner_check(spec, measured)
    return holes, pockets, corners, surface_check(measured, used_concave, used_convex)


def body_check(spec: Spec, shape: object) -> Check:
    body = spec.body
    expected = (body.length, body.width, body.height)
    size = box_of(shape).size
    data = (("size", [round(value, 6) for value in size]),)
    if all(close(have, want) for have, want in zip(size, expected)):
        return Check("body", PASS, data=data)
    shown = " x ".join(f"{value:.2f}" for value in size)
    wanted = " x ".join(f"{value:.2f}" for value in expected)
    message = f"Body is {shown} mm along X, Y, Z; expected {wanted} mm."
    if all(close(have, want) for have, want in zip(sorted(size), sorted(expected))):
        return Check("body", FAIL, "axes_swapped", message + " The axes look swapped.", (message,), data)
    if all(close(have, want) for have, want in zip(oriented_size(shape), sorted(expected))):
        hint = message + " The part looks rotated; build it aligned with the X, Y and Z axes."
        return Check("body", FAIL, "rotated", hint, (hint,), data)
    return Check("body", FAIL, "body_size", message, (message,), data)


def difference_check(reference: object, shape: object) -> Check:
    comparison = compare(reference, shape)
    if not comparison.reliable:
        message = f"The geometric comparison is not reliable: {comparison.reason}."
        return Check("symmetric_difference", UNVERIFIED, "boolean_unreliable", message)
    data = (
        ("difference", comparison.difference),
        ("residual", comparison.residual),
        ("missing", comparison.missing),
        ("missing_center", list(comparison.missing_center) if comparison.missing_center else None),
        ("extra", comparison.extra),
        ("extra_center", list(comparison.extra_center) if comparison.extra_center else None),
    )
    if comparison.difference <= DIFFERENCE_LIMIT:
        return Check("symmetric_difference", PASS, data=data)
    problems = [f"The part differs from the specification by {comparison.difference:.3f} mm3."]
    if comparison.missing_center:
        x, y, z = comparison.missing_center
        problems.append(f"Missing material: {comparison.missing:.3f} mm3 around ({x:.2f}, {y:.2f}, {z:.2f}).")
    if comparison.extra_center:
        x, y, z = comparison.extra_center
        problems.append(f"Extra material: {comparison.extra:.3f} mm3 around ({x:.2f}, {y:.2f}, {z:.2f}).")
    return Check("symmetric_difference", FAIL, "geometry_mismatch", problems[0], tuple(problems), data)


def combine(checks: list) -> tuple:
    difference = next(item for item in checks if item.name == "symmetric_difference")
    failures = [item for item in checks if item.status == FAIL]
    if difference.status == PASS:
        if failures:
            return UNVERIFIED, "internal_inconsistency"
        return VERIFIED, ""
    if failures:
        return FAIL, failures[0].code
    return UNVERIFIED, difference.code


def verify(spec: Spec, shape: object, diagnose: object = diagnose) -> Report:
    started = time.perf_counter()

    def finish(status: str, code: str, checks: list, translation: tuple | None = None) -> Report:
        return Report(status, code, tuple(checks), translation, round(time.perf_counter() - started, 4))

    issues = check_spec(spec)
    if any(issue.severity == "unsupported" for issue in issues):
        message = "; ".join(issue.message for issue in issues if issue.severity == "unsupported")
        return finish(
            UNSUPPORTED, "unsupported_spec", [Check("specification", UNVERIFIED, "unsupported_spec", message)]
        )
    try:
        reference = build_reference(spec)
    except BuildError as error:
        return finish(
            UNVERIFIED, "reference_unavailable", [Check("reference", UNVERIFIED, "reference_unavailable", str(error))]
        )
    if shape is None or shape.wrapped.IsNull():
        message = "The program produced an empty shape."
        return finish(UNVERIFIED, "empty_shape", [Check("validity", UNVERIFIED, "empty_shape", message)])
    solids = shape.Solids()
    if len(solids) != 1:
        message = f"The part must be exactly one solid; found {len(solids)}."
        return finish(FAIL, "solid_count", [Check("validity", FAIL, "solid_count", message, (message,))])
    solid = solids[0]
    if not shape.isValid() or not solid.isValid():
        message = "The solid is not valid (for example it is open or intersects itself)."
        return finish(FAIL, "invalid_solid", [Check("validity", FAIL, "invalid_solid", message, (message,))])

    box = box_of(solid)
    translation = (-(box.xmin + box.xmax) / 2, -(box.ymin + box.ymax) / 2, -box.zmin)
    moved = solid.translate(cq.Vector(*translation))
    checks = [Check("validity", PASS), body_check(spec, moved), difference_check(reference, moved)]
    try:
        checks.extend(diagnose(spec, measure(moved)))
    except Exception as error:
        checks.append(Check("diagnostics", UNVERIFIED, "diagnostics_failed", f"{type(error).__name__}: {error}"[:300]))
    status, code = combine(checks)
    return finish(status, code, checks, translation)
