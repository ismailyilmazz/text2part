import math
import runpy
import subprocess
import sys
from pathlib import Path

import pytest

from text2part.rcq import executor
from text2part.rcq.compiler import compile_source
from text2part.rcq.executor import Execution, Limits, child_environment, run_program
from text2part.rcq.ir import RcqError
from text2part.rcq.worker import evaluate, summarize

ROOT = Path(__file__).parent.parent
GOLDEN = Path(__file__).parent / "golden" / "rcq"
HEADER = "import cadquery as cq\n"
LINUX = pytest.mark.skipif(not sys.platform.startswith("linux"), reason="resource limits are only claimed on Linux")
P1 = 60 * 40 * 5 - 4 * math.pi * 3**2 * 5
DIAGONAL = 30 / (2 * math.sqrt(2))
TAPERED_SIDE = 10 - 2 * 5 * math.tan(math.radians(10))
EXPECTED = {
    "g01_box": (12000, (-30, -20, -2.5, 30, 20, 2.5)),
    "g02_plate_holes": (P1, (-30, -20, 0, 30, 20, 5)),
    "g03_sketch_and_cut": (P1, (-30, -20, 0, 30, 20, 5)),
    "g04_arcs": ((40 * 20 + math.pi * 10**2) * 8, (-30, -10, 0, 30, 10, 8)),
    "g05_ring": (math.pi * (20**2 - 5**2) * 10, (-20, -20, 0, 20, 20, 10)),
    "g06_cut_blind": (50 * 30 * 20 - 20 * 10 * 6, (-25, -15, -10, 25, 15, 10)),
    "g07_fillet": ((80 * 80 - (4 - math.pi) * 5**2) * 8, (-40, -40, -4, 40, 40, 4)),
    "g08_union": (40 * 40 * 10 + math.pi * 5**2 * 30, (-20, -20, -5, 20, 20, 35)),
    "g09_pocket": (60 * 40 * 15 - (30 * 20 - (4 - math.pi) * 3**2) * 5, (-30, -20, 0, 30, 20, 15)),
    "g10_variables": (P1, (-30, -20, -2.5, 30, 20, 2.5)),
    "g11_both_sides": (10 * 20 * 8, (-5, -10, -4, 5, 10, 4)),
    "g12_taper": (5 / 3 * (100 + TAPERED_SIDE**2 + 10 * TAPERED_SIDE), (-5, -5, 0, 5, 5, 5)),
    "g13_plane_and_vector": (2 * 4 * 6, (0, -1, 6, 2, 5, 10)),
    "g14_selector_expression": (1000 - 2 * 10 * (1 - math.pi / 4), (-5, -5, -5, 5, 5, 5)),
    "g15_workplane_options": (20 * 20 * 10 - math.pi * 2**2 * 3, (-10, -10, -5, 10, 10, 5)),
    "g20_rotate": (12000, (-20, -30, -2.5, 20, 30, 2.5)),
    "g21_polyline": (40 * 30 / 2 * 10, (0, 0, 0, 40, 30, 10)),
    "g22_transformed": (10 * 20 * 5, (-DIAGONAL, -DIAGONAL, 10, DIAGONAL, DIAGONAL, 15)),
    "g23_add": (40 * 40 * 10 + math.pi * 5**2 * 25, (-20, -20, -5, 20, 20, 30)),
}
HEAVY = (
    HEADER
    + "points = ["
    + ", ".join(f"({x * 3}, {y * 3})" for x in range(-11, 11) for y in range(-11, 11))
    + ']\na = cq.Workplane("XY").box(80, 80, 6).faces(">Z").workplane().pushPoints(points).hole(1)\n'
    + "".join(f"a = a.union(a.rotate((0, 0, 0), (0, 0, 1), {7 + step}))\n" for step in range(40))
    + "result = a\n"
)
LIGHT = HEADER + 'result = cq.Workplane("XY").box(1, 2, 3)\n'
PATIENT = Limits(wall_seconds=180.0, cpu_seconds=60)
DEEPEST = 38


def test_expected_values_cover_every_golden_program(golden_rcq):
    assert set(EXPECTED) == set(golden_rcq)


