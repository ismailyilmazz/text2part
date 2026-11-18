import pytest

from scripts.data_compat import extract_code, normalize, wilson
from text2part.rcq.compiler import compile_source


def item(text):
    return {"messages": [{"role": "user", "content": "a plate"}, {"role": "assistant", "content": text}]}


def test_code_is_taken_from_the_last_fenced_block():
    text = "Reasoning first.\n```python\nx = 1\n```\nThen the answer:\n```python\nimport cadquery as cq\n```\n"
    assert extract_code(item(text)) == "import cadquery as cq\n"
    assert extract_code(item("import cadquery as cq\n")) == "import cadquery as cq\n"


def test_normalize_drops_noise_and_names_the_result():
    code = (
        '"""A plate."""\n'
        "import cadquery as cq\n"
        "# a comment with a degree sign °\n"
        "r = cq.Workplane('XY').box(1, 2, 3)\n"
        "print(r)\n"
        "show_object(r)\n"
        "r\n"
    )
    source = normalize(code)
    assert source == "import cadquery as cq\nr = cq.Workplane('XY').box(1, 2, 3)\nresult = r\n"
    assert len(compile_source(source).assigns) == 2


def test_normalize_keeps_an_existing_result():
    assert normalize("import cadquery as cq\nresult = cq.Workplane('XY')\n").endswith("result = cq.Workplane('XY')\n")


def test_normalize_raises_on_broken_code():
    with pytest.raises(SyntaxError):
        normalize("result = (")


def test_wilson_interval():
    low, high = wilson(277, 300)
    assert 0.88 < low < 0.89 and 0.948 < high < 0.952
    assert wilson(0, 0) == (0.0, 0.0)
    assert wilson(10, 10)[1] == 1.0
