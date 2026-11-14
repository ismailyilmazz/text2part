import json
import math
import random
from pathlib import Path

import cadquery as cq
import pytest

from text2part.core.builder import BuildError, build_reference
from text2part.core.spec import MIN_WALL, Spec, check_proposal, expand_holes, nominal_volume

GOLDEN = Path(__file__).parent / "golden" / "specs"
CASES = {path.stem: json.loads(path.read_text(encoding="utf-8")) for path in sorted(GOLDEN.glob("*.json"))}
POSITIVE = sorted(name for name in CASES if name.startswith("P"))
DELTA = 0.005
DIAGONAL = math.sqrt(0.5)

HAND_VOLUMES = {
    "P1": 60 * 40 * 5 - 4 * math.pi * 3**2 * 5,
    "P2": 100 * 50 * 3,
    "P3": (80 * 80 - (4 - math.pi) * 5**2) * 8 - 6 * math.pi * 2.5**2 * 8,
    "P4": 50 * 30 * 20 - math.pi * 4**2 * 10,
    "P5": 60 * 40 * 15 - (30 * 20 - (4 - math.pi) * 3**2) * 5,
    "P6": 60 * 40 * 15 - (30 * 20 - (4 - math.pi) * 3**2) * 5 - 4 * math.pi * 2.5**2 * 15,
    "P7": 120 * 20 * 4 - 5 * math.pi * 2**2 * 4,
    "P8": 60 * 40 * 5 - math.pi * 10**2 * 5,
}


def make(body, family="block", **extra):
    length, width, height = body
    data = {
        "schema_version": "1.0",
        "units": "mm",
        "family": family,
        "body": {"length": length, "width": width, "height": height},
        **extra,
    }
    report = check_proposal({"spec": data})
    assert report.status == "VALID", [issue.message for issue in report.issues]
    return report.spec


def hole(diameter, depth, *centers):
    return {
        "diameter": diameter,
        "depth": depth,
        "placement": {"kind": "explicit", "centers": [list(c) for c in centers]},
    }


def material(solid, x, y, z):
    return solid.isInside(cq.Vector(x, y, z), 1e-7)


def assert_matches_spec(solid, spec: Spec):
    body = spec.body
    half_x, half_y, height = body.length / 2, body.width / 2, body.height
    box = solid.BoundingBox()
    measured = (box.xmin, box.xmax, box.ymin, box.ymax, box.zmin, box.zmax)
    expected = (-half_x, half_x, -half_y, half_y, 0.0, height)
    assert all(math.isclose(a, b, abs_tol=1e-6) for a, b in zip(measured, expected)), measured

    for sign_x, sign_y in ((1, 0), (-1, 0), (0, 1), (0, -1)):
        x, y = sign_x * (half_x - 2 * DELTA), sign_y * (half_y - 2 * DELTA)
        assert material(solid, x, y, DELTA) and material(solid, x, y, height - DELTA)
        assert not material(solid, x, y, -DELTA) and not material(solid, x, y, height + DELTA)
        assert not material(solid, sign_x * (half_x + DELTA), sign_y * (half_y + DELTA), height / 2)

    radius = spec.corner_radius
    for sign_x in (-1, 1):
        for sign_y in (-1, 1):
            if radius > 0:
                cx, cy = sign_x * (half_x - radius), sign_y * (half_y - radius)
                inner, outer = (radius - DELTA) * DIAGONAL, (radius + DELTA) * DIAGONAL
                assert material(solid, cx + sign_x * inner, cy + sign_y * inner, height / 2)
                assert not material(solid, cx + sign_x * outer, cy + sign_y * outer, height / 2)
            else:
                assert material(solid, sign_x * (half_x - DELTA), sign_y * (half_y - DELTA), height / 2)

    for item in expand_holes(spec):
        r = item.diameter / 2
        depth = height if item.depth is None else item.depth
        middle = height - depth / 2
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            assert not material(solid, item.x + dx * (r - DELTA), item.y + dy * (r - DELTA), middle)
            assert material(solid, item.x + dx * (r + DELTA), item.y + dy * (r + DELTA), middle)
        assert not material(solid, item.x, item.y, height - DELTA)
        assert not material(solid, item.x, item.y, height - depth + DELTA)
        if item.depth is not None:
            assert material(solid, item.x, item.y, height - depth - DELTA)

    for pocket in spec.pockets:
        cx, cy = pocket.center
        px, py = pocket.length / 2, pocket.width / 2
        middle = height - pocket.depth / 2
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            assert not material(solid, cx + dx * (px - DELTA), cy + dy * (py - DELTA), middle)
            assert material(solid, cx + dx * (px + DELTA), cy + dy * (py + DELTA), middle)
        assert not material(solid, cx, cy, height - pocket.depth + DELTA)
        assert material(solid, cx, cy, height - pocket.depth - DELTA)
        r = pocket.corner_radius
        for sign_x in (-1, 1):
            for sign_y in (-1, 1):
                if r > 0:
                    ax, ay = cx + sign_x * (px - r), cy + sign_y * (py - r)
                    inner, outer = (r - DELTA) * DIAGONAL, (r + DELTA) * DIAGONAL
                    assert not material(solid, ax + sign_x * inner, ay + sign_y * inner, middle)
                    assert material(solid, ax + sign_x * outer, ay + sign_y * outer, middle)
                else:
                    assert not material(solid, cx + sign_x * (px - DELTA), cy + sign_y * (py - DELTA), middle)
                    assert material(solid, cx + sign_x * (px + DELTA), cy + sign_y * (py + DELTA), middle)