@pytest.mark.parametrize("name", sorted(EXPECTED))
def test_golden_program_gives_the_hand_computed_part(name, golden_rcq):
    volume, bbox = EXPECTED[name]
    summary = summarize(evaluate(compile_source(golden_rcq[name])))
    assert summary["solids"] == 1 and summary["valid"] is True
    assert math.isclose(summary["volume"], volume, rel_tol=1e-9)
    assert all(math.isclose(measured, expected, abs_tol=1e-6) for measured, expected in zip(summary["bbox"], bbox))


@pytest.mark.parametrize("name", sorted(EXPECTED))
def test_golden_program_matches_plain_cadquery(name, golden_rcq, symmetric_difference):
    ours = evaluate(compile_source(golden_rcq[name]))
    plain = runpy.run_path(str(GOLDEN / f"{name}.py"))["result"].findSolid()
    assert math.isclose(ours.Volume(), plain.Volume(), rel_tol=1e-12)
    assert symmetric_difference(ours, plain) <= 1e-9


@pytest.mark.parametrize("name", ["g02_plate_holes", "g09_pocket", "g23_add"])
def test_separate_process_returns_the_same_part(name, golden_rcq, load_brep, symmetric_difference):
    program = compile_source(golden_rcq[name])
    execution = run_program(program, PATIENT)
    assert execution.ok and execution.error is None
    assert execution.solids == 1 and execution.valid is True
    assert math.isclose(execution.volume, EXPECTED[name][0], rel_tol=1e-9)
    assert all(math.isclose(a, b, abs_tol=1e-6) for a, b in zip(execution.bbox, EXPECTED[name][1]))
    shape = load_brep(execution.brep)
    assert math.isclose(shape.Volume(), execution.volume, rel_tol=1e-12)
    assert symmetric_difference(shape, evaluate(program)) <= 1e-9


def test_default_limits_match_the_design():
    assert Limits() == Limits(wall_seconds=30.0, cpu_seconds=20, memory_bytes=4 * 1024**3, output_bytes=64 * 1024**2)


def test_parent_process_never_imports_cadquery():
    code = (
        "import sys\n"
        "from text2part.rcq.compiler import compile_source\n"
        "from text2part.rcq.executor import Limits, run_program\n"
        f"execution = run_program(compile_source({LIGHT!r}), Limits(wall_seconds=180.0, cpu_seconds=60))\n"
        "assert execution.ok and abs(execution.volume - 6) < 1e-9, execution\n"
        "assert 'cadquery' not in sys.modules and 'OCP' not in sys.modules\n"
    )
    completed = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True, timeout=300)
    assert completed.returncode == 0, completed.stderr[-2000:]


def test_child_is_started_without_secrets_shell_or_inherited_folder(monkeypatch):
    seen = {}

    def fake_run(command, **options):
        seen["command"] = command
        seen.update(options)
        return subprocess.CompletedProcess(command, 1)

    monkeypatch.setenv("HF_TOKEN", "secret-value")
    monkeypatch.setenv("GEMINI_API_KEY", "secret-value")
    monkeypatch.setattr(executor.subprocess, "run", fake_run)
    execution = run_program(compile_source(LIGHT))
    assert execution.ok is False and execution.error.code == "worker_crash"
    assert seen["command"][:3] == [sys.executable, "-I", "-c"]
    assert seen["command"][3] == executor.BOOTSTRAP
    assert Path(seen["command"][4]).resolve() == ROOT.resolve()
    assert "secret-value" not in str(seen["env"]) and "HF_TOKEN" not in seen["env"]
    assert "shell" not in seen and seen["stdout"] == subprocess.DEVNULL
    assert Path(seen["cwd"]).name.startswith("text2part-") and not Path(seen["cwd"]).exists()
    assert LIGHT not in " ".join(seen["command"])
    if sys.platform.startswith("linux"):
        assert seen["env"] == {} and child_environment() == {}
    assert not any("TOKEN" in name or "KEY" in name or "SECRET" in name for name in executor.WINDOWS_ENVIRONMENT)


