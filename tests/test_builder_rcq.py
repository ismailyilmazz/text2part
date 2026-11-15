import json
import math
from pathlib import Path

import pytest
from test_builder import BOUNDARY, assert_matches_spec

from text2part.core.builder import BuildError, build_reference, rcq_source
from text2part.core.spec import Spec, check_proposal, nominal_volume
from text2part.rcq.compiler import compile_source
from text2part.rcq.executor import Limits, run_program
from text2part.rcq.grammar import STAGE_A
from text2part.rcq.ir import RcqError
from text2part.rcq.worker import evaluate

GOLDEN = Path(__file__).parent / "golden" / "specs"
CASES = {path.stem: json.loads(path.read_text(encoding="utf-8")) for path in sorted(GOLDEN.glob("*.json"))}
POSITIVE = sorted(name for name in CASES if name.startswith("P"))
TOLERANCE = 1e-3


def golden_spec(name):
    return check_proposal(CASES[name]["proposal"]).spec


@pytest.mark.parametrize("name", POSITIVE)
def test_compiled_builder_source_matches_the_reference(name, load_brep, symmetric_difference):
    spec = golden_spec(name)
    reference = build_reference(spec)
    execution = run_program(compile_source(rcq_source(spec)), Limits(wall_seconds=180.0, cpu_seconds=60))
    assert execution.ok, execution.error and execution.error.as_dict()
    assert execution.solids == 1 and execution.valid is True
    shape = load_brep(execution.brep)
    assert math.isclose(execution.volume, nominal_volume(spec), rel_tol=1e-9)
    assert symmetric_difference(shape, reference) <= TOLERANCE
    assert_matches_spec(shape.Solids()[0], spec)


@pytest.mark.parametrize("label", sorted(BOUNDARY))
def test_boundary_specs_survive_the_round_trip(label, symmetric_difference):
    spec = BOUNDARY[label]
    shape = evaluate(compile_source(rcq_source(spec)))
    assert len(shape.Solids()) == 1 and shape.isValid()
    assert symmetric_difference(shape, build_reference(spec)) <= TOLERANCE


def test_builder_source_uses_only_stage_a_methods():
    for name in POSITIVE:
        compile_source(rcq_source(golden_spec(name)), frozenset(STAGE_A))


def test_builder_source_is_deterministic_and_plain():
    spec = golden_spec("P6")
    source = rcq_source(spec)
    assert source == rcq_source(spec)
    assert source.startswith("import cadquery as cq\nresult = ")
    assert source.isascii() and source.endswith("\n")
    assert all(line.startswith("result = ") for line in source.splitlines()[1:])


def test_builder_source_refuses_invalid_specs():
    for name in ("N1", "N2", "N4", "N8"):
        spec = Spec.model_validate(CASES[name]["proposal"]["spec"])
        with pytest.raises(BuildError):
            rcq_source(spec)


def many_groups(count):
    groups = [
        {
            "diameter": 1 + 0.01 * index,
            "depth": "through",
            "placement": {"kind": "explicit", "centers": [[-900 + 10 * index, 0]]},
        }
        for index in range(count)
    ]
    data = {
        "schema_version": "1.0",
        "units": "mm",
        "family": "plate",
        "body": {"length": 2000, "width": 100, "height": 5},
        "holes": groups,
    }
    report = check_proposal({"spec": data})
    assert report.status == "VALID"
    return report.spec


def test_specs_with_too_many_distinct_features_do_not_fit_the_language():
    compile_source(rcq_source(many_groups(100)))
    with pytest.raises(RcqError) as caught:
        compile_source(rcq_source(many_groups(190)))
    assert (caught.value.stage, caught.value.code) == ("source", "source_too_large")
    build_reference(many_groups(190))
