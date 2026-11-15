import ast
import math
import warnings

from text2part.rcq.grammar import (
    DEFERRED,
    ENABLED,
    MAX_CALLS,
    MAX_CHAIN,
    MAX_DEPTH,
    MAX_NESTING,
    MAX_SEQUENCE,
    MAX_SOURCE_BYTES,
    MAX_STATEMENTS,
    MAX_STRING,
    MODULE_NAME,
    NUMBER_LIMIT,
    REQUIRED,
    RESULT_NAME,
    SIGNATURES,
    WORKPLANE_SIGNATURE,
    check_program,
    conform,
    is_name,
)
from text2part.rcq.ir import Assign, Call, Chain, New, Program, RcqError, Ref, Vec

ALLOWED_CONTROL = "\n\r\t"
STATEMENT_NAMES = {
    ast.For: "for loop",
    ast.While: "while loop",
    ast.If: "if statement",
    ast.FunctionDef: "function definition",
    ast.ClassDef: "class definition",
    ast.With: "with statement",
    ast.Try: "try statement",
    ast.Import: "second import",
    ast.ImportFrom: "second import",
    ast.Expr: "expression statement",
    ast.AugAssign: "augmented assignment",
    ast.AnnAssign: "annotated assignment",
    ast.Delete: "del statement",
    ast.Global: "global statement",
    ast.Return: "return statement",
}
ARITHMETIC = (ast.Add, ast.Sub, ast.Mult, ast.Div)


def fail(stage: str, code: str, message: str, node: object = None) -> RcqError:
    return RcqError(stage, code, message, getattr(node, "lineno", None), getattr(node, "col_offset", None))


def is_plain_number(value: object) -> bool:
    return type(value) in (int, float)


def checked_number(value: object, stage: str, node: object) -> object:
    if abs(value) > NUMBER_LIMIT or not math.isfinite(value):
        raise fail(stage, "number_out_of_range", f"Numbers must be finite and at most {NUMBER_LIMIT:g} in size.", node)
    return value


