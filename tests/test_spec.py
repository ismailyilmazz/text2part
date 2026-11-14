import json
import math
from pathlib import Path

import pytest

from text2part.core.spec import (
    MAX_ISSUES,
    MIN_WALL,
    Spec,
    check_proposal,
    check_spec,
    expand_holes,
    nominal_volume,
    rounded_rect_distance,
)

GOLDEN = Path(__file__).parent / "golden" / "specs"
CASES = {path.stem: json.loads(path.read_text(encoding="utf-8")) for path in sorted(GOLDEN.glob("*.json"))}
POSITIVE = sorted(name for name in CASES if name.startswith("P"))
NEGATIVE = sorted(name for name in CASES if name.startswith("N"))

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


def plate(**extra):
    return {
        "schema_version": "1.0",
        "units": "mm",
        "family": "plate",
        "body": {"length": 60, "width": 40, "height": 5},
        **extra,
    }


def through(diameter, *centers):
    return {
        "diameter": diameter,
        "depth": "through",
        "placement": {"kind": "explicit", "centers": [list(c) for c in centers]},
    }


def codes(report):
    return sorted({issue.code for issue in report.issues})


def test_golden_set_is_complete():
    assert len(POSITIVE) == 8
    assert len(NEGATIVE) >= 12
    assert set(HAND_VOLUMES) == set(POSITIVE)


@pytest.mark.parametrize("name", POSITIVE + NEGATIVE)
def test_golden_status_and_codes(name):
    case = CASES[name]
    report = check_proposal(case["proposal"])
    assert report.status == case["expected"]["status"], [issue.message for issue in report.issues]
    assert codes(report) == sorted(case["expected"]["codes"])


@pytest.mark.parametrize("name", POSITIVE)
def test_golden_hole_centers(name):
    case = CASES[name]
    report = check_proposal(case["proposal"])
    measured = sorted((round(hole.x, 9), round(hole.y, 9)) for hole in report.holes)
    assert measured == sorted((float(x), float(y)) for x, y in case["expected"]["hole_centers"])


@pytest.mark.parametrize("name", POSITIVE)
def test_nominal_volume_matches_hand_formula(name):
    report = check_proposal(CASES[name]["proposal"])
    assert math.isclose(nominal_volume(report.spec), HAND_VOLUMES[name], rel_tol=1e-12)


def test_p1_volume_is_the_known_reference_value():
    assert round(HAND_VOLUMES["P1"], 4) == 11434.5133


@pytest.mark.parametrize(
    "body",
    [
        {"length": True, "width": 40, "height": 5},
        {"length": "60", "width": 40, "height": 5},
        {"length": float("nan"), "width": 40, "height": 5},
        {"length": float("inf"), "width": 40, "height": 5},
        {"length": 0.4, "width": 40, "height": 5},
        {"length": 60, "width": 40},
        {"length": 60, "width": 40, "height": 5, "depth": 1},
    ],
)
def test_schema_rejects_bad_numbers_and_fields(body):
    report = check_proposal({"spec": plate(body=body)})
    assert report.status == "INVALID"
    assert codes(report) == ["SCHEMA"]


@pytest.mark.parametrize(
    "change",
    [
        {"schema_version": "2.0"},
        {"family": "flange"},
        {"holes": [{"diameter": 6, "depth": "blind", "placement": {"kind": "explicit", "centers": [[0, 0]]}}]},
        {"holes": [{"diameter": 6, "depth": "through", "placement": {"kind": "circle", "count": 4}}]},
        {"holes": [{"diameter": 6, "depth": "through", "placement": {"kind": "explicit", "centers": []}}]},
        {"holes": [{"diameter": 6, "depth": "through", "placement": {"kind": "explicit", "centers": [[0, 0, 0]]}}]},
        {"holes": [{"diameter": 6, "depth": "through", "placement": {"kind": "grid", "nx": 2.5, "ny": 1}}]},
        {"pockets": [{"center": [0, 0], "length": 10, "width": 10, "depth": 2, "taper": 3}]},
    ],
)
def test_schema_rejects_unknown_shapes(change):
    report = check_proposal({"spec": plate(**change)})
    assert report.status == "INVALID"
    assert codes(report) == ["SCHEMA"]


