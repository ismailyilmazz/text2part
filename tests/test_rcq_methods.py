import inspect

import cadquery as cq
import pytest

from text2part.rcq.compiler import compile_source
from text2part.rcq.grammar import (
    CENTER_OPTIONS,
    ENABLED,
    MAX_CALLS,
    MAX_CHAIN,
    MAX_POINTS,
    MAX_STATEMENTS,
    PLANES,
    REQUIRED,
    SIGNATURES,
    WORKPLANE_SIGNATURE,
)
from text2part.rcq.ir import RcqError

HEADER = "import cadquery as cq\n"
BASE = 'cq.Workplane("XY").box(10, 10, 10)'
SKETCH = 'cq.Workplane("XY")'
OMITTED = {"faces": {"tag"}, "edges": {"tag"}, "cut": {"tol"}, "union": {"tol"}}
STRICTER_DEFAULTS = {("union", "toUnion")}


def accepted(expression):
    return compile_source(HEADER + f"result = {expression}\n")


def rejected(expression):
    with pytest.raises(RcqError) as caught:
        compile_source(HEADER + f"result = {expression}\n")
    return caught.value


@pytest.mark.parametrize("method", sorted(SIGNATURES))
def test_signature_matches_cadquery(method):
    real = inspect.signature(getattr(cq.Workplane, method)).parameters
    real_names = [name for name in real if name != "self"]
    ours = SIGNATURES[method]
    assert [param.name for param in ours] == [name for name in real_names if name not in OMITTED.get(method, set())]
    for param in ours:
        default = real[param.name].default
        if (method, param.name) in STRICTER_DEFAULTS:
            assert param.default is REQUIRED
        elif default is inspect.Parameter.empty:
            assert param.default is REQUIRED, param.name
        else:
            assert param.default == default and type(param.default) is type(default), param.name


def test_workplane_constructor_matches_cadquery():
    real = inspect.signature(cq.Workplane.__init__).parameters
    assert [name for name in real if name != "self"] == ["inPlane", "origin", "obj"]
    assert [(param.name, param.default) for param in WORKPLANE_SIGNATURE] == [
        ("inPlane", real["inPlane"].default),
        ("origin", real["origin"].default),
    ]
    for plane in PLANES:
        cq.Workplane(plane)
    assert set(CENTER_OPTIONS) == set(real_center_options())


def real_center_options():
    annotation = inspect.signature(cq.Workplane.workplane).parameters["centerOption"].annotation
    return annotation.__args__


