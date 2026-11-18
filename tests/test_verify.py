import json
import math
from pathlib import Path

import cadquery as cq
import pytest
from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeSolid, BRepBuilderAPI_Sewing
from OCP.TopoDS import TopoDS, TopoDS_Shape

from scripts.calibrate_verify import alternate_source
from text2part.core import verify as verify_module
from text2part.core.builder import build_reference, rcq_source
from text2part.core.measure import measure
from text2part.core.spec import Spec, check_proposal
from text2part.core.status import FAIL, PASS, UNSUPPORTED, UNVERIFIED, VERIFIED
from text2part.core.verify import Check, compare, verify
from text2part.rcq.compiler import compile_source
from text2part.rcq.worker import evaluate

ROOT = Path(__file__).parent / "golden"
GEOMETRY = ROOT / "geometry"
PROPOSALS = {
    path.stem: json.loads(path.read_text(encoding="utf-8")) for path in sorted((ROOT / "specs").glob("*.json"))
}
EXTRA_SPECS = json.loads((GEOMETRY / "specs.json").read_text(encoding="utf-8"))
POSITIVE = sorted(name for name in PROPOSALS if name.startswith("P"))

GOLDEN = {
    "GA": ("P1", VERIFIED, {}),
    "GB": ("P1", VERIFIED, {}),
    "GC": ("P1", FAIL, {"symmetric_difference": "geometry_mismatch", "holes": "hole_position"}),
    "GD": ("P1", FAIL, {"symmetric_difference": "geometry_mismatch", "holes": "hole_diameter"}),
    "GE": ("P1", FAIL, {"symmetric_difference": "geometry_mismatch", "holes": "hole_count"}),
    "GF": ("P1", FAIL, {"body": "body_size", "holes": "hole_count", "surfaces": "unexpected_boss"}),
    "GG": ("P1", FAIL, {"body": "axes_swapped"}),
    "GH": ("P1", VERIFIED, {}),
    "GI": ("P1", FAIL, {"body": "rotated"}),
    "GJ": ("P5", VERIFIED, {}),
    "GK": ("P1", FAIL, {"symmetric_difference": "geometry_mismatch", "holes": "coaxial_feature"}),
    "GL": ("P4", VERIFIED, {}),
    "GM": ("P1", FAIL, {"validity": "solid_count"}),
    "GP": ("P1", FAIL, {"validity": "invalid_solid"}),
    "GR": ("P1", FAIL, {"symmetric_difference": "geometry_mismatch"}),
    "GS_builder": ("GS", VERIFIED, {}),
    "GS_alternate": ("GS", VERIFIED, {}),
    "GT": ("GT", VERIFIED, {}),
}


def spec_named(name: str) -> Spec:
    if name in EXTRA_SPECS:
        return Spec.model_validate(EXTRA_SPECS[name])
    return check_proposal(PROPOSALS[name]["proposal"]).spec


def shape_of(name: str) -> object:
    return evaluate(compile_source((GEOMETRY / f"{name}.py").read_text(encoding="utf-8")))


def problems(report) -> list:
    return [problem for check in report.checks for problem in check.problems]


def test_golden_table_matches_the_files():
    assert set(GOLDEN) == {path.stem for path in GEOMETRY.glob("*.py")}


@pytest.mark.parametrize("name", sorted(GOLDEN))
def test_golden_geometry(name):
    spec_name, status, codes = GOLDEN[name]
    report = verify(spec_named(spec_name), shape_of(name))
    assert report.status == status, report.as_dict()
    for check, code in codes.items():
        assert report.check(check).status == FAIL, report.as_dict()
        assert report.check(check).code == code, report.as_dict()
    if status == VERIFIED:
        assert all(check.status == PASS for check in report.checks), report.as_dict()
        assert report.code == ""


def test_builder_output_is_the_first_golden_case():
    assert (GEOMETRY / "GA.py").read_text(encoding="utf-8") == rcq_source(spec_named("P1"))