@pytest.mark.parametrize("name", POSITIVE)
def test_golden_reference_geometry(name):
    spec = check_proposal(CASES[name]["proposal"]).spec
    solid = build_reference(spec)
    assert solid.isValid()
    assert len(solid.Solids()) == 1
    assert math.isclose(solid.Volume(), HAND_VOLUMES[name], rel_tol=1e-9)
    assert_matches_spec(solid, spec)


def test_reference_is_deterministic():
    spec = check_proposal(CASES["P6"]["proposal"]).spec
    first, second = build_reference(spec), build_reference(spec)
    assert first.Volume() == second.Volume()
    assert len(first.Faces()) == len(second.Faces())


@pytest.mark.parametrize("name", ["N1", "N2", "N3", "N4", "N7", "N8", "N13", "N17"])
def test_builder_refuses_specs_that_are_not_valid(name):
    spec = Spec.model_validate(CASES[name]["proposal"]["spec"])
    with pytest.raises(BuildError):
        build_reference(spec)


BOUNDARY = {
    "smallest body": make((0.5, 0.5, 0.5)),
    "largest body with the largest hole": make((2000, 2000, 2000), holes=[hole(500, "through", (0, 0))]),
    "two hundred holes": make(
        (1000, 500, 10),
        holes=[
            {
                "diameter": 8,
                "depth": "through",
                "placement": {"kind": "grid", "nx": 20, "ny": 10, "pitch_x": 45, "pitch_y": 45},
            }
        ],
    ),
    "twenty pockets": make(
        (1000, 100, 20),
        pockets=[
            {"center": [-475 + 50 * i, 0], "length": 30, "width": 60, "depth": 10, "corner_radius": 5}
            for i in range(20)
        ],
    ),
    "largest body corner radius": make((60, 40, 5), corner_radius=19.95),
    "smallest body corner radius": make((60, 40, 5), corner_radius=0.1),
    "largest pocket corner radius": make(
        (60, 40, 15), pockets=[{"center": [0, 0], "length": 30, "width": 20, "depth": 5, "corner_radius": 9.95}]
    ),
    "hole at minimum wall from the edge": make((60, 40, 5), holes=[hole(6, "through", (30 - 3 - MIN_WALL, 0))]),
    "holes at minimum wall from each other": make((60, 40, 5), holes=[hole(6, "through", (0, 0), (6 + MIN_WALL, 0))]),
    "blind hole with minimum floor": make((60, 40, 5), holes=[hole(6, 4.5, (0, 0))]),
    "pocket at minimum wall and minimum floor": make(
        (60, 40, 15), pockets=[{"center": [0, 0], "length": 59, "width": 39, "depth": 14.5}]
    ),
    "hole at minimum wall from a pocket": make(
        (60, 40, 15),
        pockets=[{"center": [0, 0], "length": 30, "width": 20, "depth": 5, "corner_radius": 3}],
        holes=[hole(5, "through", (15 + 2.5 + MIN_WALL, 0))],
    ),
    "pockets at minimum wall from each other": make(
        (60, 40, 15),
        pockets=[
            {"center": [-10.25, 0], "length": 20, "width": 10, "depth": 5},
            {"center": [10.25, 0], "length": 20, "width": 10, "depth": 8, "corner_radius": 2},
        ],
    ),
    "hole next to a rounded body corner": make((60, 40, 5), corner_radius=15, holes=[hole(4, "through", (20, 10))]),
    "mixed blind and through holes of one diameter": make(
        (60, 40, 10), holes=[hole(5, "through", (-15, 0)), hole(5, 4, (15, 0)), hole(5, 7, (0, 10))]
    ),
    "smallest verifiable hole": make((60, 40, 5), holes=[hole(0.5, 0.26, (0, 0))]),
}


@pytest.mark.parametrize("label", sorted(BOUNDARY))
def test_boundary_specs_build_and_match(label):
    spec = BOUNDARY[label]
    solid = build_reference(spec)
    assert math.isclose(solid.Volume(), nominal_volume(spec), rel_tol=1e-9, abs_tol=1e-6)
    assert_matches_spec(solid, spec)