VALID_CALLS = {
    "box": [
        f"{SKETCH}.box(1, 2, 3)",
        f"{SKETCH}.box(1, 2, 3, False)",
        f"{SKETCH}.box(1, 2, 3, centered=(True, False, True), combine=False, clean=False)",
    ],
    "rect": [f"{BASE}.rect(1, 2)", f"{BASE}.rect(1, 2, False, True)", f"{BASE}.rect(1, 2, centered=(True, False))"],
    "circle": [f"{BASE}.circle(1)", f"{BASE}.circle(0.001, True)", f"{BASE}.circle(radius=10000)"],
    "moveTo": [f"{BASE}.moveTo()", f"{BASE}.moveTo(1)", f"{BASE}.moveTo(-1, y=2.5)"],
    "lineTo": [f"{BASE}.lineTo(0, 0)", f"{BASE}.lineTo(-1, 2, True)"],
    "threePointArc": [
        f"{BASE}.threePointArc((1, 1), (2, 0))",
        f"{BASE}.threePointArc((1, 1, 0), cq.Vector(2, 0, 0), forConstruction=True)",
    ],
    "close": [f"{BASE}.close()"],
    "extrude": [
        f"{BASE}.extrude(1)",
        f"{BASE}.extrude(-1, False, False, True, 5)",
        f"{BASE}.extrude(2, both=True, taper=-89)",
        f"{BASE}.extrude(2, taper=None)",
    ],
    "cutBlind": [f"{BASE}.cutBlind(-1)", f"{BASE}.cutBlind(1, False, True, 10)"],
    "cutThruAll": [f"{BASE}.cutThruAll()", f"{BASE}.cutThruAll(False, 5)"],
    "hole": [f"{BASE}.hole(1)", f"{BASE}.hole(1, 2)", f"{BASE}.hole(1, depth=None, clean=False)"],
    "faces": [f"{BASE}.faces()", f"{BASE}.faces('>Z')", f"{BASE}.faces(selector='not <Z')", f"{BASE}.faces(None)"],
    "edges": [f"{BASE}.edges()", f"{BASE}.edges('|Z')", f"{BASE}.edges(selector='|X or |Y')"],
    "workplane": [
        f"{BASE}.workplane()",
        f"{BASE}.workplane(1.5, True, 'CenterOfBoundBox', (0, 0, 1))",
        f"{BASE}.workplane(offset=-2, origin=cq.Vector(1, 2, 3))",
    ],
    "pushPoints": [
        f"{BASE}.pushPoints([(0, 0)])",
        f"{BASE}.pushPoints(((0, 0), (1, 1, 1)))",
        f"{BASE}.pushPoints([cq.Vector(1, 2, 3)])",
    ],
    "fillet": [f"{BASE}.fillet(0.5)", f"{BASE}.fillet(radius=1)"],
    "cut": [f"{BASE}.cut({BASE})", f"{BASE}.cut(toCut={BASE}, clean=False)"],
    "union": [f"{BASE}.union({BASE})", f"{BASE}.union({BASE}, False, True)"],
    "translate": [f"{BASE}.translate((1, 2, 3))", f"{BASE}.translate((1, 2))", f"{BASE}.translate(cq.Vector(1, 2, 3))"],
    "center": [f"{BASE}.center(1, -2)", f"{BASE}.center(x=0, y=0)"],
    "rotate": [
        f"{BASE}.rotate((0, 0, 0), (0, 0, 1), 90)",
        f"{BASE}.rotate(cq.Vector(0, 0, 0), (1, 0, 0), angleDegrees=-720)",
    ],
    "polyline": [f"{BASE}.polyline([(0, 0), (1, 1)])", f"{BASE}.polyline([(0, 0), (1, 1), (2, 0)], True, True)"],
    "transformed": [
        f"{BASE}.transformed()",
        f"{BASE}.transformed((0, 90, 0), (1, 2, 3))",
        f"{BASE}.transformed(offset=cq.Vector(0, 0, 1))",
    ],
    "add": [f"{BASE}.add({BASE})"],
}