def test_geometry_failure_is_a_structured_error():
    program = compile_source(HEADER + 'box = cq.Workplane("XY").box(1, 1, 1)\nresult = box.edges().fillet(5)\n')
    with pytest.raises(RcqError) as caught:
        evaluate(program)
    assert caught.value.as_dict()["stage"] == "execute"
    assert (caught.value.code, caught.value.line) == ("geometry_error", 3)
    assert caught.value.message.startswith("fillet failed: ")
    execution = run_program(program, PATIENT)
    assert execution.ok is False and execution.brep is None
    assert (execution.error.stage, execution.error.code, execution.error.line) == ("execute", "geometry_error", 3)


@pytest.mark.parametrize(
    "body",
    [
        'result = cq.Workplane("XY").rect(1, 1)\n',
        'result = cq.Workplane("XY")\n',
        'result = cq.Workplane("XY").circle(1).circle(2)\n',
    ],
)
def test_program_without_a_solid_is_an_error(body):
    with pytest.raises(RcqError) as caught:
        evaluate(compile_source(HEADER + body))
    assert (caught.value.stage, caught.value.code) == ("execute", "no_solid")


def test_several_solids_are_reported_not_hidden():
    source = HEADER + 'result = cq.Workplane("XY").pushPoints([(0, 0), (10, 0)]).box(1, 1, 1)\n'
    summary = summarize(evaluate(compile_source(source)))
    assert summary["solids"] == 2 and math.isclose(summary["volume"], 2)


def test_deepest_accepted_program_survives_the_worker_round_trip():
    box = 'cq.Workplane("XY").box(1, 1, 1)'

    def nested(depth):
        return HEADER + "result = " + f"{box}.union(" * depth + box + ")" * depth + "\n"

    with pytest.raises(RcqError) as caught:
        compile_source(nested(DEEPEST + 1))
    assert caught.value.code == "too_deep"
    execution = run_program(compile_source(nested(DEEPEST)), PATIENT)
    assert execution.ok and execution.solids == 1
    assert math.isclose(execution.volume, 1, rel_tol=1e-9)


def test_runaway_object_growth_is_stopped_on_every_platform():
    source = HEADER + 'a = cq.Workplane("XY").box(1, 1, 1)\n' + "a = a.add(a)\n" * 40 + "result = a\n"
    with pytest.raises(RcqError) as caught:
        evaluate(compile_source(source))
    assert (caught.value.stage, caught.value.code) == ("execute", "resource_limit")
    assert caught.value.line is not None and caught.value.line < 20


def test_evaluation_order_follows_python(tmp_path, symmetric_difference):
    source = (
        HEADER
        + 'a = cq.Workplane("XY").box(10, 10, 10)\n'
        + "b = a\n"
        + 'a = a.faces(">Z").workplane().circle(2).cutThruAll()\n'
        + "result = b.union(a.translate((20, 0, 0)))\n"
    )
    path = tmp_path / "order_probe.py"
    path.write_text(source, encoding="utf-8")
    ours = evaluate(compile_source(source))
    plain = runpy.run_path(str(path))["result"].findSolid()
    assert len(ours.Solids()) == 2
    assert math.isclose(ours.Volume(), 2000 - math.pi * 4 * 10, rel_tol=1e-9)
    assert symmetric_difference(ours, plain) <= 1e-9


def test_golden_programs_have_no_side_effects(golden_rcq, audit_log, tmp_path, monkeypatch):
    programs = [compile_source(source) for source in golden_rcq.values()]
    for program in programs:
        evaluate(program)
    monkeypatch.chdir(tmp_path)
    with audit_log as log:
        for program in programs:
            evaluate(program)
    assert log.events == []
    assert list(tmp_path.iterdir()) == []


def test_the_audit_log_detects_side_effects(audit_log, tmp_path):
    with audit_log as log:
        (tmp_path / "written.txt").write_text("x", encoding="utf-8")
        subprocess.run([sys.executable, "-c", "pass"], check=True)
    kinds = {event for event, _ in log.events}
    assert "open" in kinds and "subprocess.Popen" in kinds


def test_wall_clock_limit_gives_resource_limit_and_parent_survives():
    execution = run_program(compile_source(LIGHT), Limits(wall_seconds=0.3))
    assert isinstance(execution, Execution) and execution.ok is False
    assert (execution.error.stage, execution.error.code) == ("execute", "resource_limit")
    assert execution.seconds < 5
    assert run_program(compile_source(LIGHT), PATIENT).ok


