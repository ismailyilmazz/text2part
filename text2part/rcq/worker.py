import json
import sys
from pathlib import Path

from text2part.rcq.grammar import ENABLED, MAX_OBJECTS, check_program
from text2part.rcq.ir import Chain, New, Program, RcqError, Ref, Vec, decode_program

RESULT_FILE = "result.json"
SHAPE_FILE = "shape.brep"
STEP_FILE = "part.step"
LOG_FILE = "stderr.log"
HEADROOM_BYTES = 32 * 1024**2
MEMORY_HINTS = ("MemoryError", "bad_alloc", "OutOfMemory", "allocate memory", "failed to map segment")

METHOD_TABLE = {
    "box": lambda w, a: w.box(
        a["length"], a["width"], a["height"], centered=a["centered"], combine=a["combine"], clean=a["clean"]
    ),
    "rect": lambda w, a: w.rect(a["xLen"], a["yLen"], centered=a["centered"], forConstruction=a["forConstruction"]),
    "circle": lambda w, a: w.circle(a["radius"], forConstruction=a["forConstruction"]),
    "moveTo": lambda w, a: w.moveTo(a["x"], a["y"]),
    "lineTo": lambda w, a: w.lineTo(a["x"], a["y"], forConstruction=a["forConstruction"]),
    "threePointArc": lambda w, a: w.threePointArc(a["point1"], a["point2"], forConstruction=a["forConstruction"]),
    "close": lambda w, a: w.close(),
    "extrude": lambda w, a: w.extrude(
        a["until"], combine=a["combine"], clean=a["clean"], both=a["both"], taper=a["taper"]
    ),
    "cutBlind": lambda w, a: w.cutBlind(a["until"], clean=a["clean"], both=a["both"], taper=a["taper"]),
    "cutThruAll": lambda w, a: w.cutThruAll(clean=a["clean"], taper=a["taper"]),
    "hole": lambda w, a: w.hole(a["diameter"], depth=a["depth"], clean=a["clean"]),
    "faces": lambda w, a: w.faces(a["selector"]),
    "edges": lambda w, a: w.edges(a["selector"]),
    "workplane": lambda w, a: w.workplane(
        offset=a["offset"], invert=a["invert"], centerOption=a["centerOption"], origin=a["origin"]
    ),
    "pushPoints": lambda w, a: w.pushPoints(a["pntList"]),
    "fillet": lambda w, a: w.fillet(a["radius"]),
    "cut": lambda w, a: w.cut(a["toCut"], clean=a["clean"]),
    "union": lambda w, a: w.union(a["toUnion"], clean=a["clean"], glue=a["glue"]),
    "translate": lambda w, a: w.translate(a["vec"]),
    "center": lambda w, a: w.center(a["x"], a["y"]),
    "rotate": lambda w, a: w.rotate(a["axisStartPoint"], a["axisEndPoint"], a["angleDegrees"]),
    "polyline": lambda w, a: w.polyline(
        a["listOfXYTuple"], forConstruction=a["forConstruction"], includeCurrent=a["includeCurrent"]
    ),
    "transformed": lambda w, a: w.transformed(rotate=a["rotate"], offset=a["offset"]),
    "add": lambda w, a: w.add(a["obj"]),
}


def looks_like_memory(text: str) -> bool:
    return any(hint in text for hint in MEMORY_HINTS)


def native(value: object, env: dict, cq: object) -> object:
    if isinstance(value, Vec):
        return cq.Vector(value.x, value.y, value.z)
    if isinstance(value, Ref):
        return env[value.name]
    if isinstance(value, Chain):
        return run_chain(value, env, cq)
    if type(value) is tuple:
        return tuple(native(item, env, cq) for item in value)
    return value


def run_chain(chain: Chain, env: dict, cq: object) -> object:
    base = chain.base
    if isinstance(base, New):
        workplane = cq.Workplane(base.plane, origin=native(base.origin, env, cq))
    else:
        workplane = env[base.name]
    for call in chain.calls:
        arguments = {name: native(value, env, cq) for name, value in call.args}
        try:
            workplane = METHOD_TABLE[call.method](workplane, arguments)
        except RcqError:
            raise
        except MemoryError:
            raise
        except Exception as error:
            detail = f"{type(error).__name__}: {error}"
            if looks_like_memory(detail):
                raise MemoryError(detail) from None
            raise RcqError(
                "execute", "geometry_error", f"{call.method} failed: {detail}", call.line, call.col
            ) from None
        if len(workplane.objects) > MAX_OBJECTS:
            raise RcqError(
                "execute", "resource_limit", f"More than {MAX_OBJECTS} objects on the stack.", call.line, call.col
            )
    return workplane


