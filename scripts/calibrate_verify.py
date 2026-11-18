import argparse
import collections
import json
import math
import random
import sys
import time
from pathlib import Path

from scripts.stress_builder import crowded_spec
from text2part.core.builder import rcq_source
from text2part.core.spec import Spec, check_proposal, expand_holes
from text2part.core.verify import DIFFERENCE_LIMIT, IDENTITY_LIMIT, VERIFIED, verify
from text2part.rcq.compiler import compile_source
from text2part.rcq.worker import evaluate

GOLDEN = Path(__file__).resolve().parent.parent / "tests" / "golden" / "specs"
DEFAULT_SEED = 20261118
DEFAULT_RANDOM = 100
HALF_DIAGONAL = 1 - 1 / math.sqrt(2)


def number(value: float) -> str:
    return repr(float(value))


def outline(cx: float, cy: float, length: float, width: float, radius: float) -> str:
    x0, x1, y0, y1 = cx - length / 2, cx + length / 2, cy - width / 2, cy + width / 2
    if radius == 0:
        corners = [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
        steps = [f"moveTo({number(x0)}, {number(y0)})"]
        steps += [f"lineTo({number(x)}, {number(y)})" for x, y in corners[1:]]
        return ".".join(steps + ["close()"])
    r, s = radius, radius * HALF_DIAGONAL

    def arc(mid: tuple, end: tuple) -> str:
        return f"threePointArc(({number(mid[0])}, {number(mid[1])}), ({number(end[0])}, {number(end[1])}))"

    steps = [
        f"moveTo({number(x0 + r)}, {number(y0)})",
        f"lineTo({number(x1 - r)}, {number(y0)})",
        arc((x1 - s, y0 + s), (x1, y0 + r)),
        f"lineTo({number(x1)}, {number(y1 - r)})",
        arc((x1 - s, y1 - s), (x1 - r, y1)),
        f"lineTo({number(x0 + r)}, {number(y1)})",
        arc((x0 + s, y1 - s), (x0, y1 - r)),
        f"lineTo({number(x0)}, {number(y0 + r)})",
        arc((x0 + s, y0 + s), (x0 + r, y0)),
        "close()",
    ]
    return ".".join(steps)


def alternate_source(spec: Spec) -> str:
    body = spec.body
    lines = [
        "import cadquery as cq",
        f'result = cq.Workplane("XY").{outline(0, 0, body.length, body.width, spec.corner_radius)}'
        f".extrude({number(body.height)})",
    ]
    groups: dict = {}
    for hole in expand_holes(spec):
        groups.setdefault((hole.diameter, hole.depth), []).append((hole.x, hole.y))
    for (diameter, depth), centers in groups.items():
        points = ", ".join(f"({number(x)}, {number(y)})" for x, y in centers)
        size = number(diameter) if depth is None else f"{number(diameter)}, {number(depth)}"
        lines.append(f'result = result.faces(">Z").workplane().pushPoints([{points}]).hole({size})')
    for pocket in spec.pockets:
        shape = outline(pocket.center[0], pocket.center[1], pocket.length, pocket.width, pocket.corner_radius)
        lines.append(f'result = result.faces(">Z").workplane().{shape}.cutBlind({number(-pocket.depth)})')
    return "\n".join(lines) + "\n"


def make(data: dict) -> Spec:
    base = {"schema_version": "1.0", "units": "mm", "family": "block", "holes": [], "pockets": []}
    report = check_proposal({"spec": {**base, **data}})
    if report.status != "VALID":
        raise ValueError(f"calibration spec is not valid: {[issue.message for issue in report.issues]}")
    return report.spec


def grid(diameter: float, nx: int, ny: int, pitch: float, depth: object = "through", center: tuple = (0, 0)) -> dict:
    placement = {"kind": "grid", "nx": nx, "ny": ny, "pitch_x": pitch, "pitch_y": pitch, "center": list(center)}
    return {"diameter": diameter, "depth": depth, "placement": placement}


def pocket(cx: float, cy: float, length: float, width: float, depth: float, radius: float = 0.0) -> dict:
    return {"center": [cx, cy], "length": length, "width": width, "depth": depth, "corner_radius": radius}


def categories(seed: int, count: int) -> dict:
    golden = {path.stem: json.loads(path.read_text(encoding="utf-8")) for path in sorted(GOLDEN.glob("P*.json"))}
    rng = random.Random(seed)
    return {
        "golden P1-P8": [check_proposal(case["proposal"]).spec for case in golden.values()],
        "small": [
            make({"body": {"length": 2, "width": 1.5, "height": 0.5}, "holes": [grid(0.5, 1, 1, 1)]}),
            make({"body": {"length": 0.5, "width": 0.5, "height": 0.5}}),
            make({"body": {"length": 3, "width": 3, "height": 1}, "pockets": [pocket(0, 0, 1.5, 1.5, 0.4, 0.2)]}),
        ],
        "large": [
            make(
                {
                    "body": {"length": 2000, "width": 1500, "height": 40},
                    "corner_radius": 20,
                    "holes": [grid(12, 5, 4, 300)],
                }
            ),
            make({"body": {"length": 2000, "width": 2000, "height": 2000}, "holes": [grid(500, 1, 1, 1)]}),
            make({"body": {"length": 1800, "width": 900, "height": 60}, "pockets": [pocket(0, 0, 1200, 500, 30, 50)]}),
        ],
        "rounded": [
            make({"body": {"length": 80, "width": 80, "height": 8}, "corner_radius": 5, "holes": [grid(5, 3, 2, 20)]}),
            make({"body": {"length": 60, "width": 40, "height": 5}, "corner_radius": 19.9}),
            make({"body": {"length": 60, "width": 40, "height": 10}, "corner_radius": 0.1}),
        ],
        "pocketed": [
            make({"body": {"length": 60, "width": 40, "height": 15}, "pockets": [pocket(0, 0, 30, 20, 5, 3)]}),
            make(
                {
                    "body": {"length": 100, "width": 60, "height": 20},
                    "pockets": [pocket(-25, 0, 30, 40, 10), pocket(25, 0, 30, 40, 5, 14.9)],
                }
            ),
            make({"body": {"length": 40, "width": 40, "height": 12}, "pockets": [pocket(0, 0, 38, 38, 11.5, 0.1)]}),
        ],
        "many features": [
            make({"body": {"length": 400, "width": 400, "height": 10}, "holes": [grid(8, 10, 10, 36)]}),
            make(
                {
                    "body": {"length": 120, "width": 80, "height": 20},
                    "corner_radius": 6,
                    "holes": [grid(4, 6, 1, 15), grid(3, 3, 1, 20, 8, (0, -20))],
                    "pockets": [pocket(-30, 20, 20, 16, 6, 2), pocket(30, 20, 20, 16, 12)],
                }
            ),
        ],
        f"random crowded (seed {seed})": [crowded_spec(rng) for _ in range(count)],
    }


def run(seed: int, count: int) -> int:
    started = time.perf_counter()
    rows = []
    failures = []
    for category, specs in categories(seed, count).items():
        stats = collections.Counter()
        worst_difference = worst_residual = slowest = 0.0
        for spec in specs:
            for style, source in (("builder", rcq_source(spec)), ("alternate", alternate_source(spec))):
                report = verify(spec, evaluate(compile_source(source)))
                stats[report.status] += 1
                data = dict(report.check("symmetric_difference").data) if report.check("symmetric_difference") else {}
                worst_difference = max(worst_difference, data.get("difference") or 0.0)
                worst_residual = max(worst_residual, data.get("residual") or 0.0)
                slowest = max(slowest, report.seconds)
                if report.status != VERIFIED:
                    failures.append((category, style, report.status, report.code, spec.model_dump_json()[:400]))
        rows.append((category, len(specs), dict(stats), worst_difference, worst_residual, slowest))
    print(f"difference limit {DIFFERENCE_LIMIT:g} mm3, identity limit {IDENTITY_LIMIT:g} mm3")
    print("| Category | Specs | Results (2 code styles each) | Worst difference | Worst residual | Slowest verify |")
    print("|---|---|---|---|---|---|")
    for category, size, stats, difference, residual, slowest in rows:
        print(f"| {category} | {size} | {stats} | {difference:.3g} | {residual:.3g} | {slowest:.2f} s |")
    difference = max(row[3] for row in rows)
    residual = max(row[4] for row in rows)
    if difference == 0:
        print("worst difference 0 mm3: every pair was geometrically identical")
    else:
        print(f"worst difference {difference:.3g} mm3 -> margin {DIFFERENCE_LIMIT / difference:.3g}x")
    print(f"worst residual {residual:.3g} mm3 -> margin {IDENTITY_LIMIT / max(residual, 1e-300):.3g}x")
    for failure in failures[:5]:
        print("NOT VERIFIED", failure)
    print(f"seconds: {time.perf_counter() - started:.0f}")
    return 1 if failures else 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--random", type=int, default=DEFAULT_RANDOM)
    arguments = parser.parse_args()
    return run(arguments.seed, arguments.random)


if __name__ == "__main__":
    sys.exit(main())
