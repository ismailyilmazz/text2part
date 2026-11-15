import json

import pytest

from text2part.rcq.compiler import compile_source
from text2part.rcq.grammar import (
    DEFERRED,
    ENABLED,
    MAX_CALLS,
    SIGNATURES,
    STAGE_A,
    STAGE_B,
    check_program,
    parse_selector,
)
from text2part.rcq.ir import (
    STAGES,
    Assign,
    Call,
    Chain,
    New,
    Program,
    RcqError,
    Ref,
    Vec,
    decode_program,
    encode_program,
)

HEADER = "import cadquery as cq\n"
BOX = 'cq.Workplane("XY").box(10, 10, 10)'


def program(body):
    return compile_source(HEADER + body)


def rejection(source, enabled=ENABLED):
    with pytest.raises(RcqError) as caught:
        compile_source(source, enabled)
    return caught.value


def methods_of(compiled):
    found = []

    def walk(chain):
        for call in chain.calls:
            found.append(call.method)
            for _, value in call.args:
                if isinstance(value, Chain):
                    walk(value)

    for assign in compiled.assigns:
        walk(assign.chain)
    return found


def test_method_stages_match_the_design():
    assert len(STAGE_A) == 20 and len(STAGE_B) == 4 and len(DEFERRED) == 14
    assert set(SIGNATURES) == set(STAGE_A) | set(STAGE_B) == ENABLED
    assert not set(DEFERRED) & ENABLED


def test_golden_programs_compile(golden_rcq):
    assert len(golden_rcq) >= 19
    used = set()
    for name, source in golden_rcq.items():
        compiled = compile_source(source)
        assert compiled.result == "result", name
        used.update(methods_of(compiled))
    assert used == ENABLED


def test_calls_are_bound_to_full_signatures():
    compiled = program(
        'result = cq.Workplane("XY").box(1, 2, 3).faces(">Z").workplane().circle(0.2, True).extrude(-1)\n'
    )
    chain = compiled.assigns[0].chain
    assert chain.base == New("XY", (0, 0, 0))
    assert [call.method for call in chain.calls] == ["box", "faces", "workplane", "circle", "extrude"]
    assert chain.calls[0].args == (
        ("length", 1),
        ("width", 2),
        ("height", 3),
        ("centered", True),
        ("combine", True),
        ("clean", True),
    )
    assert chain.calls[1].args == (("selector", ">Z"),)
    assert chain.calls[3].args == (("radius", 0.2), ("forConstruction", True))
    assert dict(chain.calls[4].args)["until"] == -1
    assert all(call.line == 2 for call in chain.calls)


def test_numbers_are_folded_with_python_semantics():
    compiled = program(
        "a = 7\nb = a / 2 + 3 * -a - (+a)\nc = (a, b)\nresult = cq.Workplane('XY').box(a, c[1] + 100, c[-2] * 2)\n"
    )
    arguments = dict(compiled.assigns[0].chain.calls[0].args)
    assert arguments["length"] == 7 and type(arguments["length"]) is int
    assert arguments["width"] == 7 / 2 + 3 * -7 - 7 + 100
    assert arguments["height"] == 14
    assert len(compiled.assigns) == 1


def test_star_unpacking_and_keywords():
    compiled = program(
        "size = (4, 5, 6)\nresult = cq.Workplane('XY').box(*size, centered=(True, False, True), clean=False)\n"
    )
    arguments = dict(compiled.assigns[0].chain.calls[0].args)
    assert (arguments["length"], arguments["width"], arguments["height"]) == (4, 5, 6)
    assert arguments["centered"] == (True, False, True) and arguments["clean"] is False


def test_workplane_arguments_and_references():
    compiled = program(f"a = {BOX}\nb = a\nresult = a.cut(cq.Workplane('XY').circle(1).extrude(20)).union(b)\n")
    assert [assign.name for assign in compiled.assigns] == ["a", "b", "result"]
    assert compiled.assigns[1].chain == Chain(Ref("a"), ())
    cut, union = compiled.assigns[2].chain.calls
    assert isinstance(dict(cut.args)["toCut"], Chain)
    assert dict(union.args)["toUnion"] == Ref("b")