class Compiler:
    def __init__(self, enabled: frozenset):
        self.enabled = enabled
        self.env: dict = {}
        self.workplanes: set = set()
        self.calls = 0
        self.sequences: list = []
        self.depths: dict = {}

    def count_call(self, node: object) -> None:
        self.calls += 1
        if self.calls > MAX_CALLS:
            raise fail("reduce", "too_many_calls", f"A program may make at most {MAX_CALLS} calls.", node)

    def value(self, node: object, depth: int) -> object:
        if depth > MAX_DEPTH:
            raise fail("reduce", "too_deep", f"Expressions may be nested at most {MAX_DEPTH} levels deep.", node)
        if isinstance(node, ast.Constant):
            return self.constant(node)
        if isinstance(node, ast.Name):
            return self.name(node)
        if isinstance(node, ast.UnaryOp):
            return self.unary(node, depth)
        if isinstance(node, ast.BinOp):
            return self.binary(node, depth)
        if isinstance(node, (ast.Tuple, ast.List)):
            return self.sequence(node, depth)
        if isinstance(node, ast.Subscript):
            return self.index(node)
        if isinstance(node, ast.Call):
            return self.call(node, depth)
        if isinstance(node, ast.Attribute):
            raise fail(
                "reduce", "attribute_not_allowed", "Attributes may only be used to call an allowed method.", node
            )
        if isinstance(node, (ast.Compare, ast.BoolOp)):
            raise fail("reduce", "operator_not_allowed", "Only + - * / are allowed, and only on numbers.", node)
        raise fail("reduce", "expression_not_allowed", f"{type(node).__name__} is not part of the language.", node)

    def constant(self, node: ast.Constant) -> object:
        value = node.value
        if value is None or type(value) is bool:
            return value
        if is_plain_number(value):
            return checked_number(value, "type", node)
        if type(value) is str:
            if len(value) > MAX_STRING or not value.isascii():
                raise fail("type", "bad_string", f"Strings may have at most {MAX_STRING} ASCII characters.", node)
            return value
        raise fail("reduce", "constant_not_allowed", f"Constants of type {type(value).__name__} are not allowed.", node)

    def name(self, node: ast.Name) -> object:
        if node.id == MODULE_NAME:
            raise fail(
                "reduce", "module_not_a_value", "cq may only be used as cq.Workplane(...) or cq.Vector(...).", node
            )
        if node.id in self.workplanes:
            return Ref(node.id)
        if node.id in self.env:
            return self.env[node.id]
        raise fail("reduce", "undefined_name", f"The name {node.id[:40]!r} is not defined.", node)

    def unary(self, node: ast.UnaryOp, depth: int) -> object:
        if not isinstance(node.op, (ast.USub, ast.UAdd)):
            raise fail("reduce", "operator_not_allowed", "Only unary + and - are allowed.", node)
        operand = self.value(node.operand, depth + 1)
        if not is_plain_number(operand):
            raise fail("type", "bad_operand", "Unary + and - need a number.", node)
        return -operand if isinstance(node.op, ast.USub) else operand

    def binary(self, node: ast.BinOp, depth: int) -> object:
        if not isinstance(node.op, ARITHMETIC):
            raise fail("reduce", "operator_not_allowed", "Only + - * / are allowed, and only on numbers.", node)
        left = self.value(node.left, depth + 1)
        right = self.value(node.right, depth + 1)
        if not is_plain_number(left) or not is_plain_number(right):
            raise fail("type", "bad_operand", "Arithmetic needs numbers on both sides.", node)
        if isinstance(node.op, ast.Add):
            result = left + right
        elif isinstance(node.op, ast.Sub):
            result = left - right
        elif isinstance(node.op, ast.Mult):
            result = left * right
        else:
            if right == 0:
                raise fail("fold", "division_by_zero", "Division by zero.", node)
            result = left / right
        return checked_number(result, "fold", node)

    def sequence(self, node: object, depth: int) -> tuple:
        if len(node.elts) > MAX_SEQUENCE:
            raise fail("reduce", "sequence_too_long", f"A list may have at most {MAX_SEQUENCE} items.", node)
        items = []
        for element in node.elts:
            item = self.value(element, depth + 1)
            if not (is_plain_number(item) or type(item) in (bool, tuple) or isinstance(item, Vec)):
                raise fail("type", "bad_item", "Lists may only hold numbers, booleans, points and lists.", element)
            items.append(item)
        result = tuple(items)
        depth_here = 1 + max((self.depths[id(item)] for item in result if type(item) is tuple), default=0)
        if depth_here > MAX_NESTING:
            raise fail("reduce", "too_nested", f"Lists may be nested at most {MAX_NESTING} levels deep.", node)
        self.sequences.append(result)
        self.depths[id(result)] = depth_here
        return result

    def index(self, node: ast.Subscript) -> object:
        position = node.slice
        if isinstance(position, ast.UnaryOp) and isinstance(position.op, ast.USub):
            inner = position.operand
            number = -inner.value if isinstance(inner, ast.Constant) and type(inner.value) is int else None
        else:
            number = position.value if isinstance(position, ast.Constant) and type(position.value) is int else None
        if not isinstance(node.value, ast.Name) or number is None:
            raise fail("reduce", "bad_index", "Indexing must be name[k] with a constant integer k.", node)
        target = self.name(node.value)
        if type(target) is not tuple:
            raise fail("type", "bad_index", "Only lists can be indexed.", node)
        if not -len(target) <= number < len(target):
            raise fail("type", "bad_index", "List index is out of range.", node)
        return target[number]

    def call(self, node: ast.Call, depth: int) -> object:
        function = node.func
        if not isinstance(function, ast.Attribute):
            raise fail(
                "reduce", "not_a_method_call", "Only cq.Workplane, cq.Vector and workplane methods can be called.", node
            )
        pending = []
        current: object = node
        while (
            isinstance(current, ast.Call)
            and isinstance(current.func, ast.Attribute)
            and not (isinstance(current.func.value, ast.Name) and current.func.value.id == MODULE_NAME)
        ):
            pending.append(current)
            if len(pending) > MAX_CHAIN:
                raise fail("reduce", "chain_too_long", f"A chain may have at most {MAX_CHAIN} method calls.", node)
            current = current.func.value
        if not pending:
            return self.constructor(node, depth)
        for item in pending:
            self.check_method(item)
        base = self.value(current, depth + 1)
        if isinstance(base, Ref):
            chain_base: object = base
            calls: tuple = ()
        elif isinstance(base, Chain):
            chain_base = base.base
            calls = base.calls
        else:
            raise fail("type", "not_a_workplane", "Methods can only be called on a workplane.", pending[-1])
        bound = []
        for item in reversed(pending):
            self.count_call(item)
            method = item.func.attr
            arguments = self.bind(method, SIGNATURES[method], item, depth)
            bound.append(Call(method, arguments, item.func.end_lineno or 0, item.func.end_col_offset or 0))
        if len(calls) + len(bound) > MAX_CHAIN:
            raise fail("reduce", "chain_too_long", f"A chain may have at most {MAX_CHAIN} method calls.", node)
        return Chain(chain_base, calls + tuple(bound))

    def check_method(self, node: ast.Call) -> None:
        method = node.func.attr
        if method.startswith("_"):
            raise fail("reduce", "attribute_not_allowed", "Names starting with an underscore are not allowed.", node)
        if method in DEFERRED:
            raise fail("reduce", "method_deferred", f"The method {method} is not available in this version.", node)
        if method not in SIGNATURES:
            raise fail("reduce", "method_not_allowed", f"The method {method[:40]} is not part of the language.", node)
        if method not in self.enabled:
            raise fail("reduce", "method_not_enabled", f"The method {method} is not enabled.", node)

    def constructor(self, node: ast.Call, depth: int) -> object:
        name = node.func.attr
        self.count_call(node)
        if name == "Workplane":
            arguments = dict(self.bind("cq.Workplane", WORKPLANE_SIGNATURE, node, depth))
            return Chain(New(arguments["inPlane"], arguments["origin"]), ())
        if name == "Vector":
            if (
                node.keywords
                or any(isinstance(item, ast.Starred) for item in node.args)
                or len(node.args) not in (2, 3)
            ):
                raise fail("type", "bad_argument", "cq.Vector takes two or three numbers.", node)
            numbers = [self.value(item, depth + 1) for item in node.args]
            if not all(is_plain_number(item) for item in numbers):
                raise fail("type", "bad_argument", "cq.Vector takes two or three numbers.", node)
            return Vec(*(numbers + [0])[:3])
        raise fail("reduce", "attribute_not_allowed", f"cq.{name[:40]} is not part of the language.", node)

    def bind(self, label: str, signature: tuple, node: ast.Call, depth: int) -> tuple:
        positional = []
        for argument in node.args:
            if isinstance(argument, ast.Starred):
                if not isinstance(argument.value, ast.Name):
                    raise fail("type", "bad_star", "Only *name with a list variable can be unpacked.", argument)
                unpacked = self.name(argument.value)
                if type(unpacked) is not tuple:
                    raise fail("type", "bad_star", "Only *name with a list variable can be unpacked.", argument)
                positional.extend(unpacked)
            else:
                positional.append(self.value(argument, depth + 1))
            if len(positional) > len(signature):
                raise fail("type", "too_many_arguments", f"{label} takes at most {len(signature)} arguments.", node)
        given = {param.name: value for param, value in zip(signature, positional)}
        names = {param.name for param in signature}
        for keyword in node.keywords:
            if keyword.arg is None:
                raise fail("type", "kwargs_not_allowed", "Unpacking with ** is not allowed.", node)
            if keyword.arg not in names:
                raise fail("type", "unknown_keyword", f"{label} has no argument named {keyword.arg[:40]}.", node)
            if keyword.arg in given:
                raise fail("type", "duplicate_argument", f"{label} got {keyword.arg} more than once.", node)
            given[keyword.arg] = self.value(keyword.value, depth + 1)
        bound = []
        for param in signature:
            if param.name in given:
                value = given[param.name]
            elif param.default is REQUIRED:
                raise fail("type", "missing_argument", f"{label} needs the argument {param.name}.", node)
            else:
                value = param.default
            try:
                bound.append((param.name, conform(param.kind, value)))
            except ValueError as error:
                raise fail("type", "bad_argument", f"{label}: {param.name}: {error}.", node) from None
        return tuple(bound)

    def statement(self, node: object) -> Assign | None:
        if not isinstance(node, ast.Assign):
            label = STATEMENT_NAMES.get(type(node), type(node).__name__)
            raise fail("reduce", "statement_not_allowed", f"Only assignments are allowed; found: {label}.", node)
        if len(node.targets) != 1 or not isinstance(node.targets[0], ast.Name):
            raise fail("reduce", "bad_target", "An assignment needs exactly one plain name on the left.", node)
        name = node.targets[0].id
        if name == MODULE_NAME:
            raise fail("reduce", "bad_target", "The name cq cannot be reassigned.", node)
        if not is_name(name):
            raise fail("reduce", "bad_target", "Variable names must be ASCII and start with a letter.", node)
        value = self.value(node.value, 0)
        if isinstance(value, Ref):
            value = Chain(value, ())
        if isinstance(value, Chain):
            self.workplanes.add(name)
            self.env.pop(name, None)
            return Assign(name, value, node.lineno)
        if value is None or type(value) is str:
            raise fail("type", "bad_assignment", "Strings and None cannot be stored in variables.", node)
        self.workplanes.discard(name)
        self.env[name] = value
        return None