INVALID_CALLS = {
    "box": [
        ("box(0, 1, 1)", "bad_argument"),
        ("box(-1, 1, 1)", "bad_argument"),
        ("box(1, 1)", "missing_argument"),
        ("box(1, 1, 1, 1)", "bad_argument"),
        ("box(1, 1, 1, (True, True))", "bad_argument"),
        ("box(1, 1, 1, combine='cut')", "bad_argument"),
        ("box(True, 1, 1)", "bad_argument"),
        ("box(1, 1, 20000)", "number_out_of_range"),
        ("box(1, 1, 1, True, True, True, True)", "too_many_arguments"),
        ("box((1, 1, 1))", "bad_argument"),
    ],
    "rect": [
        ("rect(0, 1)", "bad_argument"),
        ("rect(1)", "missing_argument"),
        ("rect(1, 1, (True, True, True))", "bad_argument"),
        ("rect(1, 1, mode='a')", "unknown_keyword"),
    ],
    "circle": [
        ("circle(0)", "bad_argument"),
        ("circle(-2)", "bad_argument"),
        ("circle()", "missing_argument"),
        ("circle('1')", "bad_argument"),
        ("circle(None)", "bad_argument"),
    ],
    "moveTo": [
        ("moveTo((1, 2))", "bad_argument"),
        ("moveTo(1, 2, 3)", "too_many_arguments"),
        ("moveTo(x=True)", "bad_argument"),
    ],
    "lineTo": [
        ("lineTo(1)", "missing_argument"),
        ("lineTo(1, 2, 3)", "bad_argument"),
        ("lineTo(1, y=2, x=3)", "duplicate_argument"),
    ],
    "threePointArc": [
        ("threePointArc(1, 2)", "bad_argument"),
        ("threePointArc((1, 2, 3, 4), (0, 0))", "bad_argument"),
        ("threePointArc((1,), (0, 0))", "bad_argument"),
        ("threePointArc((1, True), (0, 0))", "bad_argument"),
    ],
    "close": [("close(1)", "too_many_arguments"), ("close(forConstruction=True)", "unknown_keyword")],
    "extrude": [
        ("extrude(0)", "bad_argument"),
        ("extrude()", "missing_argument"),
        ("extrude('next')", "bad_argument"),
        ("extrude(1, 'cut')", "bad_argument"),
        ("extrude(1, taper=90)", "bad_argument"),
        ("extrude(1, both=1)", "bad_argument"),
        (f"extrude({BASE})", "bad_argument"),
    ],
    "cutBlind": [
        ("cutBlind(0)", "bad_argument"),
        ("cutBlind('last')", "bad_argument"),
        ("cutBlind(1, taper=-120)", "bad_argument"),
    ],
    "cutThruAll": [
        ("cutThruAll(1)", "bad_argument"),
        ("cutThruAll(taper=None)", "bad_argument"),
        ("cutThruAll(True, 95)", "bad_argument"),
    ],
    "hole": [
        ("hole(0)", "bad_argument"),
        ("hole(1, 0)", "bad_argument"),
        ("hole(1, -3)", "bad_argument"),
        ("hole()", "missing_argument"),
        ("hole(1, 2, True, 4)", "too_many_arguments"),
    ],
    "faces": [
        ("faces(1)", "bad_argument"),
        ("faces('>Z', 'top')", "too_many_arguments"),
        ("faces(tag='top')", "unknown_keyword"),
        ("faces('%Plane')", "bad_argument"),
    ],
    "edges": [
        ("edges(('>Z',))", "bad_item"),
        ("edges(tag='a')", "unknown_keyword"),
        ("edges('>Z[1]')", "bad_argument"),
    ],
    "workplane": [
        ("workplane('XY')", "bad_argument"),
        ("workplane(centerOption='Origin')", "bad_argument"),
        ("workplane(origin=5)", "bad_argument"),
        ("workplane(invert=0)", "bad_argument"),
    ],
    "pushPoints": [
        ("pushPoints([])", "bad_argument"),
        ("pushPoints((1, 2))", "bad_argument"),
        ("pushPoints([(1,)])", "bad_argument"),
        ("pushPoints([(1, 2, 3, 4)])", "bad_argument"),
        ("pushPoints(5)", "bad_argument"),
        ("pushPoints()", "missing_argument"),
    ],
    "fillet": [
        ("fillet(0)", "bad_argument"),
        ("fillet(-1)", "bad_argument"),
        ("fillet()", "missing_argument"),
        ("fillet(1, 2)", "too_many_arguments"),
    ],
    "cut": [
        ("cut(1)", "bad_argument"),
        ("cut()", "missing_argument"),
        ("cut((1, 2, 3))", "bad_argument"),
        (f"cut({BASE}, tol=0.1)", "unknown_keyword"),
    ],
    "union": [
        ("union()", "missing_argument"),
        ("union(None)", "bad_argument"),
        (f"union({BASE}, tol=1)", "unknown_keyword"),
        ("union(cq.Vector(1, 2, 3))", "bad_argument"),
    ],
    "translate": [
        ("translate(1, 2, 3)", "too_many_arguments"),
        ("translate((1,))", "bad_argument"),
        ("translate((1, 2, 3, 4))", "bad_argument"),
        ("translate()", "missing_argument"),
    ],
    "center": [
        ("center(1)", "missing_argument"),
        ("center((1, 2))", "bad_argument"),
        ("center(1, 2, 3)", "too_many_arguments"),
    ],
    "rotate": [
        ("rotate((0, 0, 0), (0, 0, 1))", "missing_argument"),
        ("rotate(90)", "bad_argument"),
        ("rotate((0, 0, 0), (0, 0, 1), (90,))", "bad_argument"),
    ],
    "polyline": [
        ("polyline([(0, 0)])", "bad_argument"),
        ("polyline([])", "bad_argument"),
        ("polyline((0, 0), (1, 1))", "bad_argument"),
        ("polyline([(0, 0), 5])", "bad_argument"),
    ],
    "transformed": [
        ("transformed(90)", "bad_argument"),
        ("transformed(rotate=(1, 2, 3, 4))", "bad_argument"),
        ("transformed(origin=(0, 0, 0))", "unknown_keyword"),
    ],
    "add": [
        ("add(1)", "bad_argument"),
        ("add((0, 0, 0))", "bad_argument"),
        ("add(cq.Vector(1, 2, 3))", "bad_argument"),
        ("add()", "missing_argument"),
    ],
}


