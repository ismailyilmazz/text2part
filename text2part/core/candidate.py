import time
from dataclasses import dataclass

from text2part.core.feedback import error_message, repair_message
from text2part.core.spec import Spec, check_spec
from text2part.core.status import ERROR, UNSUPPORTED, UNVERIFIED
from text2part.rcq.compiler import compile_source
from text2part.rcq.executor import Limits, run_program
from text2part.rcq.ir import RcqError


@dataclass(frozen=True)
class Outcome:
    status: str
    code: str
    error: dict | None = None
    report: dict | None = None
    step: bytes | None = None
    feedback: str | None = None
    seconds: float = 0.0


def check_candidate(spec: Spec, source: str, limits: Limits = Limits()) -> Outcome:
    started = time.perf_counter()

    def elapsed() -> float:
        return round(time.perf_counter() - started, 4)

    issues = check_spec(spec)
    if any(issue.severity == "unsupported" for issue in issues):
        return Outcome(UNSUPPORTED, "unsupported_spec", seconds=elapsed())
    if issues:
        return Outcome(UNVERIFIED, "invalid_spec", seconds=elapsed())
    try:
        program = compile_source(source)
    except RcqError as error:
        details = error.as_dict()
        return Outcome(ERROR, error.code, error=details, feedback=error_message(details), seconds=elapsed())
    execution = run_program(program, limits, spec=spec)
    if not execution.ok:
        details = execution.error.as_dict()
        return Outcome(ERROR, execution.error.code, error=details, feedback=error_message(details), seconds=elapsed())
    report = execution.report
    if report is None:
        return Outcome(UNVERIFIED, "missing_report", seconds=elapsed())
    return Outcome(
        report["status"],
        report["code"],
        report=report,
        step=execution.step,
        feedback=repair_message(report),
        seconds=elapsed(),
    )
