import io
import json
import subprocess
import sys
from pathlib import Path

import cadquery as cq
import pytest

from text2part.core.builder import rcq_source
from text2part.core.candidate import check_candidate
from text2part.core.spec import check_proposal
from text2part.core.status import ERROR, FAIL, UNSUPPORTED, VERIFIED
from text2part.rcq import executor
from text2part.rcq.compiler import compile_source
from text2part.rcq.executor import Limits, run_program

ROOT = Path(__file__).parent.parent
GOLDEN = Path(__file__).parent / "golden"
PATIENT = Limits(wall_seconds=180.0, cpu_seconds=60)


def spec(name):
    return check_proposal(json.loads((GOLDEN / "specs" / f"{name}.json").read_text(encoding="utf-8"))["proposal"]).spec


def geometry(name):
    return (GOLDEN / "geometry" / f"{name}.py").read_text(encoding="utf-8")


def test_verified_candidate_returns_a_step_file_in_canonical_position(tmp_path):
    outcome = check_candidate(spec("P1"), geometry("GH"), PATIENT)
    assert (outcome.status, outcome.code, outcome.feedback) == (VERIFIED, "", None)
    assert outcome.step.startswith(b"ISO-10303-21;")
    path = tmp_path / "part.step"
    path.write_bytes(outcome.step)
    part = cq.importers.importStep(str(path)).findSolid()
    box = part.BoundingBox()
    assert [round(value, 6) for value in (box.xmin, box.ymin, box.zmin, box.zmax)] == [-30, -20, 0, 5]
    assert abs(part.Volume() - outcome.report["checks"][0]["data"].get("volume", part.Volume())) < 1e-6


def test_failed_candidate_returns_repair_feedback():
    outcome = check_candidate(spec("P1"), geometry("GC"), PATIENT)
    assert (outcome.status, outcome.code) == (FAIL, "geometry_mismatch")
    lines = outcome.feedback.splitlines()
    assert lines[0] == "The part does not match the specification."
    assert "Hole expected at (24.00, 14.00) is at (25.00, 14.00)." in lines


def test_rejected_program_never_reaches_the_executor(monkeypatch):
    monkeypatch.setattr(executor.subprocess, "run", lambda *arguments, **options: pytest.fail("executor started"))
    outcome = check_candidate(spec("P1"), "import cadquery as cq\nresult = 42\n")
    assert (outcome.status, outcome.code) == (ERROR, "result_not_workplane")
    assert outcome.feedback == "The program was rejected: The variable result must be a workplane."


def test_unsupported_spec_is_not_executed(monkeypatch):
    monkeypatch.setattr(executor.subprocess, "run", lambda *arguments, **options: pytest.fail("executor started"))
    pocket = spec("P5").pockets[0].model_copy(update={"depth": 15.0})
    outcome = check_candidate(spec("P5").model_copy(update={"pockets": (pocket,)}), geometry("GA"))
    assert outcome.status == UNSUPPORTED


def test_geometry_error_is_reported_with_its_line():
    source = 'import cadquery as cq\nbox = cq.Workplane("XY").box(1, 1, 1)\nresult = box.edges().fillet(5)\n'
    outcome = check_candidate(spec("P1"), source, PATIENT)
    assert (outcome.status, outcome.code, outcome.error["line"]) == (ERROR, "geometry_error", 3)
    assert outcome.feedback.startswith("The program failed while building the part (line 3, column 27): fillet failed")


def test_execution_without_a_spec_has_no_report():
    execution = run_program(compile_source(geometry("GA")), PATIENT)
    assert execution.ok and execution.report is None and execution.step is None


def test_parent_process_never_imports_cadquery_while_checking():
    code = (
        "import json, sys\n"
        "from pathlib import Path\n"
        "from text2part.core.candidate import check_candidate\n"
        "from text2part.core.spec import check_proposal\n"
        "from text2part.rcq.executor import Limits\n"
        "golden = Path('tests/golden')\n"
        "proposal = json.loads((golden / 'specs' / 'P1.json').read_text())['proposal']\n"
        "source = (golden / 'geometry' / 'GB.py').read_text()\n"
        "outcome = check_candidate(check_proposal(proposal).spec, source, Limits(wall_seconds=180.0, cpu_seconds=60))\n"
        "assert outcome.status == 'VERIFIED', outcome\n"
        "assert 'cadquery' not in sys.modules and 'OCP' not in sys.modules\n"
    )
    completed = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True, timeout=300)
    assert completed.returncode == 0, completed.stderr[-2000:]


def test_unexpected_files_left_by_the_worker_are_an_error(monkeypatch):
    writer = (
        "import sys, pathlib; sys.path.insert(0, sys.argv[1]); "
        "pathlib.Path(sys.argv[2], 'leftover.tmp').write_text('x'); "
        "from text2part.rcq.worker import main; main()"
    )
    monkeypatch.setattr(executor, "BOOTSTRAP", writer)
    execution = run_program(compile_source(geometry("GA")), PATIENT)
    assert execution.ok is False
    assert (execution.error.code, execution.error.message) == (
        "worker_error",
        "The worker left unexpected files: leftover.tmp.",
    )


def test_step_bytes_round_trip_through_memory():
    outcome = check_candidate(spec("P4"), rcq_source(spec("P4")), PATIENT)
    assert outcome.status == VERIFIED
    assert io.BytesIO(outcome.step).read(13) == b"ISO-10303-21;"
