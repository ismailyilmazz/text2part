import argparse
import ast
import collections
import concurrent.futures
import json
import math
import random
import re
import sys
import time
from pathlib import Path

from text2part.rcq.compiler import compile_source
from text2part.rcq.executor import Limits, run_program
from text2part.rcq.ir import RcqError

DEFAULT_SAMPLE = 300
DEFAULT_SEED = 20261118
STRUCTURE_STAGES = ("source", "parse", "reduce")
NOISE_CALLS = {"print", "show_object", "display", "export", "debug"}
FENCE = re.compile(r"```(?:python)?\s*\n(.*?)```", re.S)


def extract_code(item: dict) -> str:
    text = next(message["content"] for message in item["messages"] if message["role"] == "assistant")
    blocks = FENCE.findall(text)
    return blocks[-1] if blocks else text


def call_name(node: ast.AST) -> str | None:
    if isinstance(node, ast.Call):
        function = node.func
        if isinstance(function, ast.Name):
            return function.id
        if isinstance(function, ast.Attribute):
            return function.attr
    return None


def normalize(code: str) -> str:
    tree = ast.parse(code)
    kept = []
    last = None
    for statement in tree.body:
        if isinstance(statement, ast.Expr):
            value = statement.value
            if isinstance(value, (ast.Constant, ast.Name)) or call_name(value) in NOISE_CALLS:
                continue
        if (
            isinstance(statement, ast.Assign)
            and len(statement.targets) == 1
            and isinstance(statement.targets[0], ast.Name)
        ):
            last = statement.targets[0].id
        kept.append(statement)
    source = "\n".join(ast.unparse(statement) for statement in kept) + "\n"
    if last is not None and last != "result":
        source += f"result = {last}\n"
    return source


def wilson(successes: int, total: int, z: float = 1.96) -> tuple:
    if total == 0:
        return (0.0, 0.0)
    share = successes / total
    centre = (share + z * z / (2 * total)) / (1 + z * z / total)
    spread = z * math.sqrt(share * (1 - share) / total + z * z / (4 * total * total)) / (1 + z * z / total)
    return (max(0.0, centre - spread), min(1.0, centre + spread))


def execute(program: object) -> str:
    execution = run_program(program, Limits())
    if not execution.ok:
        return execution.error.code
    if execution.solids == 1 and execution.valid:
        return "one valid solid"
    if execution.valid:
        return "several solids"
    return "invalid solid"


def survey(path: Path, sample: int, seed: int, workers: int) -> dict:
    items = json.loads(path.read_text(encoding="utf-8"))
    rejections = collections.Counter()
    structure = 0
    compiled = []
    for item in items:
        try:
            source = normalize(extract_code(item))
        except (SyntaxError, ValueError, RecursionError):
            rejections["parse/unparsable_original"] += 1
            continue
        try:
            compiled.append(compile_source(source))
            structure += 1
        except RcqError as error:
            rejections[f"{error.stage}/{error.code}"] += 1
            if error.stage not in STRUCTURE_STAGES:
                structure += 1
    chosen = random.Random(seed).sample(compiled, min(sample, len(compiled)))
    with concurrent.futures.ThreadPoolExecutor(workers) as pool:
        outcomes = collections.Counter(pool.map(execute, chosen))
    return {
        "file": path.name,
        "total": len(items),
        "m1": structure,
        "m2": len(compiled),
        "rejections": rejections,
        "sample": len(chosen),
        "m3": outcomes["one valid solid"],
        "outcomes": outcomes,
    }


def percent(part: int, whole: int) -> str:
    return f"{100 * part / whole:.1f}%" if whole else "n/a"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("files", nargs="+", type=Path)
    parser.add_argument("--sample", type=int, default=DEFAULT_SAMPLE)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--workers", type=int, default=2)
    arguments = parser.parse_args()
    started = time.perf_counter()
    print(f"sample {arguments.sample} per file for M3, seed {arguments.seed}")
    print("| File | Programs | M1 structure | M2 compiles | M3 one valid solid (sample) | M3 95% interval |")
    print("|---|---|---|---|---|---|")
    results = [survey(path, arguments.sample, arguments.seed, arguments.workers) for path in arguments.files]
    for result in results:
        low, high = wilson(result["m3"], result["sample"])
        print(
            f"| {result['file']} | {result['total']} | {result['m1']} ({percent(result['m1'], result['total'])}) "
            f"| {result['m2']} ({percent(result['m2'], result['total'])}) "
            f"| {result['m3']}/{result['sample']} ({percent(result['m3'], result['sample'])}) "
            f"| {100 * low:.1f}% to {100 * high:.1f}% |"
        )
    for result in results:
        print(f"{result['file']} top rejections: {dict(result['rejections'].most_common(10))}")
        print(f"{result['file']} executor outcomes: {dict(result['outcomes'].most_common())}")
    print(f"seconds: {time.perf_counter() - started:.0f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
