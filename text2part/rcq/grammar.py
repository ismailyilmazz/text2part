import math
from dataclasses import dataclass

from text2part.rcq.ir import Chain, New, Program, RcqError, Ref, Vec

MAX_SOURCE_BYTES = 16 * 1024
MAX_STATEMENTS = 150
MAX_CHAIN = 80
MAX_CALLS = 2000
MAX_POINTS = 500
MAX_OBJECTS = 5000
MAX_DEPTH = 40
MAX_SEQUENCE = 512
MAX_NESTING = 2
MAX_STRING = 32
MAX_NAME = 64
NUMBER_LIMIT = 1e4
MAX_TAPER = 89.0

PLANES = ("XY", "YZ", "ZX", "XZ", "YX", "ZY", "front", "back", "left", "right", "top", "bottom")
CENTER_OPTIONS = ("CenterOfMass", "ProjectedOrigin", "CenterOfBoundBox")
MODULE_NAME = "cq"
RESULT_NAME = "result"

STAGE_A = (
    "box",
    "rect",
    "circle",
    "moveTo",
    "lineTo",
    "threePointArc",
    "close",
    "extrude",
    "cutBlind",
    "cutThruAll",
    "hole",
    "faces",
    "edges",
    "workplane",
    "pushPoints",
    "fillet",
    "cut",
    "union",
    "translate",
    "center",
)
STAGE_B = ("rotate", "polyline", "transformed", "add")
DEFERRED = (
    "line",
    "hLine",
    "vLine",
    "hLineTo",
    "vLineTo",
    "radiusArc",
    "polygon",
    "rarray",
    "polarArray",
    "vertices",
    "cylinder",
    "chamfer",
    "intersect",
    "combine",
)
ENABLED = frozenset(STAGE_A + STAGE_B)


class Required:
    pass


REQUIRED = Required()


@dataclass(frozen=True)
class Param:
    name: str
    kind: str
    default: object = REQUIRED


SIGNATURES = {
    "box": (
        Param("length", "positive"),
        Param("width", "positive"),
        Param("height", "positive"),
        Param("centered", "centered3", True),
        Param("combine", "bool", True),
        Param("clean", "bool", True),
    ),
    "rect": (
        Param("xLen", "positive"),
        Param("yLen", "positive"),
        Param("centered", "centered2", True),
        Param("forConstruction", "bool", False),
    ),
    "circle": (Param("radius", "positive"), Param("forConstruction", "bool", False)),
    "moveTo": (Param("x", "number", 0), Param("y", "number", 0)),
    "lineTo": (Param("x", "number"), Param("y", "number"), Param("forConstruction", "bool", False)),
    "threePointArc": (Param("point1", "point"), Param("point2", "point"), Param("forConstruction", "bool", False)),
    "close": (),
    "extrude": (
        Param("until", "nonzero"),
        Param("combine", "bool", True),
        Param("clean", "bool", True),
        Param("both", "bool", False),
        Param("taper", "optional_taper", None),
    ),
    "cutBlind": (
        Param("until", "nonzero"),
        Param("clean", "bool", True),
        Param("both", "bool", False),
        Param("taper", "optional_taper", None),
    ),
    "cutThruAll": (Param("clean", "bool", True), Param("taper", "taper", 0)),
    "hole": (Param("diameter", "positive"), Param("depth", "optional_positive", None), Param("clean", "bool", True)),
    "faces": (Param("selector", "optional_selector", None),),
    "edges": (Param("selector", "optional_selector", None),),
    "workplane": (
        Param("offset", "number", 0.0),
        Param("invert", "bool", False),
        Param("centerOption", "center_option", "ProjectedOrigin"),
        Param("origin", "optional_point", None),
    ),
    "pushPoints": (Param("pntList", "points"),),
    "fillet": (Param("radius", "positive"),),
    "cut": (Param("toCut", "workplane"), Param("clean", "bool", True)),
    "union": (Param("toUnion", "workplane"), Param("clean", "bool", True), Param("glue", "bool", False)),
    "translate": (Param("vec", "point"),),
    "center": (Param("x", "number"), Param("y", "number")),
    "rotate": (Param("axisStartPoint", "point"), Param("axisEndPoint", "point"), Param("angleDegrees", "number")),
    "polyline": (
        Param("listOfXYTuple", "polyline_points"),
        Param("forConstruction", "bool", False),
        Param("includeCurrent", "bool", False),
    ),
    "transformed": (Param("rotate", "point", (0, 0, 0)), Param("offset", "point", (0, 0, 0))),
    "add": (Param("obj", "workplane"),),
}
WORKPLANE_SIGNATURE = (Param("inPlane", "plane", "XY"), Param("origin", "point", (0, 0, 0)))

