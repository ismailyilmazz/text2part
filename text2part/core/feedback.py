from text2part.core.status import FAIL, UNVERIFIED, VERIFIED

MAX_LINES = 15
MAX_CHARACTERS = 1500
CHECK_ORDER = ("validity", "body", "holes", "pockets", "corners", "surfaces", "symmetric_difference")
NOT_FOR_REPAIR = ("internal_inconsistency", "reference_unavailable", "boolean_unreliable", "diagnostics_failed")


def location(error: dict) -> str:
    if error.get("line") is None:
        return ""
    if error.get("col") is None:
        return f" (line {error['line']})"
    return f" (line {error['line']}, column {error['col']})"


def bounded(lines: list) -> str:
    kept = lines[:MAX_LINES]
    if len(lines) > MAX_LINES:
        kept.append(f"{len(lines) - MAX_LINES} more line(s) not shown.")
    text = "\n".join(kept)
    return text if len(text) <= MAX_CHARACTERS else text[: MAX_CHARACTERS - 3] + "..."


def error_message(error: dict) -> str:
    stage = error.get("stage", "")
    message = error.get("message", "")
    code = error.get("code", "")
    if stage == "execute" and code == "resource_limit":
        return bounded([f"The program used too many resources: {message}", "Make the program simpler."])
    if stage == "execute":
        return bounded([f"The program failed while building the part{location(error)}: {message}"])
    return bounded([f"The program was rejected{location(error)}: {message}"])


def repair_message(report: dict) -> str | None:
    status = report.get("status")
    code = report.get("code", "")
    if status == VERIFIED or code in NOT_FOR_REPAIR:
        return None
    if status == UNVERIFIED and code == "empty_shape":
        return bounded(["The program produced an empty shape. It must build exactly one solid part."])
    if status != FAIL:
        return None
    checks = {check["name"]: check for check in report.get("checks", [])}
    lines = ["The part does not match the specification."]
    for name in CHECK_ORDER:
        check = checks.get(name)
        if check and check.get("status") == FAIL:
            lines.extend(check.get("problems") or [check.get("message", "")])
    return bounded(lines)