def test_non_mapping_input_is_invalid_not_an_exception():
    for value in (None, [], "plate", 42):
        assert check_proposal(value).status == "INVALID"


def test_integer_and_float_inputs_are_equivalent():
    as_int = check_proposal({"spec": plate(holes=[through(6, (0, 0))])})
    as_float = check_proposal(
        {"spec": plate(body={"length": 60.0, "width": 40.0, "height": 5.0}, holes=[through(6.0, (0.0, 0.0))])}
    )
    assert as_int.status == as_float.status == "VALID"
    assert as_int.spec == as_float.spec


def test_exact_minimum_wall_is_accepted_and_slightly_less_is_rejected():
    edge = 30 - 3 - MIN_WALL
    assert check_proposal({"spec": plate(holes=[through(6, (edge, 0))])}).status == "VALID"
    assert codes(check_proposal({"spec": plate(holes=[through(6, (edge + 0.001, 0))])})) == ["R1"]
    assert check_proposal({"spec": plate(holes=[through(6, (0, 0), (6 + MIN_WALL, 0))])}).status == "VALID"
    assert codes(check_proposal({"spec": plate(holes=[through(6, (0, 0), (6 + MIN_WALL - 0.001, 0))])})) == ["R2"]


def test_blind_hole_depth_rules():
    def blind(depth):
        hole = {"diameter": 6, "depth": depth, "placement": {"kind": "explicit", "centers": [[0, 0]]}}
        return check_proposal({"spec": plate(holes=[hole])})

    assert blind(4.5).status == "VALID"
    assert codes(blind(4.6)) == ["R3"]
    assert codes(blind(5)) == ["R3"]
    assert codes(blind(0.05)) == ["R3"]


def test_hole_in_rounded_corner_region_is_rejected():
    inside = check_proposal({"spec": plate(corner_radius=15, holes=[through(4, (20, 10))])})
    outside = check_proposal({"spec": plate(corner_radius=15, holes=[through(4, (24, 14))])})
    assert inside.status == "VALID"
    assert codes(outside) == ["R1"]
    assert check_proposal({"spec": plate(holes=[through(4, (24, 14))])}).status == "VALID"


@pytest.mark.parametrize(
    "radius, expected",
    [(0, "VALID"), (0.05, "INVALID"), (0.1, "VALID"), (19.95, "VALID"), (19.96, "INVALID"), (20, "INVALID")],
)
def test_body_corner_radius_limits(radius, expected):
    report = check_proposal({"spec": plate(corner_radius=radius)})
    assert report.status == expected
    assert codes(report) == ([] if expected == "VALID" else ["R6"])


@pytest.mark.parametrize("radius, expected", [(0, "VALID"), (0.05, "INVALID"), (4.95, "VALID"), (5, "INVALID")])
def test_pocket_corner_radius_limits(radius, expected):
    pocket = {"center": [0, 0], "length": 20, "width": 10, "depth": 2, "corner_radius": radius}
    report = check_proposal({"spec": plate(pockets=[pocket])})
    assert report.status == expected
    assert codes(report) == ([] if expected == "VALID" else ["R5"])


def test_pocket_depth_rules():
    def pocket(depth):
        return check_proposal({"spec": plate(pockets=[{"center": [0, 0], "length": 20, "width": 10, "depth": depth}])})

    assert pocket(4.5).status == "VALID"
    assert codes(pocket(4.6)) == ["R4"]
    assert codes(pocket(0.05)) == ["R4"]
    assert pocket(5).status == "UNSUPPORTED"
    assert pocket(7).status == "UNSUPPORTED"


def test_feature_too_small_to_verify():
    hole = {"diameter": 0.5, "depth": 0.2, "placement": {"kind": "explicit", "centers": [[0, 0]]}}
    assert codes(check_proposal({"spec": plate(holes=[hole])})) == ["R8"]


def test_status_precedence():
    question = {"id": "q1", "text": "Which one?"}
    declared = {"feature": "thread"}
    bad_hole = through(6, (29, 0))
    assert check_proposal({"spec": plate(), "questions": [question], "unsupported": [declared]}).status == "UNSUPPORTED"
    assert check_proposal({"questions": [question]}).status == "NEEDS_ANSWER"
    assert check_proposal({"spec": plate(holes=[bad_hole]), "questions": [question]}).status == "NEEDS_ANSWER"
    assert check_proposal({"unsupported": [declared]}).status == "UNSUPPORTED"
    assert check_proposal({"spec": plate(holes=[bad_hole])}).status == "INVALID"