SELECTOR_OPERATORS = "<>|#+-"
SELECTOR_AXES = "XYZ"
SELECTOR_WORDS = ("and", "or", "not")


def is_number(value: object) -> bool:
    return type(value) in (int, float) and abs(value) <= NUMBER_LIMIT and math.isfinite(value)


def is_name(value: object) -> bool:
    return (
        type(value) is str
        and 0 < len(value) <= MAX_NAME
        and value.isascii()
        and value[0].isalpha()
        and all(char.isalnum() or char == "_" for char in value)
        and value != MODULE_NAME
    )


def tokenize_selector(text: str) -> list[str]:
    tokens = []
    position = 0
    while position < len(text):
        char = text[position]
        if char == " ":
            position += 1
        elif char in "()":
            tokens.append(char)
            position += 1
        elif char in SELECTOR_OPERATORS:
            if position + 1 >= len(text) or text[position + 1] not in SELECTOR_AXES:
                raise ValueError("a selector operator must be followed by X, Y or Z")
            tokens.append(text[position : position + 2])
            position += 2
        elif char in SELECTOR_AXES:
            tokens.append(char)
            position += 1
        else:
            word = next((item for item in SELECTOR_WORDS if text.startswith(item, position)), None)
            if word is None:
                raise ValueError(f"unexpected character {char!r} in selector")
            tokens.append(word)
            position += len(word)
    return tokens


def parse_selector(text: str) -> str:
    if len(text) > MAX_STRING:
        raise ValueError(f"a selector may have at most {MAX_STRING} characters")
    tokens = tokenize_selector(text)
    position = 0

    def peek() -> str | None:
        return tokens[position] if position < len(tokens) else None

    def take() -> str:
        nonlocal position
        position += 1
        return tokens[position - 1]

    def factor() -> str:
        token = peek()
        if token == "not":
            take()
            return "not " + factor()
        if token == "(":
            take()
            inner = union()
            if peek() != ")":
                raise ValueError("missing closing parenthesis in selector")
            take()
            return "(" + inner + ")"
        if token is not None and token[-1] in SELECTOR_AXES:
            return take()
        raise ValueError("a selector needs an axis such as >Z")

    def intersection() -> str:
        parts = [factor()]
        while peek() == "and":
            take()
            parts.append(factor())
        return " and ".join(parts)

    def union() -> str:
        parts = [intersection()]
        while peek() == "or":
            take()
            parts.append(intersection())
        return " or ".join(parts)

    result = union()
    if position != len(tokens):
        raise ValueError("unexpected text after selector")
    return result


def conform_point(value: object) -> object:
    if isinstance(value, Vec):
        if all(is_number(item) for item in (value.x, value.y, value.z)):
            return value
        raise ValueError("vector components must be finite numbers within range")
    if type(value) is tuple and len(value) in (2, 3) and all(is_number(item) for item in value):
        return value
    raise ValueError("expected a point: two or three numbers")


def conform_points(value: object, minimum: int) -> tuple:
    if type(value) is not tuple:
        raise ValueError("expected a list of points")
    if len(value) > MAX_POINTS:
        raise ValueError(f"at most {MAX_POINTS} points are allowed")
    if len(value) < minimum:
        raise ValueError(f"at least {minimum} point(s) are needed")
    return tuple(conform_point(item) for item in value)


def conform_centered(value: object, size: int) -> object:
    if type(value) is bool:
        return value
    if type(value) is tuple and len(value) == size and all(type(item) is bool for item in value):
        return value
    raise ValueError(f"expected True, False or {size} booleans")