def test_vector_and_plane_origin():
    compiled = program(
        "v = cq.Vector(1, 2)\nresult = cq.Workplane('front', origin=v).box(1, 1, 1).translate(cq.Vector(0, 0, 3))\n"
    )
    chain = compiled.assigns[0].chain
    assert chain.base == New("front", Vec(1, 2, 0))
    assert dict(chain.calls[1].args)["vec"] == Vec(0, 0, 3)


def test_variables_can_be_reassigned_and_change_type():
    compiled = program(
        f"result = 5\nresult = {BOX}\nresult = result.faces('>Z').workplane().hole(result_size)\n".replace(
            "result_size", "2"
        )
    )
    assert [assign.name for assign in compiled.assigns] == ["result", "result"]
    assert rejection(HEADER + f"result = {BOX}\nresult = 5\n").code == "result_not_workplane"
    assert rejection(HEADER + f"w = {BOX}\nw = 3\nresult = w.faces()\n").code == "not_a_workplane"


def test_x_equals_42_and_result_equals_42_are_rejected():
    first = rejection(HEADER + "x = 42\n")
    second = rejection(HEADER + "result = 42\n")
    assert (first.stage, first.code) == ("type", "no_result")
    assert (second.stage, second.code) == ("type", "result_not_workplane")
    assert rejection(HEADER).code == "no_result"
    assert rejection(HEADER + "result = cq.Vector(1, 2, 3)\n").code == "result_not_workplane"
    assert rejection(HEADER + "result = (1, 2)\n").code == "result_not_workplane"


def test_errors_are_structured():
    error = rejection(HEADER + "a = 1\nresult = cq.Workplane('XY').box(1, 1, 1).sphere(2)\n")
    assert error.as_dict() == {
        "stage": "reduce",
        "line": 3,
        "col": 9,
        "code": "method_not_allowed",
        "message": "The method sphere is not part of the language.",
    }
    assert error.stage in STAGES
    assert set(rejection("x = (").as_dict()) == {"stage", "line", "col", "code", "message"}
    long_name = "a" * 5000
    assert len(rejection(HEADER + f"result = {long_name}\n").message) <= 300


def test_stage_b_methods_are_closed_until_enabled():
    stage_a = frozenset(STAGE_A)
    for name, source in {
        "rotate": f"result = {BOX}.rotate((0, 0, 0), (0, 0, 1), 90)\n",
        "polyline": "result = cq.Workplane('XY').polyline([(0, 0), (1, 0), (0, 1)]).close().extrude(1)\n",
        "transformed": "result = cq.Workplane('XY').transformed(offset=(0, 0, 1)).rect(1, 1).extrude(1)\n",
        "add": f"a = {BOX}\nresult = cq.Workplane('XY').add(a)\n",
    }.items():
        error = rejection(HEADER + source, stage_a)
        assert (error.stage, error.code) == ("reduce", "method_not_enabled"), name
        assert name in methods_of(compile_source(HEADER + source))


def test_stage_a_golden_programs_need_only_stage_a(golden_rcq):
    stage_a = frozenset(STAGE_A)
    for name, source in golden_rcq.items():
        if name < "g20":
            assert set(methods_of(compile_source(source, stage_a))) <= stage_a, name
        else:
            assert rejection(source, stage_a).code == "method_not_enabled", name


@pytest.mark.parametrize("method", DEFERRED)
def test_deferred_methods_are_rejected(method):
    error = rejection(HEADER + f"result = {BOX}.{method}(1)\n")
    assert (error.stage, error.code) == ("reduce", "method_deferred")


def test_enabled_cannot_be_widened_beyond_the_table():
    widened = ENABLED | {"chamfer", "exportStep", "val"}
    assert rejection(HEADER + f"result = {BOX}.chamfer(1)\n", widened).code == "method_deferred"
    assert rejection(HEADER + f"result = {BOX}.val()\n", widened).code == "method_not_allowed"


@pytest.mark.parametrize(
    "text, canonical",
    [
        (">Z", ">Z"),
        ("Z", "Z"),
        ("  |X   or |Y ", "|X or |Y"),
        ("not(>Z)", "not (>Z)"),
        ("(>Z or <Z) and |X", "(>Z or <Z) and |X"),
        ("#Z and not +X or -Y", "#Z and not +X or -Y"),
    ],
)
def test_selectors_are_parsed_and_normalised(text, canonical):
    from cadquery.selectors import StringSyntaxSelector

    assert parse_selector(text) == canonical
    StringSyntaxSelector(canonical)


