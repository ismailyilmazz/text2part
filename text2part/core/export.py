from pathlib import Path

MAX_STEP_BYTES = 10 * 1024**2


class ExportError(Exception):
    def __init__(self, message: str, too_large: bool = False):
        super().__init__(message)
        self.too_large = too_large


def write_step(shape: object, path: Path, limit: int = MAX_STEP_BYTES) -> int:
    path = Path(path)
    try:
        shape.exportStep(str(path))
    except Exception as error:
        raise ExportError(f"The STEP file could not be written: {type(error).__name__}.") from None
    if not path.is_file() or path.stat().st_size == 0:
        raise ExportError("The STEP file could not be written.")
    size = path.stat().st_size
    if size > limit:
        path.unlink()
        raise ExportError(f"The STEP file is {size} bytes; the limit is {limit} bytes.", too_large=True)
    return size