def test_every_method_has_valid_and_invalid_cases():
    assert set(VALID_CALLS) == set(INVALID_CALLS) == ENABLED


@pytest.mark.parametrize("expression", [item for name in sorted(VALID_CALLS) for item in VALID_CALLS[name]])
def test_valid_calls_are_accepted(expression):
    accepted(expression)


@pytest.mark.parametrize("call, code", [item for name in sorted(INVALID_CALLS) for item in INVALID_CALLS[name]])
def test_invalid_calls_are_rejected_in_type_checking(call, code):
    error = rejected(f"{BASE}.{call}")
    assert (error.stage, error.code) == ("type", code)


@pytest.mark.parametrize(
    "expression, code",
    [
        ("cq.Workplane('AB').box(1, 1, 1)", "bad_argument"),
        ("cq.Workplane(5).box(1, 1, 1)", "bad_argument"),
        ("cq.Workplane('XY', (0, 0, 0), None).box(1, 1, 1)", "too_many_arguments"),
        ("cq.Workplane('XY', obj=1).box(1, 1, 1)", "unknown_keyword"),
        ("cq.Workplane(cq.Workplane('XY')).box(1, 1, 1)", "bad_argument"),
        ("cq.Workplane('XY').box(1, 1, 1).translate(cq.Vector(1))", "bad_argument"),
        ("cq.Workplane('XY').box(1, 1, 1).translate(cq.Vector(1, 2, 3, 4))", "bad_argument"),
        ("cq.Workplane('XY').box(1, 1, 1).translate(cq.Vector((1, 2, 3)))", "bad_argument"),
        ("cq.Workplane('XY').box(1, 1, 1).translate(cq.Vector(x=1, y=2, z=3))", "bad_argument"),
    ],
)
def test_constructors_are_checked(expression, code):
    error = rejected(expression)
    assert (error.stage, error.code) == ("type", code)


def points(count):
    return "[" + ", ".join(f"({index}, 0)" for index in range(count)) + "]"


def test_point_counts_are_limited():
    accepted(f"{BASE}.pushPoints({points(MAX_POINTS)})")
    accepted(f"{BASE}.polyline({points(MAX_POINTS)})")
    for method in ("pushPoints", "polyline"):
        error = rejected(f"{BASE}.{method}({points(MAX_POINTS + 1)})")
        assert (error.stage, error.code) == ("type", "bad_argument")
        assert "500" in error.message


def test_chain_length_is_limited():
    accepted(BASE + ".faces()" * (MAX_CHAIN - 1))
    error = rejected(BASE + ".faces()" * MAX_CHAIN)
    assert (error.stage, error.code) == ("reduce", "chain_too_long")
    source = HEADER + f"a = {BASE}" + ".faces()" * 50 + "\nresult = a" + ".edges()" * 50 + "\n"
    compile_source(source)
    inline = HEADER + f"result = ({BASE}" + ".faces()" * 50 + ")" + ".edges()" * 50 + "\n"
    with pytest.raises(RcqError) as caught:
        compile_source(inline)
    assert caught.value.code == "chain_too_long"


def test_statement_count_is_limited():
    lines = [f"v{index} = {index}" for index in range(MAX_STATEMENTS - 1)]
    compile_source(HEADER + "\n".join(lines) + f"\nresult = {BASE}\n")
    with pytest.raises(RcqError) as caught:
        compile_source(HEADER + "\n".join(lines) + f"\nextra = 1\nresult = {BASE}\n")
    assert (caught.value.stage, caught.value.code) == ("reduce", "too_many_statements")


def test_total_calls_are_limited():
    full = "a=a" + ".add(a)" * MAX_CHAIN
    body = "a=cq.Workplane('XY')\n" + "\n".join([full] * 24) + "\na=a" + ".add(a)" * (MAX_CHAIN - 1)
    assert 1 + 24 * MAX_CHAIN + MAX_CHAIN - 1 == MAX_CALLS
    compile_source(HEADER + body + "\nresult=a\n")
    with pytest.raises(RcqError) as caught:
        compile_source(HEADER + body + ".add(a)\nresult=a\n")
    assert (caught.value.stage, caught.value.code) == ("reduce", "too_many_calls")
