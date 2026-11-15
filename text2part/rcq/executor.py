import json
import os
import signal
import subprocess
import sys
import tempfile
import time
from dataclasses import asdict, dataclass
from pathlib import Path

from text2part.rcq.grammar import ENABLED, check_program
from text2part.rcq.ir import Program, RcqError, encode_program
from text2part.rcq.worker import RESULT_FILE, SHAPE_FILE, looks_like_memory

PACKAGE_ROOT = str(Path(__file__).resolve().parents[2])
BOOTSTRAP = "import sys; sys.path.insert(0, sys.argv[1]); from text2part.rcq.worker import main; main()"
WINDOWS_ENVIRONMENT = (
    "SYSTEMROOT",
    "SYSTEMDRIVE",
    "WINDIR",
    "TEMP",
    "TMP",
    "PATH",
    "USERPROFILE",
    "HOMEDRIVE",
    "HOMEPATH",
    "APPDATA",
    "LOCALAPPDATA",
    "NUMBER_OF_PROCESSORS",
    "PROCESSOR_ARCHITECTURE",
)
ERROR_TAIL_BYTES = 2000
MAX_RESULT_BYTES = 64 * 1024
SIGNAL_MESSAGES = {
    int(number): message
    for number, message in (
        (getattr(signal, "SIGXCPU", None), "CPU time limit exceeded."),
        (getattr(signal, "SIGXFSZ", None), "Output size limit exceeded."),
        (getattr(signal, "SIGKILL", None), "The worker was killed by the CPU hard limit or by running out of memory."),
    )
    if number is not None
}


@dataclass(frozen=True)
class Limits:
    wall_seconds: float = 30.0
    cpu_seconds: int = 20
    memory_bytes: int = 4 * 1024**3
    output_bytes: int = 64 * 1024**2


@dataclass(frozen=True)
class Execution:
    ok: bool
    error: RcqError | None = None
    brep: bytes | None = None
    solids: int = 0
    valid: bool = False
    volume: float = 0.0
    bbox: tuple | None = None
    seconds: float = 0.0


def child_environment() -> dict:
    if sys.platform.startswith("win"):
        return {name: os.environ[name] for name in WINDOWS_ENVIRONMENT if name in os.environ}
    return {}


def failure(code: str, message: str, seconds: float) -> Execution:
    return Execution(ok=False, error=RcqError("execute", code, message), seconds=seconds)


def read_tail(path: Path) -> str:
    try:
        data = path.read_bytes()
    except OSError:
        return ""
    return data[-ERROR_TAIL_BYTES:].decode("utf-8", "replace")


def run_program(program: Program, limits: Limits = Limits(), enabled: frozenset = ENABLED) -> Execution:
    check_program(program, enabled)
    request = json.dumps({"limits": asdict(limits), "program": encode_program(program)})
    started = time.perf_counter()
    with tempfile.TemporaryDirectory(prefix="text2part-", ignore_cleanup_errors=True) as name:
        folder = Path(name)
        log = folder / "stderr.log"
        command = [sys.executable, "-I", "-c", BOOTSTRAP, PACKAGE_ROOT, name]
        try:
            with open(log, "wb") as stderr:
                completed = subprocess.run(
                    command,
                    input=request.encode("utf-8"),
                    stdout=subprocess.DEVNULL,
                    stderr=stderr,
                    timeout=limits.wall_seconds,
                    env=child_environment(),
                    cwd=name,
                )
        except subprocess.TimeoutExpired:
            return failure("resource_limit", "Wall clock limit exceeded.", time.perf_counter() - started)
        seconds = time.perf_counter() - started
        result = folder / RESULT_FILE
        if not result.is_file():
            tail = read_tail(log)
            if -completed.returncode in SIGNAL_MESSAGES:
                return failure("resource_limit", SIGNAL_MESSAGES[-completed.returncode], seconds)
            if looks_like_memory(tail):
                return failure("resource_limit", "Memory limit exceeded.", seconds)
            return failure("worker_crash", f"The worker stopped with return code {completed.returncode}.", seconds)
        if result.stat().st_size > MAX_RESULT_BYTES:
            return failure("worker_error", "The worker returned an oversized result.", seconds)
        try:
            payload = json.loads(result.read_text(encoding="utf-8"))
            if not payload["ok"]:
                error = payload["error"]
                return Execution(
                    ok=False,
                    error=RcqError(
                        "execute", str(error["code"]), str(error["message"]), error.get("line"), error.get("col")
                    ),
                    seconds=seconds,
                )
            shape = folder / SHAPE_FILE
            if shape.stat().st_size > limits.output_bytes:
                return failure("resource_limit", "Output size limit exceeded.", seconds)
            return Execution(
                ok=True,
                brep=shape.read_bytes(),
                solids=int(payload["solids"]),
                valid=bool(payload["valid"]),
                volume=float(payload["volume"]),
                bbox=tuple(float(item) for item in payload["bbox"]),
                seconds=seconds,
            )
        except (OSError, ValueError, KeyError, TypeError):
            return failure("worker_error", "The worker returned an unreadable result.", seconds)
