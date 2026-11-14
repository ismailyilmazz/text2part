import cadquery as cq

from text2part.core.spec import Spec, check_spec, expand_holes, nominal_volume

OVERSHOOT = 1.0
VOLUME_RELATIVE_TOLERANCE = 1e-9
VOLUME_ABSOLUTE_TOLERANCE = 1e-6


class BuildError(Exception):
    pass


def build_reference(spec: Spec) -> cq.Solid:
    issues = check_spec(spec)
    if issues:
        raise BuildError("specification is not valid: " + "; ".join(f"{issue.code} {issue.path}" for issue in issues))

    body = spec.body
    height = body.height
    part = cq.Workplane("XY").box(body.length, body.width, height, centered=(True, True, False))
    if spec.corner_radius > 0:
        part = part.edges("|Z").fillet(spec.corner_radius)

    groups: dict[tuple[float, float | None], list[tuple[float, float]]] = {}
    for hole in expand_holes(spec):
        groups.setdefault((hole.diameter, hole.depth), []).append((hole.x, hole.y))
    for (diameter, depth), centers in groups.items():
        bottom = -OVERSHOOT if depth is None else height - depth
        tool = (
            cq.Workplane("XY")
            .workplane(offset=bottom)
            .pushPoints(centers)
            .circle(diameter / 2)
            .extrude(height + OVERSHOOT - bottom)
        )
        part = part.cut(tool)

    for pocket in spec.pockets:
        bottom = height - pocket.depth
        tool = (
            cq.Workplane("XY")
            .workplane(offset=bottom)
            .center(*pocket.center)
            .rect(pocket.length, pocket.width)
            .extrude(pocket.depth + OVERSHOOT)
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
