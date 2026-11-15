from dataclasses import dataclass

STAGES = ("source", "parse", "reduce", "type", "fold", "execute")
MAX_MESSAGE = 300
MAX_DECODE_DEPTH = 128


class RcqError(Exception):
    def __init__(self, stage: str, code: str, message: str, line: int | None = None, col: int | None = None):
        super().__init__(message)
        self.stage = stage
        self.code = code
        self.message = message[:MAX_MESSAGE]
        self.line = line
        self.col = col

    def as_dict(self) -> dict:
        return {"stage": self.stage, "line": self.line, "col": self.col, "code": self.code, "message": self.message}


@dataclass(frozen=True)
class Vec:
    x: float
    y: float
    z: float


@dataclass(frozen=True)
class Ref:
    name: str


@dataclass(frozen=True)
class New:
    plane: str
    origin: object


@dataclass(frozen=True)
class Call:
    method: str
    args: tuple
    line: int = 0
    col: int = 0


@dataclass(frozen=True)
class Chain:
    base: object
    calls: tuple = ()


@dataclass(frozen=True)
class Assign:
    name: str
    chain: Chain
    line: int = 0


@dataclass(frozen=True)
class Program:
    assigns: tuple
    result: str = "result"


def encode_value(value: object) -> object:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, tuple):
        return [encode_value(item) for item in value]
    if isinstance(value, Vec):
        return {"vec": [value.x, value.y, value.z]}
    if isinstance(value, Ref):
        return {"ref": value.name}
    if isinstance(value, Chain):
        return {"chain": encode_chain(value)}
    raise TypeError(f"value of type {type(value).__name__} is not part of the intermediate representation")


def encode_chain(chain: Chain) -> dict:
    base = chain.base
    if isinstance(base, Ref):
        encoded: dict = {"ref": base.name}
    elif isinstance(base, New):
        encoded = {"new": {"plane": base.plane, "origin": encode_value(base.origin)}}
    else:
        raise TypeError("chain base must be a new workplane or a reference")
    calls = [
        {
            "method": call.method,
            "args": [[name, encode_value(value)] for name, value in call.args],
            "line": call.line,
            "col": call.col,
        }
        for call in chain.calls
    ]
    return {"base": encoded, "calls": calls}


def encode_program(program: Program) -> dict:
    return {
        "result": program.result,
        "assigns": [
            {"name": assign.name, "line": assign.line, "chain": encode_chain(assign.chain)}
            for assign in program.assigns
        ],
    }


def malformed(what: str) -> RcqError:
    return RcqError("execute", "malformed_program", f"The intermediate representation is malformed: {what}.")


def expect(condition: bool, what: str) -> None:
    if not condition:
        raise malformed(what)


def keys(data: object, names: tuple) -> dict:
    expect(isinstance(data, dict) and tuple(sorted(data)) == tuple(sorted(names)), f"expected keys {names}")
    return data


def integer(value: object) -> int:
    expect(type(value) is int, "expected an integer")
    return value


def text(value: object) -> str:
    expect(type(value) is str, "expected a string")
    return value


def decode_value(data: object, depth: int = 0) -> object:
    expect(depth <= MAX_DECODE_DEPTH, "value is nested too deeply")
    if data is None or type(data) in (bool, int, float, str):
        return data
    if type(data) is list:
        return tuple(decode_value(item, depth + 1) for item in data)
    expect(type(data) is dict and len(data) == 1, "unknown value")
    if "vec" in data:
        items = data["vec"]
        expect(type(items) is list and len(items) == 3, "a vector needs three numbers")
        expect(all(type(item) in (int, float) for item in items), "a vector needs three numbers")
        return Vec(*items)
    if "ref" in data:
        return Ref(text(data["ref"]))
    if "chain" in data:
        return decode_chain(data["chain"], depth + 1)
    raise malformed("unknown value")


def decode_chain(data: object, depth: int = 0) -> Chain:
    data = keys(data, ("base", "calls"))
    base = data["base"]
    expect(type(base) is dict and len(base) == 1, "unknown chain base")
    if "ref" in base:
        decoded: object = Ref(text(base["ref"]))
    elif "new" in base:
        new = keys(base["new"], ("plane", "origin"))
        decoded = New(text(new["plane"]), decode_value(new["origin"], depth + 1))
    else:
        raise malformed("unknown chain base")
    expect(type(data["calls"]) is list, "calls must be a list")
    calls = []
    for item in data["calls"]:
        item = keys(item, ("method", "args", "line", "col"))
        expect(type(item["args"]) is list, "arguments must be a list")
        args = []
        for pair in item["args"]:
            expect(type(pair) is list and len(pair) == 2, "an argument is a name and a value")
            args.append((text(pair[0]), decode_value(pair[1], depth + 1)))
        calls.append(Call(text(item["method"]), tuple(args), integer(item["line"]), integer(item["col"])))
    return Chain(decoded, tuple(calls))


def decode_program(data: object) -> Program:
    data = keys(data, ("result", "assigns"))
    expect(type(data["assigns"]) is list, "assigns must be a list")
    assigns = []
    for item in data["assigns"]:
        item = keys(item, ("name", "line", "chain"))
        assigns.append(Assign(text(item["name"]), decode_chain(item["chain"]), integer(item["line"])))
    return Program(tuple(assigns), text(data["result"]))