def test_issue_list_is_capped():
    centers = [[0.01 * index, 0] for index in range(120)]
    report = check_proposal({"spec": plate(holes=[through(6, *centers)])})
    assert report.status == "INVALID"
    assert len(report.issues) == MAX_ISSUES


def test_status_does_not_depend_on_the_issue_cap():
    centers = [[0.01 * index, 0] for index in range(120)]
    pocket = {"center": [0, 12], "length": 10, "width": 6, "depth": 5}
    report = check_proposal({"spec": plate(holes=[through(6, *centers)], pockets=[pocket])})
    assert report.status == "UNSUPPORTED"
    assert len(report.issues) == MAX_ISSUES
    assert report.issues[0].code == "U_THROUGH_POCKET"


def test_hole_count_limit_is_checked_before_expansion():
    grid = {"kind": "grid", "nx": 200, "ny": 200, "pitch_x": 1, "pitch_y": 1}
    report = check_proposal({"spec": plate(holes=[{"diameter": 0.5, "depth": "through", "placement": grid}] * 50)})
    assert codes(report) == ["R9"]
    assert report.holes == ()


def test_expansion_order_is_deterministic():
    spec = Spec.model_validate(CASES["P3"]["proposal"]["spec"])
    assert expand_holes(spec) == expand_holes(spec)
    assert check_spec(spec) == ()


@pytest.mark.parametrize(
    "nx, ny, pitch_x, pitch_y, center",
    [
        (1, 1, 7, 9, (3, -2)),
        (2, 1, 10, 4, (0, 0)),
        (3, 2, 20, 20, (0, 0)),
        (4, 5, 6.5, 3.25, (-8, 4.5)),
        (10, 20, 2, 1.5, (1, 1)),
    ],
)
def test_grid_expansion_properties(nx, ny, pitch_x, pitch_y, center):
    grid = {"kind": "grid", "nx": nx, "ny": ny, "pitch_x": pitch_x, "pitch_y": pitch_y, "center": list(center)}
    body = {"length": 400, "width": 400, "height": 5}
    spec = Spec.model_validate(plate(body=body, holes=[{"diameter": 1, "depth": "through", "placement": grid}]))
    points = [(hole.x, hole.y) for hole in expand_holes(spec)]
    xs, ys = sorted({x for x, _ in points}), sorted({y for _, y in points})
    assert len(points) == len(set(points)) == nx * ny
    assert (len(xs), len(ys)) == (nx, ny)
    assert math.isclose(sum(x for x, _ in points) / len(points), center[0], abs_tol=1e-9)
    assert math.isclose(sum(y for _, y in points) / len(points), center[1], abs_tol=1e-9)
    assert all(math.isclose(b - a, pitch_x, abs_tol=1e-9) for a, b in zip(xs, xs[1:]))
    assert all(math.isclose(b - a, pitch_y, abs_tol=1e-9) for a, b in zip(ys, ys[1:]))


def test_corner_expansion_properties():
    holes = [{"diameter": 4, "depth": "through", "placement": {"kind": "corners", "inset": 7.5}}]
    points = {(hole.x, hole.y) for hole in expand_holes(Spec.model_validate(plate(holes=holes)))}
    assert points == {(22.5, 12.5), (-22.5, 12.5), (22.5, -12.5), (-22.5, -12.5)}


def test_models_are_immutable():
    spec = Spec.model_validate(plate())
    with pytest.raises(Exception):
        spec.corner_radius = 3


@pytest.mark.parametrize(
    "point, radius, expected",
    [
        ((0, 0), 0, -20),
        ((30, 0), 0, 0),
        ((35, 0), 0, 5),
        ((33, 24), 0, 5),
        ((30, 20), 5, 5 * (math.sqrt(2) - 1)),
        ((25, 15), 5, -5),
    ],
)
def test_rounded_rect_distance(point, radius, expected):
    assert math.isclose(rounded_rect_distance(*point, 30, 20, radius), expected, abs_tol=1e-12)
