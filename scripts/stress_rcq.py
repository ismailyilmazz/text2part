import argparse
import ast
import collections
import copy
import random
import sys
import time
from pathlib import Path

from text2part.rcq.compiler import compile_source
from text2part.rcq.executor import Limits, run_program
from text2part.rcq.grammar import DEFERRED, ENABLED
from text2part.rcq.ir import RcqError

GOLDEN = Path(__file__).resolve().parent.parent / "tests" / "golden" / "rcq"
DEFAULT_SEED = 20261115
DEFAULT_COUNT = 1000
EXECUTION_LIMITS = Limits(wall_seconds=60.0, cpu_seconds=15)
EXECUTION_CODES = {"geometry_error", "no_solid", "resource_limit", "worker_crash"}
NAMES = ["cq", "eval", "exec", "open", "getattr", "__builtins__", "__import__", "print", "result", "x", "os", "globals"]
ATTRIBUTES = (
    sorted(ENABLED)
    + list(DEFERRED)
    + ["exportStep", "val", "vals", "each", "eachpoint", "toSvg", "exporters", "importers", "Workplane", "Vector"]
    + ["Sketch", "Assembly", "__class__", "__globals__", "__dict__", "_ctx", "ctx", "plane", "objects", "newObject"]
    + ["sphere", "sketch", "tag", "end", "mirror", "shell", "text", "sweep", "revolve", "findSolid", "copyWorkplane"]
)
CONSTANTS = [0, 1, -1, 2.5, 1e-9, 9999, 10000, 10001, 1e5, 1e308, float("inf"), float("nan"), True, False, None]
CONSTANTS += ["", ">Z", "XY", "os", ">Z or <Z", "a" * 40, b"x", 1j, ..., 10**30, -0.0, 3, 6, 20, 0.5]
KEYWORDS = ["clean", "combine", "tol", "obj", "fileName", "centered", "both", "taper", "selector", "offset", None]
STATEMENTS = [
    "import os",
    "import cadquery as cq",
    "from os import system",
    "while True: pass",
    "for i in range(3): pass",
    "if result: pass",
    "def f(): return 1",
    "class A: pass",
    "with open('f', 'w') as f: pass",
    "try:\n    pass\nexcept Exception:\n    pass",
    "del result",
    "result += 1",
    "x: int = 1",
    "print(result)",
    "show_object(result)",
    "cq = 1",
    "global cq",
    "x = lambda: 1",
    "x = [i for i in (1, 2)]",
    "x = eval('1')",
    "x = __import__('os')",
    "cq.exporters.export(result, 'out.step')",
    "result.exportStep('out.step')",
    "x = result.val()",
    "assert result",
    "raise SystemExit",
    "x = y = 1",
    "a, b = 1, 2",
    "pass",
    "x = 1",
    "x = (1, 2, 3)",
    "result = result",
    "result = result.faces()",
    "result = result.edges('|Z').fillet(0.5)",
    "result = result.translate((1, 2, 3))",
    "result = result.union(result)",
    "result = 42",
]
OPERATORS = [ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Pow, ast.FloorDiv, ast.Mod, ast.LShift, ast.BitOr, ast.MatMult]
TEXT_INSERTS = [
    "(",
    ")",
    "[",
    "'",
    '"',
    "\\",
    "\x00",
    "е",
    ";",
    "\n",
    "\t",
    "#",
    ".",
    ",",
    "**",
    " import os\n",
    "=",
    ":",
]


def nodes(tree, kind):
    return [node for node in ast.walk(tree) if isinstance(node, kind)]


