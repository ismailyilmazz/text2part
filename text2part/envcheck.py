import argparse
import importlib.metadata
import json
import math
import platform
import shutil
import sys
import tempfile
import time
from pathlib import Path

PLATE_SIZE = (60.0, 40.0, 5.0)
HOLE_DIAMETER = 6.0
HOLE_EDGE_OFFSET = 6.0
SIZE_TOLERANCE = 1e-6
VOLUME_TOLERANCE = 1e-3
CHECK_NAMES = ("import_cadquery", "build_part", "measure", "export_step", "export_stl", "step_roundtrip")
SYSTEM_LIBRARY_HINT = "Missing system graphics library. Install it with: apt-get install -y libgl1 libx11-6"


def expected_plate_volume() -> float:
    x, y, z = PLATE_SIZE
    return x * y * z - 4 * math.pi * (HOLE_DIAMETER / 2) ** 2 * z


def build_reference_plate():
    import cadquery as cq

    x, y, z = PLATE_SIZE
    return (
        cq.Workplane("XY")
        .box(x, y, z)
        .faces(">Z")
        .workplane()
        .rect(x - 2 * HOLE_EDGE_OFFSET, y - 2 * HOLE_EDGE_OFFSET, forConstruction=True)
        .vertices()
        .hole(HOLE_DIAMETER)
    )


def package_version(name: str) -> str | None:
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return None


class Recorder:
    def __init__(self) -> None:
        self.checks: list[dict] = []

    def run(self, name: str, step) -> bool:
        started = time.perf_counter()
        entry: dict = {"name": name}
        try:
            details = dict(step() or {})
            entry["ok"] = bool(details.pop("ok", True))
            entry.update(details)
        except Exception as error:
            entry["ok"] = False
            entry["error"] = f"{type(error).__name__}: {error}"
            if "libGL" in str(error) or "libX11" in str(error):
                entry["hint"] = SYSTEM_LIBRARY_HINT
        entry["seconds"] = round(time.perf_counter() - started, 3)
        self.checks.append(entry)
        return entry["ok"]


def cad_checks(recorder: Recorder, workdir: Path) -> None:
    state: dict = {}
    expected_volume = expected_plate_volume()
    step_path = workdir / "plate.step"
    stl_path = workdir / "plate.stl"

    def import_cadquery():
        import cadquery

        state["cq"] = cadquery
        return {"version": cadquery.__version__}

    def build_part():
        part = build_reference_plate()
        state["part"] = part
        shape = part.val()
        solids = len(part.solids().vals())
        return {"valid": shape.isValid(), "solids": solids, "ok": shape.isValid() and solids == 1}

    def measure():
        shape = state["part"].val()
        box = shape.BoundingBox()
        size = [round(box.xlen, 6), round(box.ylen, 6), round(box.zlen, 6)]
        volume = shape.Volume()
        cylinders = sum(1 for face in shape.Faces() if face.geomType() == "CYLINDER")
        size_ok = all(abs(measured - target) <= SIZE_TOLERANCE for measured, target in zip(size, PLATE_SIZE))
        volume_ok = abs(volume - expected_volume) <= VOLUME_TOLERANCE
        return {
            "size": size,
            "volume": round(volume, 4),
            "expected_volume": round(expected_volume, 4),
            "cylindrical_faces": cylinders,
            "ok": size_ok and volume_ok,
        }

    def export_step():
        state["cq"].exporters.export(state["part"], str(step_path))
        size = step_path.stat().st_size
        return {"bytes": size, "ok": size > 0}

    def export_stl():
        state["cq"].exporters.export(state["part"], str(stl_path))
        size = stl_path.stat().st_size
        return {"bytes": size, "ok": size > 0}

    def step_roundtrip():
        volume = state["cq"].importers.importStep(str(step_path)).val().Volume()
        return {"volume": round(volume, 4), "ok": abs(volume - expected_volume) <= VOLUME_TOLERANCE}

    for name, step in zip(CHECK_NAMES, (import_cadquery, build_part, measure, export_step, export_stl, step_roundtrip)):
        if not recorder.run(name, step):
            break


def gpu_check() -> dict:
    import torch

    available = torch.cuda.is_available()
    details = {"torch": torch.__version__, "cuda": available, "ok": available}
    if available:
        details["device"] = torch.cuda.get_device_name(0)
    return details


def run_checks(include_gpu: bool = False) -> dict:
    recorder = Recorder()
    workdir = Path(tempfile.mkdtemp(prefix="text2part-"))
    try:
        cad_checks(recorder, workdir)
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
    if include_gpu:
        recorder.run("gpu", gpu_check)
    return {
        "python": platform.python_version(),
        "platform": platform.platform(),
        "packages": {name: package_version(name) for name in ("cadquery", "cadquery-ocp", "torch")},
        "checks": recorder.checks,
        "ok": all(check["ok"] for check in recorder.checks) and len(recorder.checks) >= len(CHECK_NAMES),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m text2part.envcheck")
    parser.add_argument("--gpu", action="store_true")
    args = parser.parse_args(argv)
    report = run_checks(include_gpu=args.gpu)
    print(json.dumps(report, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
