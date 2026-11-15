from dataclasses import dataclass

import cadquery as cq

from text2part.core.spec import Spec, check_spec, expand_holes, nominal_volume

OVERSHOOT = 1.0
VOLUME_RELATIVE_TOLERANCE = 1e-9
VOLUME_ABSOLUTE_TOLERANCE = 1e-6


class BuildError(Exception):
    pass


@dataclass(frozen=True)
class HoleTool:
    bottom: float
    centers: tuple
    radius: float
    length: float


@dataclass(frozen=True)
class PocketTool:
    bottom: float
    center: tuple
    length: float
    width: float
    depth: float
    corner_radius: float


def require_valid(spec: Spec) -> None:
    issues = check_spec(spec)
    if issues:
        raise BuildError("specification is not valid: " + "; ".join(f"{issue.code} {issue.path}" for issue in issues))


def hole_tools(spec: Spec) -> tuple:
    height = spec.body.height
    groups: dict[tuple[float, float | None], list[tuple[float, float]]] = {}
    for hole in expand_holes(spec):
        groups.setdefault((hole.diameter, hole.depth), []).append((hole.x, hole.y))
    tools = []
    for (diameter, depth), centers in groups.items():
        bottom = -OVERSHOOT if depth is None else height - depth
        tools.append(HoleTool(bottom, tuple(centers), diameter / 2, height + OVERSHOOT - bottom))
    return tuple(tools)


def pocket_tools(spec: Spec) -> tuple:
    return tuple(
        PocketTool(
            spec.body.height - pocket.depth,
            pocket.center,
            pocket.length,
            pocket.width,
            pocket.depth + OVERSHOOT,
            pocket.corner_radius,
        )
        for pocket in spec.pockets
    )


def build_reference(spec: Spec) -> cq.Solid:
    require_valid(spec)
    body = spec.body
    part = cq.Workplane("XY").box(body.length, body.width, body.height, centered=(True, True, False))
    if spec.corner_radius > 0:
        part = part.edges("|Z").fillet(spec.corner_radius)

    for hole in hole_tools(spec):
        tool = (
            cq.Workplane("XY")
            .workplane(offset=hole.bottom)
            .pushPoints(hole.centers)
            .circle(hole.radius)
            .extrude(hole.length)
        )
        part = part.cut(tool)

    for pocket in pocket_tools(spec):
        tool = (
            cq.Workplane("XY")
            .workplane(offset=pocket.bottom)
            .center(*pocket.center)
            .rect(pocket.length, pocket.width)
            .extrude(pocket.depth)
        )
        if pocket.corner_radius > 0:
            tool = tool.edges("|Z").fillet(pocket.corner_radius)
        part = part.cut(tool)

    shape = part.findSolid()
    solids = shape.Solids()
    if len(solids) != 1 or not solids[0].isValid():
        raise BuildError(f"reference is not a single valid solid: {len(solids)} solids")
    solid = solids[0]
    expected = nominal_volume(spec)
    if abs(solid.Volume() - expected) > VOLUME_ABSOLUTE_TOLERANCE + VOLUME_RELATIVE_TOLERANCE * expected:
        raise BuildError(f"reference volume {solid.Volume():.9g} differs from nominal volume {expected:.9g}")
    return solid


def literal(value: float) -> str:
    return repr(float(value))


def rcq_source(spec: Spec) -> str:
    require_valid(spec)
    body = spec.body
    size = ", ".join(literal(item) for item in (body.length, body.width, body.height))
    lines = ["import cadquery as cq", f'result = cq.Workplane("XY").box({size}, centered=(True, True, False))']
    if spec.corner_radius > 0:
        lines.append(f'result = result.edges("|Z").fillet({literal(spec.corner_radius)})')

    for hole in hole_tools(spec):
        points = ", ".join(f"({literal(x)}, {literal(y)})" for x, y in hole.centers)
        lines.append(
            f'result = result.cut(cq.Workplane("XY").workplane(offset={literal(hole.bottom)})'
            f".pushPoints([{points}]).circle({literal(hole.radius)}).extrude({literal(hole.length)}))"
        )

    for pocket in pocket_tools(spec):
        tool = (
            f'cq.Workplane("XY").workplane(offset={literal(pocket.bottom)})'
            f".center({literal(pocket.center[0])}, {literal(pocket.center[1])})"
            f".rect({literal(pocket.length)}, {literal(pocket.width)}).extrude({literal(pocket.depth)})"
        )
        if pocket.corner_radius > 0:
            tool += f'.edges("|Z").fillet({literal(pocket.corner_radius)})'
        lines.append(f"result = result.cut({tool})")

    return "\n".join(lines) + "\n"