def wrapped(node, rng):
    choice = rng.randrange(14)
    if choice == 0:
        return ast.Call(ast.Name("eval", ast.Load()), [node], [])
    if choice == 1:
        return ast.Lambda(ast.arguments([], [], None, [], [], None, []), node)
    if choice == 2:
        return ast.Subscript(node, ast.Constant(rng.choice([0, 1, -1, 99])), ast.Load())
    if choice == 3:
        return ast.Attribute(node, rng.choice(ATTRIBUTES), ast.Load())
    if choice == 4:
        return ast.BinOp(node, rng.choice(OPERATORS)(), ast.Constant(rng.choice([2, 9, 100000, 0])))
    if choice == 5:
        return ast.List([node, copy.deepcopy(node), copy.deepcopy(node)], ast.Load())
    if choice == 6:
        return ast.Tuple([node], ast.Load())
    if choice == 7:
        return ast.UnaryOp(rng.choice([ast.USub, ast.UAdd, ast.Not, ast.Invert])(), node)
    if choice == 8:
        return ast.IfExp(ast.Constant(True), node, ast.Constant(0))
    if choice == 9:
        return ast.Starred(node, ast.Load())
    if choice == 10:
        return ast.Call(node, [], [])
    if choice == 11:
        return ast.Compare(node, [ast.Lt()], [ast.Constant(1)])
    if choice == 12:
        return ast.Dict([ast.Constant("a")], [node])
    return ast.Call(ast.Attribute(node, rng.choice(ATTRIBUTES), ast.Load()), [], [])


def mutate_tree(tree, rng):
    choice = rng.randrange(13)
    body = tree.body
    if choice == 0 and nodes(tree, ast.Constant):
        rng.choice(nodes(tree, ast.Constant)).value = rng.choice(CONSTANTS)
    elif choice == 1 and nodes(tree, ast.Attribute):
        rng.choice(nodes(tree, ast.Attribute)).attr = rng.choice(ATTRIBUTES)
    elif choice == 2 and nodes(tree, ast.Name):
        rng.choice(nodes(tree, ast.Name)).id = rng.choice(NAMES)
    elif choice == 3 and nodes(tree, ast.Call):
        call = rng.choice(nodes(tree, ast.Call))
        if call.args and rng.random() < 0.7:
            del call.args[rng.randrange(len(call.args))]
        elif call.keywords:
            del call.keywords[rng.randrange(len(call.keywords))]
    elif choice == 4 and nodes(tree, ast.Call):
        call = rng.choice(nodes(tree, ast.Call))
        call.keywords.append(ast.keyword(rng.choice(KEYWORDS), ast.Constant(rng.choice(CONSTANTS))))
    elif choice == 5 and nodes(tree, ast.Call):
        call = rng.choice(nodes(tree, ast.Call))
        if call.args:
            call.args.append(copy.deepcopy(rng.choice(call.args)))
        else:
            call.args.append(ast.Constant(rng.choice(CONSTANTS)))
    elif choice == 6 and nodes(tree, ast.Call):
        call = rng.choice(nodes(tree, ast.Call))
        if call.args:
            index = rng.randrange(len(call.args))
            call.args[index] = wrapped(call.args[index], rng)
        else:
            call.func = wrapped(call.func, rng)
    elif choice == 7 and nodes(tree, ast.Assign):
        assign = rng.choice(nodes(tree, ast.Assign))
        assign.value = wrapped(assign.value, rng)
    elif choice == 8:
        body.insert(rng.randrange(len(body) + 1), ast.parse(rng.choice(STATEMENTS)).body[0])
    elif choice == 9 and len(body) > 1:
        action = rng.randrange(3)
        first, second = rng.randrange(len(body)), rng.randrange(len(body))
        if action == 0:
            del body[first]
        elif action == 1:
            body[first], body[second] = body[second], body[first]
        else:
            body.insert(second, copy.deepcopy(body[first]))
    elif choice == 10 and nodes(tree, (ast.BinOp, ast.UnaryOp)):
        node = rng.choice(nodes(tree, (ast.BinOp, ast.UnaryOp)))
        node.op = rng.choice(OPERATORS)() if isinstance(node, ast.BinOp) else rng.choice([ast.Not, ast.Invert])()
    elif choice == 11 and nodes(tree, ast.Assign):
        assign = rng.choice(nodes(tree, ast.Assign))
        target = rng.randrange(4)
        if target == 0:
            assign.targets = [ast.Name(rng.choice(NAMES), ast.Store())]
        elif target == 1:
            assign.targets = [ast.Attribute(ast.Name("result", ast.Load()), "ctx", ast.Store())]
        elif target == 2:
            assign.targets = [ast.Tuple([ast.Name("a", ast.Store()), ast.Name("b", ast.Store())], ast.Store())]
        else:
            assign.targets.append(ast.Name("extra", ast.Store()))
    elif choice == 12 and nodes(tree, ast.Attribute):
        node = rng.choice(nodes(tree, ast.Attribute))
        node.value = wrapped(node.value, rng)