def test_output_size_limit_gives_resource_limit():
    execution = run_program(compile_source(LIGHT), Limits(output_bytes=1000, wall_seconds=180.0, cpu_seconds=60))
    assert execution.ok is False and execution.brep is None
    assert (execution.error.stage, execution.error.code) == ("execute", "resource_limit")
    assert execution.error.message == "Output size limit exceeded."
    assert run_program(compile_source(LIGHT), PATIENT).ok


@LINUX
def test_cpu_limit_stops_heavy_geometry():
    execution = run_program(compile_source(HEAVY), Limits(cpu_seconds=3, wall_seconds=60))
    assert execution.ok is False
    assert (execution.error.stage, execution.error.code) == ("execute", "resource_limit")
    assert execution.error.message == "CPU time limit exceeded." and execution.seconds < 30
    assert run_program(compile_source(LIGHT), PATIENT).ok


@LINUX
def test_wall_clock_limit_stops_heavy_geometry():
    execution = run_program(compile_source(HEAVY), Limits(cpu_seconds=60, wall_seconds=5))
    assert execution.ok is False
    assert (execution.error.code, execution.error.message) == ("resource_limit", "Wall clock limit exceeded.")
    assert 5 <= execution.seconds < 15


@LINUX
def test_memory_limit_gives_resource_limit():
    execution = run_program(compile_source(LIGHT), Limits(memory_bytes=256 * 1024**2))
    assert execution.ok is False
    assert (execution.error.stage, execution.error.code) == ("execute", "resource_limit")
    assert "Memory" in execution.error.message
    assert run_program(compile_source(LIGHT), PATIENT).ok


@LINUX
def test_a_three_gigabyte_allocation_in_the_worker_is_stopped(monkeypatch):
    allocate = (
        "import sys; sys.path.insert(0, sys.argv[1]); from text2part.rcq import worker; "
        "worker.evaluate = lambda program: bytearray(3 * 1024**3); worker.main()"
    )
    monkeypatch.setattr(executor, "BOOTSTRAP", allocate)
    execution = run_program(compile_source(LIGHT), Limits(memory_bytes=2 * 1024**3, wall_seconds=180.0))
    assert execution.ok is False
    assert (execution.error.stage, execution.error.code) == ("execute", "resource_limit")
    assert "Memory" in execution.error.message
    monkeypatch.undo()
    assert run_program(compile_source(LIGHT), PATIENT).ok


@LINUX
def test_heavy_valid_program_is_stopped_by_default_limits():
    execution = run_program(compile_source(HEAVY))
    assert execution.ok is False and execution.error.code == "resource_limit"


@pytest.mark.parametrize(
    "bootstrap, code",
    [
        ("import sys; sys.exit(3)", "worker_crash"),
        pytest.param("import os; os.abort()", "worker_crash", marks=LINUX),
        ("raise SystemExit('boom')", "worker_crash"),
        ("import sys, pathlib; pathlib.Path(sys.argv[2], 'result.json').write_text('{')", "worker_error"),
        ("import sys, pathlib; pathlib.Path(sys.argv[2], 'result.json').write_text('[1]')", "worker_error"),
        ("import sys, pathlib; pathlib.Path(sys.argv[2], 'result.json').write_text('{\"ok\": true}')", "worker_error"),
        ("import sys, pathlib; pathlib.Path(sys.argv[2], 'result.json').write_text('x' * 100000)", "worker_error"),
        ("import sys; sys.stderr.write('MemoryError'); sys.exit(1)", "resource_limit"),
        ("import sys; sys.stdout.write('x' * 5000000); sys.exit(1)", "worker_crash"),
    ],
)
def test_parent_survives_a_failing_worker(monkeypatch, bootstrap, code):
    monkeypatch.setattr(executor, "BOOTSTRAP", bootstrap)
    execution = run_program(compile_source(LIGHT))
    assert execution.ok is False and execution.brep is None
    assert (execution.error.stage, execution.error.code) == ("execute", code)


def test_run_program_refuses_malformed_programs():
    with pytest.raises(RcqError) as caught:
        run_program("result = 1")
    assert caught.value.code == "malformed_program"
