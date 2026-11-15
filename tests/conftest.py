import io
import os
import sys
from pathlib import Path

import pytest

GOLDEN_RCQ = Path(__file__).parent / "golden" / "rcq"
WRITE_FLAGS = os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_APPEND | os.O_TRUNC
FORBIDDEN_EVENTS = (
    "os.remove",
    "os.rename",
    "os.mkdir",
    "os.rmdir",
    "os.system",
    "os.exec",
    "os.fork",
    "os.posix_spawn",
    "os.spawn",
    "os.chmod",
    "os.symlink",
    "os.link",
    "shutil.",
    "subprocess.Popen",
    "socket.",
    "urllib.Request",
    "ftplib.",
    "smtplib.",
    "http.client.",
    "webbrowser.open",
    "pty.spawn",
    "marshal.",
    "pickle.",
)


class AuditLog:
    def __init__(self):
        self.recording = False
        self.events = []

    def hook(self, event, args):
        if not self.recording:
            return
        if event == "open":
            mode = args[1] if len(args) > 1 else None
            flags = args[2] if len(args) > 2 else 0
            writes = isinstance(mode, str) and any(char in mode for char in "wax+")
            if writes or (isinstance(flags, int) and flags & WRITE_FLAGS):
                self.events.append((event, str(args[0])))
        elif event.startswith(FORBIDDEN_EVENTS):
            self.events.append((event, ""))

    def __enter__(self):
        self.events = []
        self.recording = True
        return self

    def __exit__(self, *details):
        self.recording = False


AUDIT_LOG = AuditLog()
sys.addaudithook(AUDIT_LOG.hook)


@pytest.fixture
def audit_log():
    return AUDIT_LOG


@pytest.fixture(scope="session")
def golden_rcq():
    return {path.stem: path.read_text(encoding="utf-8") for path in sorted(GOLDEN_RCQ.glob("*.py"))}


@pytest.fixture(scope="session")
def load_brep():
    def load(data):
        import cadquery

        return cadquery.Shape.importBrep(io.BytesIO(data))

    return load


@pytest.fixture(scope="session")
def symmetric_difference():
    def measure(first, second):
        return first.cut(second).Volume() + second.cut(first).Volume()

    return measure