def load_cadquery() -> object:
    import cadquery

    return cadquery


def evaluate(program: Program, enabled: frozenset = ENABLED) -> object:
    cadquery = load_cadquery()
    check_program(program, enabled)
    env: dict = {}
    for assign in program.assigns:
        env[assign.name] = run_chain(assign.chain, env, cadquery)
    try:
        shape = env[program.result].findSolid()
    except ValueError:
        shape = None
    if shape is None or not shape.Solids():
        raise RcqError("execute", "no_solid", "The program did not produce a solid.")
    return shape


def summarize(shape: object) -> dict:
    box = shape.BoundingBox()
    return {
        "solids": len(shape.Solids()),
        "valid": bool(shape.isValid()),
        "volume": float(shape.Volume()),
        "bbox": [box.xmin, box.ymin, box.zmin, box.xmax, box.ymax, box.zmax],
    }


def apply_limits(limits: dict) -> None:
    if not sys.platform.startswith("linux"):
        return
    import resource

    memory = int(limits["memory_bytes"])
    cpu = int(limits["cpu_seconds"])
    output = int(limits["output_bytes"])
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    resource.setrlimit(resource.RLIMIT_FSIZE, (output, output))
    resource.setrlimit(resource.RLIMIT_CPU, (cpu, cpu + 1))
    resource.setrlimit(resource.RLIMIT_AS, (memory, memory))


def respond(folder: Path, payload: dict) -> None:
    (folder / RESULT_FILE).write_text(json.dumps(payload), encoding="utf-8")


def write_shape(shape: object, path: Path, limit: int) -> None:
    try:
        written = bool(shape.exportBrep(str(path)))
    except Exception:
        written = False
    if path.is_file() and path.stat().st_size >= limit:
        raise RcqError("execute", "resource_limit", "Output size limit exceeded.")
    if not written:
        raise RcqError("execute", "export_failed", "The shape could not be written.")


def check_part(spec_data: dict, shape: object, folder: Path) -> dict:
    from text2part.core.export import ExportError, write_step
    from text2part.core.spec import Spec
    from text2part.core.verify import verify

    cadquery = load_cadquery()
    report = verify(Spec.model_validate(spec_data), shape)
    if report.translation is not None:
        moved = shape.Solids()[0].translate(cadquery.Vector(*report.translation))
        try:
            write_step(moved, folder / STEP_FILE)
        except ExportError as error:
            code = "resource_limit" if error.too_large else "export_failed"
            raise RcqError("execute", code, str(error)) from None
    return report.as_dict()


def has_headroom() -> bool:
    try:
        bytearray(HEADROOM_BYTES)
    except MemoryError:
        return False
    return True


def main() -> None:
    folder = Path(sys.argv[2])
    request = json.loads(sys.stdin.read())
    memory_error = RcqError("execute", "resource_limit", "Memory limit exceeded.")
    try:
        load_cadquery()
        apply_limits(request["limits"])
        if not has_headroom():
            raise MemoryError
        shape = evaluate(decode_program(request["program"]))
        summary = summarize(shape)
        write_shape(shape, folder / SHAPE_FILE, int(request["limits"]["output_bytes"]))
        payload = {"ok": True, **summary}
        if request.get("spec") is not None:
            payload["report"] = check_part(request["spec"], shape, folder)
        respond(folder, payload)
    except MemoryError:
        respond(folder, {"ok": False, "error": memory_error.as_dict()})
    except RcqError as error:
        respond(folder, {"ok": False, "error": (error if has_headroom() else memory_error).as_dict()})
    except Exception as error:
        detail = f"{type(error).__name__}: {error}"
        if looks_like_memory(detail) or not has_headroom():
            respond(folder, {"ok": False, "error": memory_error.as_dict()})
        else:
            respond(folder, {"ok": False, "error": RcqError("execute", "worker_error", detail).as_dict()})