def conform(kind: str, value: object) -> object:
    if kind.startswith("optional_"):
        return None if value is None else conform(kind[len("optional_") :], value)
    if kind == "number":
        if is_number(value):
            return value
        raise ValueError(f"expected a finite number with absolute value at most {NUMBER_LIMIT:g}")
    if kind == "positive":
        if is_number(value) and value > 0:
            return value
        raise ValueError("expected a positive number")
    if kind == "nonzero":
        if is_number(value) and value != 0:
            return value
        raise ValueError("expected a nonzero number")
    if kind == "taper":
        if is_number(value) and abs(value) <= MAX_TAPER:
            return value
        raise ValueError(f"expected an angle between -{MAX_TAPER:g} and {MAX_TAPER:g} degrees")
    if kind == "bool":
        if type(value) is bool:
            return value
        raise ValueError("expected True or False")
    if kind == "centered2":
        return conform_centered(value, 2)
    if kind == "centered3":
        return conform_centered(value, 3)
    if kind == "point":
        return conform_point(value)
    if kind == "points":
        return conform_points(value, 1)
    if kind == "polyline_points":
        return conform_points(value, 2)
    if kind == "selector":
        if type(value) is str:
            return parse_selector(value)
        raise ValueError("expected a selector string such as '>Z'")
    if kind == "plane":
        if type(value) is str and value in PLANES:
            return value
        raise ValueError("expected a plane name such as 'XY'")
    if kind == "center_option":
        if type(value) is str and value in CENTER_OPTIONS:
            return value
        raise ValueError("expected one of " + ", ".join(CENTER_OPTIONS))
    if kind == "workplane":
        if isinstance(value, (Ref, Chain)):
            return value
        raise ValueError("expected a workplane")
    raise ValueError(f"unknown parameter kind {kind}")


def malformed(what: str) -> RcqError:
    return RcqError("execute", "malformed_program", f"The intermediate representation is malformed: {what}.")


def check_chain(chain: object, defined: set, enabled: frozenset, counter: list, depth: int) -> None:
    if not isinstance(chain, Chain):
        raise malformed("expected a chain")
    if depth > MAX_DEPTH:
        raise malformed("chains are nested too deeply")
    base = chain.base
    if isinstance(base, Ref):
        if base.name not in defined:
            raise malformed(f"reference to undefined workplane {base.name!r}")
    elif isinstance(base, New):
        counter[0] += 1
        try:
            same = conform("plane", base.plane) == base.plane and conform("point", base.origin) == base.origin
        except ValueError as error:
            raise malformed(str(error)) from None
        if not same:
            raise malformed("workplane arguments are not in canonical form")
    else:
        raise malformed("unknown chain base")
    if len(chain.calls) > MAX_CHAIN:
        raise malformed("chain is too long")
    for call in chain.calls:
        counter[0] += 1
        if call.method not in enabled or call.method not in SIGNATURES:
            raise malformed(f"method {call.method!r} is not enabled")
        signature = SIGNATURES[call.method]
        if tuple(name for name, _ in call.args) != tuple(param.name for param in signature):
            raise malformed(f"arguments of {call.method} do not match its signature")
        for param, (_, value) in zip(signature, call.args):
            try:
                same = conform(param.kind, value) == value
            except ValueError as error:
                raise malformed(f"{call.method}: {param.name}: {error}") from None
            if not same:
                raise malformed(f"{call.method}: {param.name} is not in canonical form")
            if isinstance(value, Ref) and value.name not in defined:
                raise malformed(f"reference to undefined workplane {value.name!r}")
            if isinstance(value, Chain):
                check_chain(value, defined, enabled, counter, depth + 1)
    if counter[0] > MAX_CALLS:
        raise malformed("too many calls")


def check_program(program: object, enabled: frozenset = ENABLED) -> None:
    if not isinstance(program, Program):
        raise malformed("expected a program")
    if len(program.assigns) > MAX_STATEMENTS:
        raise malformed("too many statements")
    defined: set = set()
    counter = [0]
    for assign in program.assigns:
        if not is_name(assign.name):
            raise malformed("invalid variable name")
        check_chain(assign.chain, defined, enabled, counter, 0)
        defined.add(assign.name)
    if program.result != RESULT_NAME or program.result not in defined:
        raise malformed("the program has no result workplane")