def test_moved_hole_reports_its_measured_difference_and_location():
    report = verify(spec_named("P1"), shape_of("GC"))
    data = dict(report.check("symmetric_difference").data)
    assert math.isclose(data["difference"], 59.72, abs_tol=0.01)
    assert data["missing_center"][0] > 24 and data["extra_center"][0] < 24
    assert "Hole expected at (24.00, 14.00) is at (25.00, 14.00)." in problems(report)


def test_hundredth_of_a_millimetre_is_caught_by_the_difference_alone():
    report = verify(spec_named("P1"), shape_of("GR"))
    difference = dict(report.check("symmetric_difference").data)["difference"]
    assert 0.5 < difference < 0.7
    assert report.check("holes").status == PASS


def test_translation_is_corrected_and_reported():
    report = verify(spec_named("P1"), shape_of("GH"))
    assert all(math.isclose(a, b, abs_tol=1e-9) for a, b in zip(report.translation, (-5, 0, -2.5)))


def test_large_grid_is_verified_quickly():
    report = verify(spec_named("GT"), shape_of("GT"))
    assert report.status == VERIFIED
    assert dict(report.check("holes").data) == {"expected": 100, "found": 100}
    assert report.seconds < 15


@pytest.mark.parametrize("name", POSITIVE)
@pytest.mark.parametrize("style", ["builder", "alternate"])
def test_every_golden_spec_is_verified_in_two_code_styles(name, style):
    spec = spec_named(name)
    source = rcq_source(spec) if style == "builder" else alternate_source(spec)
    report = verify(spec, evaluate(compile_source(source)))
    assert report.status == VERIFIED, report.as_dict()
    assert all(check.status == PASS for check in report.checks)


def test_pocket_corners_are_not_counted_as_holes():
    measured = measure(build_reference(spec_named("P5")))
    assert measured.holes == ()
    assert len(measured.concave_arcs) == 4
    assert len(measured.floors) == 1


def changed(name: str, **update) -> Spec:
    return spec_named(name).model_copy(update=update)


@pytest.mark.parametrize(
    "name, update, check, code",
    [
        ("P3", {"corner_radius": 0.0}, "corners", "corner_radius"),
        ("P2", {"corner_radius": 4.0}, "corners", "corner_radius"),
        ("P5", {"pockets": ()}, "pockets", "pocket_count"),
        ("P1", {"holes": ()}, "holes", "hole_count"),
    ],
)
def test_diagnostics_point_at_the_wrong_feature(name, update, check, code):
    report = verify(changed(name, **update), evaluate(compile_source(rcq_source(spec_named(name)))))
    assert report.status == FAIL
    assert report.check(check).code == code


@pytest.mark.parametrize(
    "field, value, code",
    [("depth", 6.0, "pocket_depth"), ("corner_radius", 2.0, "pocket_corner_radius"), ("length", 28.0, "pocket_size")],
)
def test_pocket_diagnostics(field, value, code):
    original = spec_named("P5")
    pocket = original.pockets[0].model_copy(update={field: value})
    report = verify(original.model_copy(update={"pockets": (pocket,)}), build_reference(original))
    assert report.status == FAIL
    assert report.check("pockets").code == code


def test_unwanted_chamfer_fails():
    plate = cq.Workplane("XY").box(60, 40, 5, centered=(True, True, False))
    part = plate.faces(">Z").workplane().pushPoints([(24, 14), (-24, 14), (24, -14), (-24, -14)]).hole(6)
    part = part.faces(">Z").edges("not %CIRCLE").chamfer(0.5)
    report = verify(spec_named("P1"), part.findSolid())
    assert report.status == FAIL
    assert report.check("symmetric_difference").code == "geometry_mismatch"
    assert report.check("surfaces").code == "unexpected_surface"


def null_shape() -> object:
    shape = cq.Shape.__new__(cq.Shape)
    shape.wrapped = TopoDS_Shape()
    return shape


