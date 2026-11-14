# text2part

Text-to-CAD part generation for FreeCAD.

## Setup

Python 3.11 or 3.12.

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -e ".[dev]"
```

On Linux and macOS activate the environment with `source .venv/bin/activate`.

## Checks

```bash
pytest
ruff check .
ruff format --check .
python -m text2part.envcheck
```