@pytest.mark.parametrize(
    "text",
    [
        "",
        ">",
        ">>Z",
        ">W",
        ">Z[1]",
        "%Plane",
        ">Z or",
        "(>Z",
        ">Z)",
        "and",
        "not",
        ">Z <Z",
        "(1, 0, 0)",
        ">z",
        ">Z;",
        ">Z" * 17,
    ],
)
def test_bad_selectors_are_rejected(text):
    with pytest.raises(ValueError):
        parse_selector(text)
    error = rejection(HEADER + f"result = {BOX}.faces({text!r})\n")
    assert (error.stage, error.code) in {("type", "bad_argument"), ("type", "bad_string")}


def test_program_round_trips_through_json(golden_rcq):
    for name, source in golden_rcq.items():
        compiled = compile_source(source)
        encoded = json.dumps(encode_program(compiled))
        assert decode_program(json.loads(encoded)) == compiled, name


def tampered():
    good = Call(
        "box", (("length", 1), ("width", 1), ("height", 1), ("centered", True), ("combine", True), ("clean", True))
    )
    new = New("XY", (0, 0, 0))

    def single(call, base=new):
        return Program((Assign("result", Chain(base, (call,))),))

    return {
        "unknown method": single(Call("exportStep", (("fileName", "x"),))),
        "deferred method": single(Call("chamfer", (("length", 1),))),
        "private method": single(Call("_findType", ())),
        "missing argument": single(Call("box", good.args[:5])),
        "extra argument": single(Call("box", good.args + (("tol", 1),))),
        "renamed argument": single(Call("box", (("obj", 1),) + good.args[1:])),
        "wrong type": single(Call("box", (("length", "1"),) + good.args[1:])),
        "bool as number": single(Call("box", (("length", True),) + good.args[1:])),
        "out of range": single(Call("box", (("length", 1e9),) + good.args[1:])),
        "not canonical selector": single(Call("faces", (("selector", " >Z"),))),
        "raw selector": single(Call("faces", (("selector", ">Z[1]"),))),
        "bad plane": single(good, New("../etc", (0, 0, 0))),
        "bad origin": single(good, New("XY", "origin")),
        "undefined reference": single(good, Ref("other")),
        "undefined argument": single(Call("cut", (("toCut", Ref("other")), ("clean", True)))),
        "no result": Program((Assign("part", Chain(new, (good,))),)),
        "bad name": Program((Assign("__class__", Chain(new, (good,))), Assign("result", Chain(new, (good,))))),
        "module name": Program((Assign("cq", Chain(new, (good,))), Assign("result", Chain(new, (good,))))),
        "too many calls": Program(
            tuple(Assign("result", Chain(new, (good,) * 70)) for _ in range(MAX_CALLS // 70 + 1))
        ),
        "too long chain": single(good, new).__class__((Assign("result", Chain(new, (good,) * 81)),)),
        "not a program": "result = 1",
    }


@pytest.mark.parametrize("label", sorted(tampered()))
def test_malformed_intermediate_representation_is_rejected(label):
    with pytest.raises(RcqError) as caught:
        check_program(tampered()[label])
    assert (caught.value.stage, caught.value.code) == ("execute", "malformed_program")


@pytest.mark.parametrize(
    "data",
    [
        None,
        [],
        {"result": "result"},
        {"result": "result", "assigns": {}},
        {"result": 1, "assigns": []},
        {"result": "result", "assigns": [{"name": "result", "line": 1}]},
        {"result": "result", "assigns": [{"name": "result", "line": 1, "chain": {"base": {"eval": 1}, "calls": []}}]},
        {
            "result": "result",
            "assigns": [{"name": "result", "line": "1", "chain": {"base": {"ref": "a"}, "calls": []}}],
        },
        {
            "result": "result",
            "assigns": [{"name": "result", "line": 1, "chain": {"base": {"ref": "a"}, "calls": [{}]}}],
        },
    ],
)
def test_malformed_json_is_rejected(data):
    with pytest.raises(RcqError) as caught:
        decode_program(data)
    assert caught.value.code == "malformed_program"