def parse(source: object) -> ast.Module:
    if type(source) is not str:
        raise RcqError("source", "not_text", "The program must be text.")
    if len(source) > MAX_SOURCE_BYTES:
        raise RcqError("source", "source_too_large", f"The program may have at most {MAX_SOURCE_BYTES} bytes.")
    for number, line in enumerate(source.split("\n"), start=1):
        for column, char in enumerate(line):
            if not (" " <= char <= "~" or char in ALLOWED_CONTROL):
                raise RcqError(
                    "source", "bad_character", "Only printable ASCII characters are allowed.", number, column
                )
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            return ast.parse(source)
    except SyntaxError as error:
        raise RcqError("parse", "syntax_error", f"Syntax error: {error.msg}.", error.lineno, error.offset) from None
    except (RecursionError, MemoryError, ValueError, OverflowError) as error:
        raise RcqError("parse", "unparsable", f"The program could not be parsed: {type(error).__name__}.") from None


def compile_source(source: object, enabled: frozenset = ENABLED) -> Program:
    tree = parse(source)
    body = tree.body
    first = body[0] if body else None
    if (
        not isinstance(first, ast.Import)
        or len(first.names) != 1
        or first.names[0].name != "cadquery"
        or first.names[0].asname != MODULE_NAME
    ):
        raise fail("reduce", "bad_import", "The first statement must be exactly: import cadquery as cq", first)
    if len(body) - 1 > MAX_STATEMENTS:
        raise fail("reduce", "too_many_statements", f"A program may have at most {MAX_STATEMENTS} statements.", first)
    compiler = Compiler(frozenset(enabled) & ENABLED)
    assigns = []
    for node in body[1:]:
        assign = compiler.statement(node)
        if assign is not None:
            assigns.append(assign)
    if RESULT_NAME in compiler.env:
        raise RcqError("type", "result_not_workplane", f"The variable {RESULT_NAME} must be a workplane.")
    if RESULT_NAME not in compiler.workplanes:
        raise RcqError("type", "no_result", f"The program must assign a workplane to {RESULT_NAME}.")
    program = Program(tuple(assigns), RESULT_NAME)
    check_program(program, compiler.enabled)
    return program