def mutate_text(text, rng):
    choice = rng.randrange(5)
    position = rng.randrange(len(text) + 1)
    if choice == 0:
        return text[:position] + rng.choice(TEXT_INSERTS) + text[position:]
    if choice == 1:
        return text[:position] + text[position + rng.randrange(1, 12) :]
    if choice == 2:
        lines = text.splitlines()
        return "\n".join(lines + [rng.choice(lines)] * rng.choice([3, 200, 2000])) + "\n"
    if choice == 3:
        return text[:position]
    return text + rng.choice(TEXT_INSERTS) * rng.choice([1, 50, 500])


def mutant(source, rng):
    tree = ast.parse(source)
    for _ in range(rng.choice([1, 1, 2, 3])):
        mutate_tree(tree, rng)
    try:
        text = ast.unparse(ast.fix_missing_locations(tree)) + "\n"
    except Exception:
        text = source
    if rng.random() < 0.2:
        text = mutate_text(text, rng)
    return text


def mutants(count=DEFAULT_COUNT, seed=DEFAULT_SEED):
    rng = random.Random(seed)
    sources = [path.read_text(encoding="utf-8") for path in sorted(GOLDEN.glob("*.py"))]
    produced = 0
    while produced < count:
        text = mutant(rng.choice(sources), rng)
        if text in sources:
            continue
        produced += 1
        yield text


def classify(source):
    try:
        return "accepted", compile_source(source)
    except RcqError as error:
        return error.stage, error


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--count", type=int, default=DEFAULT_COUNT)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--compile-only", action="store_true")
    arguments = parser.parse_args()
    started = time.perf_counter()
    stages = collections.Counter()
    codes = collections.Counter()
    accepted = {}
    for source in mutants(arguments.count, arguments.seed):
        stage, outcome = classify(source)
        stages[stage] += 1
        if stage == "accepted":
            accepted.setdefault(source, outcome)
        else:
            codes[outcome.code] += 1
    print(f"mutants: {arguments.count}, seed: {arguments.seed}")
    print("compiler outcomes:", dict(stages.most_common()))
    print("rejection codes:", dict(codes.most_common()))
    print(f"accepted distinct programs: {len(accepted)}")
    unexpected = []
    if not arguments.compile_only:
        results = collections.Counter()
        for index, (source, program) in enumerate(accepted.items(), start=1):
            execution = run_program(program, EXECUTION_LIMITS)
            label = "ok" if execution.ok else execution.error.code
            results[label] += 1
            if not execution.ok and execution.error.code not in EXECUTION_CODES:
                unexpected.append((label, execution.error.message, source))
            if index % 25 == 0:
                print(f"  executed {index}/{len(accepted)}", flush=True)
        print("executor outcomes:", dict(results.most_common()))
    for label, message, source in unexpected[:5]:
        print("UNEXPECTED", label, message, "\n", source)
    print(f"seconds: {time.perf_counter() - started:.0f}")
    return 1 if unexpected else 0


if __name__ == "__main__":
    sys.exit(main())
