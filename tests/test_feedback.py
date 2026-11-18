import cadquery as cq
import pytest

from text2part.core.export import ExportError, write_step
from text2part.core.feedback import MAX_CHARACTERS, MAX_LINES, error_message, repair_message
from text2part.core.status import FAIL, UNSUPPORTED, UNVERIFIED, VERIFIED


def report(status, code="", checks=()):
    return {"status": status, "code": code, "checks": list(checks)}


def check(name, status, problems=(), message=""):
    return {"name": name, "status": status, "code": "", "message": message, "problems": list(problems), "data": {}}


def test_verified_and_unrepairable_reports_give_no_repair_message():
    assert repair_message(report(VERIFIED)) is None
    assert repair_message(report(UNSUPPORTED, "unsupported_spec")) is None
    for code in ("internal_inconsistency", "reference_unavailable", "boolean_unreliable", "diagnostics_failed"):
        assert repair_message(report(UNVERIFIED, code)) is None


def test_empty_shape_asks_for_one_solid():
    assert "exactly one solid" in repair_message(report(UNVERIFIED, "empty_shape"))


def test_failed_checks_are_listed_in_a_fixed_order():
    checks = [
        check("symmetric_difference", FAIL, ["The part differs from the specification by 1.000 mm3."]),
        check("holes", FAIL, ["Missing hole of diameter 6.00 mm at (24.00, 14.00)."]),
        check("body", "PASS"),
        check("validity", "PASS"),
    ]
    lines = repair_message(report(FAIL, "hole_count", checks)).splitlines()
    assert lines == [
        "The part does not match the specification.",
        "Missing hole of diameter 6.00 mm at (24.00, 14.00).",
        "The part differs from the specification by 1.000 mm3.",
    ]


def test_long_feedback_is_bounded():
    many = [f"Unexpected hole of diameter 1.00 mm at ({index}.00, 0.00)." for index in range(100)]
    message = repair_message(report(FAIL, "unexpected_hole", [check("holes", FAIL, many)]))
    assert len(message.splitlines()) == MAX_LINES + 1
    assert len(message) <= MAX_CHARACTERS
    long = ["x" * 400] * 10
    assert len(repair_message(report(FAIL, "x", [check("holes", FAIL, long)]))) == MAX_CHARACTERS


@pytest.mark.parametrize(
    "error, expected",
    [
        (
            {
                "stage": "type",
                "line": 3,
                "col": 9,
                "code": "bad_argument",
                "message": "box: length: expected a number.",
            },
            "The program was rejected (line 3, column 9): box: length: expected a number.",
        ),
        (
            {"stage": "type", "line": None, "col": None, "code": "no_result", "message": "No result."},
            "The program was rejected: No result.",
        ),
        (
            {"stage": "execute", "line": 4, "col": None, "code": "geometry_error", "message": "fillet failed."},
            "The program failed while building the part (line 4): fillet failed.",
        ),
    ],
)
def test_error_messages(error, expected):
    assert error_message(error) == expected


def test_resource_errors_ask_for_a_simpler_program():
    error = {
        "stage": "execute",
        "line": None,
        "col": None,
        "code": "resource_limit",
        "message": "CPU time limit exceeded.",
    }
    assert error_message(error).splitlines() == [
        "The program used too many resources: CPU time limit exceeded.",
        "Make the program simpler.",
    ]


def test_step_export(tmp_path):
    shape = cq.Workplane("XY").box(10, 20, 5).findSolid()
    path = tmp_path / "part.step"
    size = write_step(shape, path)
    assert size == path.stat().st_size > 0
    assert path.read_bytes().startswith(b"ISO-10303-21;")
    imported = cq.importers.importStep(str(path)).findSolid()
    assert abs(imported.Volume() - 1000) < 1e-6


def test_step_export_refuses_large_files(tmp_path):
    path = tmp_path / "part.step"
    with pytest.raises(ExportError) as caught:
        write_step(cq.Workplane("XY").box(10, 20, 5).findSolid(), path, limit=100)
    assert caught.value.too_large is True
    assert not path.exists()
