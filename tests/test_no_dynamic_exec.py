import ast
from pathlib import Path

PACKAGE = Path(__file__).parent.parent / "text2part"
FORBIDDEN_NAMES = {"exec", "eval", "compile", "__import__", "globals", "locals", "vars", "breakpoint", "input"}
FORBIDDEN_MODULES = {"importlib", "pickle", "marshal", "shelve", "runpy", "code", "codeop", "ctypes", "pty", "socket"}
ALLOWED_MODULES = {"importlib.metadata"}
ALLOWED_DUNDERS = {"__init__", "__name__", "__version__", "__file__"}
REFLECTION = {"getattr", "setattr", "delattr", "hasattr"}
FILES = sorted(PACKAGE.rglob("*.py"))


def forbidden_module(name):
    return name.split(".")[0] in FORBIDDEN_MODULES and name not in ALLOWED_MODULES


def problems(tree):
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id in FORBIDDEN_NAMES:
            found.append((node.lineno, node.id))
        if isinstance(node, ast.Attribute) and node.attr in FORBIDDEN_NAMES:
            found.append((node.lineno, node.attr))
        if isinstance(node, ast.Attribute) and node.attr.startswith("__") and node.attr not in ALLOWED_DUNDERS:
            found.append((node.lineno, node.attr))
        if isinstance(node, ast.Import):
            found.extend((node.lineno, item.name) for item in node.names if forbidden_module(item.name))
        if isinstance(node, ast.ImportFrom) and forbidden_module(node.module or ""):
            found.append((node.lineno, node.module))
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in REFLECTION:
            name = node.args[1] if len(node.args) > 1 else None
            if not (isinstance(name, ast.Constant) and isinstance(name.value, str)):
                found.append((node.lineno, f"{node.func.id} with a name that is not a constant"))
        if isinstance(node, ast.keyword) and node.arg == "shell":
            found.append((node.value.lineno, "shell argument"))
    return found


def test_the_package_has_files_to_scan():
    names = {path.relative_to(PACKAGE).as_posix() for path in FILES}
    assert {"rcq/compiler.py", "rcq/executor.py", "rcq/worker.py", "rcq/grammar.py", "rcq/ir.py"} <= names


def test_package_source_has_no_dynamic_execution():
    found = {}
    for path in FILES:
        issues = problems(ast.parse(path.read_text(encoding="utf-8")))
        if issues:
            found[path.relative_to(PACKAGE).as_posix()] = issues
    assert found == {}


def test_the_scan_detects_what_it_forbids():
    samples = [
        "exec('1')",
        "eval('1')",
        "compile('1', 'f', 'eval')",
        "__import__('os')",
        "import importlib",
        "from pickle import loads",
        "import importlib.util",
        "getattr(w, name)",
        "getattr(w, 'a' + 'b')",
        "x.__class__",
        "w.__getattribute__('box')",
        "subprocess.run('ls', shell=True)",
        "re.compile('a')",
    ]
    for sample in samples:
        assert problems(ast.parse(sample)), sample
    assert problems(ast.parse("getattr(signal, 'SIGKILL', None)")) == []
    assert problems(ast.parse("import importlib.metadata")) == []


def test_method_table_entries_call_only_their_own_method():
    from text2part.rcq.grammar import SIGNATURES

    tree = ast.parse((PACKAGE / "rcq" / "worker.py").read_text(encoding="utf-8"))
    table = next(
        node.value
        for node in tree.body
        if isinstance(node, ast.Assign)
        and isinstance(node.targets[0], ast.Name)
        and node.targets[0].id == "METHOD_TABLE"
    )
    names = []
    for key, entry in zip(table.keys, table.values):
        assert isinstance(key, ast.Constant) and isinstance(entry, ast.Lambda)
        receiver, arguments = (item.arg for item in entry.args.args)
        body = entry.body
        assert isinstance(body, ast.Call) and isinstance(body.func, ast.Attribute)
        assert isinstance(body.func.value, ast.Name) and body.func.value.id == receiver
        assert body.func.attr == key.value
        calls = [node for node in ast.walk(body) if isinstance(node, ast.Call)]
        assert calls == [body]
        for node in ast.walk(body):
            if isinstance(node, ast.Name):
                assert node.id in (receiver, arguments)
            if isinstance(node, ast.Subscript):
                assert isinstance(node.value, ast.Name) and node.value.id == arguments
                assert isinstance(node.slice, ast.Constant) and isinstance(node.slice.value, str)
        used = {node.slice.value for node in ast.walk(body) if isinstance(node, ast.Subscript)}
        assert used == {param.name for param in SIGNATURES[key.value]}
        names.append(key.value)
    assert sorted(names) == sorted(SIGNATURES)
    assert len(names) == len(set(names))