def open_solid() -> object:
    sewing = BRepBuilderAPI_Sewing()
    for face in cq.Workplane("XY").box(60, 40, 5).findSolid().Faces()[:-1]:
        sewing.Add(face.wrapped)
    sewing.Perform()
    return cq.Shape.cast(BRepBuilderAPI_MakeSolid(TopoDS.Shell_s(sewing.SewedShape())).Solid())


def test_invalid_solid_is_never_verified():
    report = verify(spec_named("P1"), open_solid())
    assert (report.status, report.code) == (FAIL, "invalid_solid")


@pytest.mark.parametrize("empty", [False, True])
def test_empty_shape_is_unverified(empty):
    report = verify(spec_named("P1"), null_shape() if empty else None)
    assert (report.status, report.code) == (UNVERIFIED, "empty_shape")


def test_naive_difference_would_pass_an_empty_shape_but_compare_refuses():
    reference = build_reference(spec_named("P1"))
    empty = null_shape()
    assert compare(reference, empty).reliable is False
    same = compare(reference, build_reference(spec_named("P1")))
    assert same.reliable and same.difference == 0 and same.residual <= 1e-9


def test_boolean_errors_make_the_result_unverified(monkeypatch):
    monkeypatch.setattr(
        verify_module, "run_boolean", lambda *arguments: (None, "the Boolean operation reported errors")
    )
    report = verify(spec_named("P1"), shape_of("GA"))
    assert (report.status, report.code) == (UNVERIFIED, "boolean_unreliable")
    report = verify(spec_named("P1"), shape_of("GC"))
    assert report.status == FAIL and report.check("symmetric_difference").status == UNVERIFIED


def test_broken_volume_identity_makes_the_result_unverified(monkeypatch):
    real = verify_module.volume_of
    calls = []

    def skewed(shape):
        calls.append(shape)
        return real(shape) + (0.01 if len(calls) == 4 else 0.0)

    monkeypatch.setattr(verify_module, "volume_of", skewed)
    report = verify(spec_named("P1"), shape_of("GA"))
    assert (report.status, report.code) == (UNVERIFIED, "boolean_unreliable")
    assert "volume identity" in report.check("symmetric_difference").message


def test_contradicting_diagnostics_are_an_internal_inconsistency():
    def lying(spec, measured):
        return (Check("holes", FAIL, "hole_count", "Expected 4 hole(s), found 3.", ("Expected 4 hole(s), found 3.",)),)

    report = verify(spec_named("P1"), shape_of("GA"), diagnose=lying)
    assert (report.status, report.code) == (UNVERIFIED, "internal_inconsistency")


def test_unverified_diagnostics_do_not_block_a_verified_part():
    def unsure(spec, measured):
        return (Check("holes", UNVERIFIED, "diagnostics_failed", "could not measure"),)

    report = verify(spec_named("P1"), shape_of("GA"), diagnose=unsure)
    assert report.status == VERIFIED


def test_crashing_diagnostics_do_not_block_a_verified_part():
    def crashing(spec, measured):
        raise RuntimeError("boom")

    report = verify(spec_named("P1"), shape_of("GA"), diagnose=crashing)
    assert report.status == VERIFIED
    assert report.check("diagnostics").code == "diagnostics_failed"


def test_unsupported_and_invalid_specs():
    through = changed("P5", pockets=(spec_named("P5").pockets[0].model_copy(update={"depth": 15.0}),))
    assert verify(through, shape_of("GA")).status == UNSUPPORTED
    overlapping = Spec.model_validate(PROPOSALS["N2"]["proposal"]["spec"])
    assert (verify(overlapping, shape_of("GA")).status, verify(overlapping, shape_of("GA")).code) == (
        UNVERIFIED,
        "reference_unavailable",
    )


def test_report_is_plain_json():
    report = verify(spec_named("P1"), shape_of("GC"))
    encoded = json.dumps(report.as_dict())
    assert json.loads(encoded)["status"] == FAIL