def random_spec(rng: random.Random) -> Spec:
    length, width, height = round(rng.uniform(8, 300), 1), round(rng.uniform(8, 200), 1), round(rng.uniform(2, 60), 1)
    data = {
        "schema_version": "1.0",
        "units": "mm",
        "family": rng.choice(["plate", "block"]),
        "body": {"length": length, "width": width, "height": height},
        "corner_radius": rng.choice([0, 0, round(rng.uniform(0.5, min(length, width) / 4), 1)]),
        "holes": [],
        "pockets": [],
    }

    def keep(kind, item):
        data[kind].append(item)
        if check_proposal({"spec": data}).status != "VALID":
            data[kind].pop()

    for _ in range(rng.randint(0, 3)):
        side_x, side_y = round(rng.uniform(2, length * 0.5), 1), round(rng.uniform(2, width * 0.5), 1)
        keep(
            "pockets",
            {
                "center": [
                    round(rng.uniform(-length / 2, length / 2), 1),
                    round(rng.uniform(-width / 2, width / 2), 1),
                ],
                "length": side_x,
                "width": side_y,
                "depth": round(rng.uniform(0.3, height - MIN_WALL), 1),
                "corner_radius": rng.choice([0, round(rng.uniform(0.2, min(side_x, side_y) / 3), 1)]),
            },
        )
    for _ in range(rng.randint(0, 6)):
        diameter = round(rng.uniform(1, min(length, width) / 3), 1)
        depth = rng.choice(["through", round(rng.uniform(0.3, height - MIN_WALL), 1)])
        kind = rng.choice(["explicit", "explicit", "corners", "grid"])
        if kind == "explicit":
            placement = {
                "kind": "explicit",
                "centers": [
                    [round(rng.uniform(-length / 2, length / 2), 1), round(rng.uniform(-width / 2, width / 2), 1)]
                ],
            }
        elif kind == "corners":
            placement = {"kind": "corners", "inset": round(rng.uniform(diameter, min(length, width) / 3), 1)}
        else:
            nx, ny = rng.randint(1, 4), rng.randint(1, 3)
            placement = {
                "kind": "grid",
                "nx": nx,
                "ny": ny,
                "pitch_x": round(length / (nx + 1), 1),
                "pitch_y": round(width / (ny + 1), 1),
            }
        keep("holes", {"diameter": diameter, "depth": depth, "placement": placement})
    return check_proposal({"spec": data}).spec


def test_random_valid_specs_build_and_match():
    rng = random.Random(20261114)
    specs = [random_spec(rng) for _ in range(60)]
    assert sum(1 for spec in specs if spec.pockets) >= 10
    assert sum(1 for spec in specs if spec.corner_radius > 0) >= 10
    assert sum(1 for spec in specs if any(item.depth is not None for item in expand_holes(spec))) >= 10
    assert sum(1 for spec in specs if any(item.depth is None for item in expand_holes(spec))) >= 10
    for spec in specs:
        solid = build_reference(spec)
        assert math.isclose(solid.Volume(), nominal_volume(spec), rel_tol=1e-9, abs_tol=1e-6)
        assert_matches_spec(solid, spec)


def sensitivity_spec(**changes):
    values = {
        "body": (60, 40, 15),
        "body_radius": 5,
        "pocket_center": [0, 0],
        "pocket_length": 30,
        "pocket_depth": 5,
        "pocket_radius": 3,
        "through_center": (24, 13),
        "through_diameter": 5,
        "through_depth": "through",
        "blind_depth": 6,
        **changes,
    }
    pocket = {
        "center": values["pocket_center"],
        "length": values["pocket_length"],
        "width": 20,
        "depth": values["pocket_depth"],
        "corner_radius": values["pocket_radius"],
    }
    holes = [
        hole(values["through_diameter"], values["through_depth"], values["through_center"]),
        hole(4, values["blind_depth"], (-24, -13)),
    ]
    return make(values["body"], corner_radius=values["body_radius"], pockets=[pocket], holes=holes)


DEVIATIONS = {
    "hole moved by 0.02": {"through_center": (24.02, 13)},
    "hole radius larger by 0.02": {"through_diameter": 5.04},
    "blind hole deeper by 0.02": {"blind_depth": 6.02},
    "through hole made blind": {"through_depth": 14},
    "pocket moved by 0.02": {"pocket_center": [0.02, 0]},
    "pocket longer by 0.04": {"pocket_length": 30.04},
    "pocket deeper by 0.02": {"pocket_depth": 5.02},
    "pocket corner radius larger by 0.1": {"pocket_radius": 3.1},
    "body corner radius larger by 0.1": {"body_radius": 5.1},
    "body longer by 0.04": {"body": (60.04, 40, 15)},
    "body taller by 0.02": {"body": (60, 40, 15.02)},
}


def test_probes_accept_the_unchanged_part():
    spec = sensitivity_spec()
    assert_matches_spec(build_reference(spec), spec)


@pytest.mark.parametrize("label", sorted(DEVIATIONS))
def test_probes_detect_small_deviations(label):
    deviated = build_reference(sensitivity_spec(**DEVIATIONS[label]))
    with pytest.raises(AssertionError):
        assert_matches_spec(deviated, sensitivity_spec())


def test_volume_detects_an_extra_or_missing_feature():
    spec = sensitivity_spec()
    report = check_proposal({"spec": {**spec.model_dump(), "holes": [spec.model_dump()["holes"][0]]}})
    assert report.status == "VALID"
    assert abs(build_reference(report.spec).Volume() - nominal_volume(spec)) > 1.0
