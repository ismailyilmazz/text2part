import pytest

from text2part.rcq.compiler import compile_source
from text2part.rcq.grammar import MAX_SOURCE_BYTES
from text2part.rcq.ir import RcqError

H = "import cadquery as cq\n"
BOX = "cq.Workplane('XY').box(1, 1, 1)"
R = f"result = {BOX}\n"

SCENARIOS = [
    ("import-os", "import", "import os\n" + R, "reduce", "bad_import"),
    ("import-exporters", "import", "from cadquery import exporters\n" + R, "reduce", "bad_import"),
    (
        "import-other-alias",
        "import",
        "import cadquery as c\nresult = c.Workplane('XY').box(1, 1, 1)\n",
        "reduce",
        "bad_import",
    ),
    ("import-no-alias", "import", "import cadquery\n" + R, "reduce", "bad_import"),
    ("import-two-modules", "import", "import cadquery as cq, os\n" + R, "reduce", "bad_import"),
    ("import-missing", "import", R, "reduce", "bad_import"),
    ("import-second-statement", "import", H + "import os\n" + R, "reduce", "statement_not_allowed"),
    ("import-after-docstring", "import", '"""doc"""\n' + H + R, "reduce", "bad_import"),
    ("import-submodule", "import", "import cadquery.exporters as cq\n" + R, "reduce", "bad_import"),
    ("builtin-eval", "builtin name", H + "result = eval('1')\n", "reduce", "not_a_method_call"),
    ("builtin-eval-name", "builtin name", H + "f = eval\n" + R, "reduce", "undefined_name"),
    ("builtin-open", "builtin name", H + "f = (open)\n" + R, "reduce", "undefined_name"),
    ("builtin-getattr", "builtin name", H + "g = getattr\n" + R, "reduce", "undefined_name"),
    ("builtin-builtins", "builtin name", H + "b = __builtins__\n" + R, "reduce", "undefined_name"),
    ("builtin-import", "builtin name", H + "i = __import__\n" + R, "reduce", "undefined_name"),
    ("builtin-in-argument", "builtin name", H + f"result = {BOX}.translate(globals)\n", "reduce", "undefined_name"),
    ("builtin-in-list", "builtin name", H + "x = [print, 1]\n" + R, "reduce", "undefined_name"),
    ("module-as-value", "builtin name", H + "m = cq\n" + R, "reduce", "module_not_a_value"),
    ("module-in-argument", "builtin name", H + f"result = {BOX}.add(cq)\n", "reduce", "module_not_a_value"),
    ("indirect-eval", "indirect call", H + "result = [eval][0]('1+1')\n", "reduce", "not_a_method_call"),
    ("indirect-lambda", "indirect call", H + "result = (lambda: 0)()\n", "reduce", "not_a_method_call"),
    ("indirect-call-of-call", "indirect call", H + f"x = {BOX}\nresult = x()(1)\n", "reduce", "not_a_method_call"),
    ("indirect-name-call", "indirect call", H + f"x = {BOX}\nresult = x(1)\n", "reduce", "not_a_method_call"),
    (
        "indirect-import-system",
        "indirect call",
        H + "result = __import__('os').system('id')\n",
        "reduce",
        "method_not_allowed",
    ),
    (
        "indirect-open-read",
        "indirect call",
        H + "result = open('/etc/passwd').read()\n",
        "reduce",
        "method_not_allowed",
    ),
    (
        "attribute-exporters",
        "attribute",
        H + R + "cq.exporters.export(result, 'out.step')\n",
        "reduce",
        "statement_not_allowed",
    ),
    ("attribute-exporters-value", "attribute", H + "e = cq.exporters\n" + R, "reduce", "attribute_not_allowed"),
    (
        "attribute-exporters-call",
        "attribute",
        H + R + "x = cq.exporters.export(result, 'out.step')\n",
        "reduce",
        "method_not_allowed",
    ),
    ("attribute-occ-impl", "attribute", H + "x = cq.occ_impl.shapes.Shape\n" + R, "reduce", "attribute_not_allowed"),
    (
        "attribute-importers",
        "attribute",
        H + "result = cq.importers.importStep('/etc/passwd')\n",
        "reduce",
        "method_not_allowed",
    ),
    (
        "attribute-export-step",
        "attribute",
        H + f"result = {BOX}.exportStep('out.step')\n",
        "reduce",
        "method_not_allowed",
    ),
    ("attribute-val", "attribute", H + f"result = {BOX}.val()\n", "reduce", "method_not_allowed"),
    ("attribute-each", "attribute", H + f"result = {BOX}.each(print)\n", "reduce", "method_not_allowed"),
    ("attribute-dunder-class", "attribute", H + f"result = {BOX}.__class__\n", "reduce", "attribute_not_allowed"),
    (
        "attribute-dunder-call",
        "attribute",
        H + f"result = {BOX}.__getattribute__('exportStep')\n",
        "reduce",
        "attribute_not_allowed",
    ),
    ("attribute-private", "attribute", H + f"result = {BOX}._findType(1)\n", "reduce", "attribute_not_allowed"),
    ("attribute-ctx", "attribute", H + f"x = {BOX}.ctx\n" + R, "reduce", "attribute_not_allowed"),
    ("attribute-on-number", "attribute", H + "x = (1).real\n" + R, "reduce", "attribute_not_allowed"),
    ("attribute-constructor-sketch", "attribute", H + "result = cq.Sketch()\n", "reduce", "attribute_not_allowed"),
    ("attribute-constructor-assembly", "attribute", H + "result = cq.Assembly()\n", "reduce", "attribute_not_allowed"),
    (
        "attribute-workplane-classmethod",
        "attribute",
        H + "result = cq.Workplane.box(1, 1, 1)\n",
        "reduce",
        "attribute_not_allowed",
    ),
    ("attribute-assignment", "attribute", H + R + "result.ctx = 1\n", "reduce", "bad_target"),
    ("flow-while", "control flow", H + R + "while True:\n    pass\n", "reduce", "statement_not_allowed"),
    ("flow-for", "control flow", H + R + "for i in (1, 2):\n    x = i\n", "reduce", "statement_not_allowed"),
    ("flow-if", "control flow", H + R + "if 1:\n    x = 1\n", "reduce", "statement_not_allowed"),
    (
        "flow-try",
        "control flow",
        H + R + "try:\n    x = 1\nexcept Exception:\n    x = 2\n",
        "reduce",
        "statement_not_allowed",
    ),
    ("flow-with", "control flow", H + R + "with open('f', 'w') as f:\n    x = 1\n", "reduce", "statement_not_allowed"),
    ("flow-def", "control flow", H + R + "def f():\n    return 1\n", "reduce", "statement_not_allowed"),
    ("flow-class", "control flow", H + R + "class A:\n    x = 1\n", "reduce", "statement_not_allowed"),
    ("flow-lambda", "control flow", H + "f = lambda: 1\n" + R, "reduce", "expression_not_allowed"),
    ("flow-comprehension", "control flow", H + "x = [i for i in (1, 2)]\n" + R, "reduce", "expression_not_allowed"),
    ("flow-generator", "control flow", H + "x = (i for i in (1, 2))\n" + R, "reduce", "expression_not_allowed"),
    ("flow-conditional", "control flow", H + "x = 1 if 2 else 3\n" + R, "reduce", "expression_not_allowed"),
    ("flow-walrus", "control flow", H + "x = (y := 5)\n" + R, "reduce", "expression_not_allowed"),
    ("flow-expression-statement", "control flow", H + R + "result\n", "reduce", "statement_not_allowed"),
    ("flow-show-object", "control flow", H + R + "show_object(result)\n", "reduce", "statement_not_allowed"),
    ("flow-augmented", "control flow", H + "x = 1\nx += 1\n" + R, "reduce", "statement_not_allowed"),
    ("flow-annotated", "control flow", H + "x: int = 1\n" + R, "reduce", "statement_not_allowed"),
    ("flow-delete", "control flow", H + R + "del result\n", "reduce", "statement_not_allowed"),
    ("flow-global", "control flow", H + "global cq\n" + R, "reduce", "statement_not_allowed"),
    ("flow-tuple-target", "control flow", H + "a, b = 1, 2\n" + R, "reduce", "bad_target"),
    ("flow-chained-target", "control flow", H + "a = b = 1\n" + R, "reduce", "bad_target"),
    ("flow-subscript-target", "control flow", H + "a = (1, 2)\na[0] = 3\n" + R, "reduce", "bad_target"),
    ("flow-fstring", "control flow", H + "x = f'{1}'\n" + R, "reduce", "expression_not_allowed"),
    ("flow-dict", "control flow", H + "x = {'a': 1}\n" + R, "reduce", "expression_not_allowed"),
    ("flow-set", "control flow", H + "x = {1, 2}\n" + R, "reduce", "expression_not_allowed"),
    ("flow-slice", "control flow", H + "a = (1, 2, 3)\nx = a[0:2]\n" + R, "reduce", "bad_index"),
    ("flow-star-in-list", "control flow", H + "a = (1, 2)\nx = [*a, 3]\n" + R, "reduce", "expression_not_allowed"),
    ("flow-await", "control flow", H + "x = await y\n" + R, "reduce", "expression_not_allowed"),
    ("operator-power", "operator", H + "x = 9 ** 9 ** 9\n" + R, "reduce", "operator_not_allowed"),
    ("operator-floor-division", "operator", H + "x = 7 // 2\n" + R, "reduce", "operator_not_allowed"),
    ("operator-modulo", "operator", H + "x = 7 % 2\n" + R, "reduce", "operator_not_allowed"),
    ("operator-shift", "operator", H + "x = 1 << 4000\n" + R, "reduce", "operator_not_allowed"),
    ("operator-matmul", "operator", H + "x = 1 @ 2\n" + R, "reduce", "operator_not_allowed"),
    ("operator-bitor", "operator", H + "x = 1 | 2\n" + R, "reduce", "operator_not_allowed"),
    ("operator-compare", "operator", H + "x = 1 < 2\n" + R, "reduce", "operator_not_allowed"),
    ("operator-boolean", "operator", H + "x = 1 and 2\n" + R, "reduce", "operator_not_allowed"),
    ("operator-not", "operator", H + "x = not 1\n" + R, "reduce", "operator_not_allowed"),
    ("operator-invert", "operator", H + "x = ~1\n" + R, "reduce", "operator_not_allowed"),
    ("operator-list-repeat", "operator", H + "x = [0] * 5000\n" + R, "type", "bad_operand"),
    ("operator-string-repeat", "operator", H + f"result = {BOX}.faces('>Z' * 9)\n", "type", "bad_operand"),
    ("operator-tuple-concat", "operator", H + "x = (1, 2) + (3, 4)\n" + R, "type", "bad_operand"),
    ("operator-bool-arithmetic", "operator", H + "x = True + 1\n" + R, "type", "bad_operand"),
    ("operator-workplane-arithmetic", "operator", H + f"a = {BOX}\nresult = a + a\n", "type", "bad_operand"),
    ("number-infinity", "number", H + "x = 1e309\n" + R, "type", "number_out_of_range"),
    ("number-float-inf", "number", H + "x = float('inf')\n" + R, "reduce", "not_a_method_call"),
    ("number-too-large", "number", H + "x = 10001\n" + R, "type", "number_out_of_range"),
    ("number-huge-integer", "number", H + "x = " + "9" * 400 + "\n" + R, "type", "number_out_of_range"),
    ("number-unparsable-integer", "number", H + "x = " + "9" * 5000 + "\n" + R, "parse", "syntax_error"),
    ("number-folded-too-large", "number", H + "x = 9999 * 9999\n" + R, "fold", "number_out_of_range"),
    ("number-division-by-zero", "number", H + "x = 1 / 0\n" + R, "fold", "division_by_zero"),
    ("number-complex", "number", H + "x = 1j\n" + R, "reduce", "constant_not_allowed"),
    ("number-bytes", "number", H + "x = b'abc'\n" + R, "reduce", "constant_not_allowed"),
    ("number-ellipsis", "number", H + "x = ...\n" + R, "reduce", "constant_not_allowed"),
    ("size-source", "size", H + "x = 1\n" * 4000 + R, "source", "source_too_large"),
    ("size-statements", "size", H + "x = 1\n" * 151 + R, "reduce", "too_many_statements"),
    ("size-chain", "size", H + f"result = {BOX}" + ".faces()" * 81 + "\n", "reduce", "chain_too_long"),
    ("size-list", "size", H + "x = [" + "1," * 513 + "]\n" + R, "reduce", "sequence_too_long"),
    ("size-list-nesting", "size", H + "x = [[[1]]]\n" + R, "reduce", "too_nested"),
    ("size-not-text", "size", b"import cadquery as cq", "source", "not_text"),
    ("nesting-parentheses", "nesting", H + "x = " + "(" * 300 + "1" + ")" * 300 + "\n" + R, "parse", None),
    ("nesting-unary", "nesting", H + "x = " + "-" * 60 + "1\n" + R, "reduce", "too_deep"),
    ("nesting-binary", "nesting", H + "x = " + "1 + " * 60 + "1\n" + R, "reduce", "too_deep"),
    (
        "nesting-inline-workplanes",
        "nesting",
        H + "result = " + f"{BOX}.cut(" * 45 + BOX + ")" * 45 + "\n",
        "reduce",
        "too_deep",
    ),
    ("nesting-lists", "nesting", H + "x = " + "[" * 100 + "]" * 100 + "\n" + R, "reduce", None),
    ("string-long", "string", H + f"result = {BOX}.faces('" + ">Z or " * 8 + ">Z')\n", "type", "bad_string"),
    ("string-selector-syntax", "string", H + f"result = {BOX}.faces('>Z; import os')\n", "type", "bad_argument"),
    ("string-selector-index", "string", H + f"result = {BOX}.faces('>Z[-2]')\n", "type", "bad_argument"),
    ("string-plane-path", "string", H + "result = cq.Workplane('/etc/passwd').box(1, 1, 1)\n", "type", "bad_argument"),
    ("string-as-variable", "string", H + "s = '>Z'\n" + R, "type", "bad_assignment"),
    ("string-as-number", "string", H + "result = cq.Workplane('XY').box('1', 1, 1)\n", "type", "bad_argument"),
    ("string-in-list", "string", H + "x = ['a', 'b']\n" + R, "type", "bad_item"),
    ("name-unicode-lookalike", "name", H + "r\u0435sult = 1\n" + R, "source", "bad_character"),
    ("name-fullwidth-module", "name", "import cadquery as \uff43\uff51\n" + R, "source", "bad_character"),
    ("name-nul-byte", "name", H + "x = 1\x00\n" + R, "source", "bad_character"),
    ("name-form-feed", "name", H + "x = 1\x0c\n" + R, "source", "bad_character"),
    ("name-escaped-unicode-in-string", "name", H + f"result = {BOX}.faces('>Z\\u0435')\n", "type", "bad_string"),
    ("name-escaped-newline-in-string", "name", H + f"result = {BOX}.faces('>Z\\nimport os')\n", "type", "bad_argument"),
    ("name-reassign-module", "name", H + "cq = 1\n" + R, "reduce", "bad_target"),
    ("name-underscore", "name", H + "_x = 1\n" + R, "reduce", "bad_target"),
    ("name-dunder", "name", H + "__builtins__ = 1\n" + R, "reduce", "bad_target"),
    ("name-undefined", "name", H + "result = part.faces()\n", "reduce", "undefined_name"),
    ("name-used-before-assignment", "name", H + "result = result.faces()\n", "reduce", "undefined_name"),
    (
        "argument-double-star",
        "argument",
        H + f"result = {BOX}.translate(**{{'vec': (1, 2, 3)}})\n",
        "type",
        "kwargs_not_allowed",
    ),
    ("argument-star-literal", "argument", H + "result = cq.Workplane('XY').box(*(1, 1, 1))\n", "type", "bad_star"),
    ("argument-star-number", "argument", H + "n = 3\nresult = cq.Workplane('XY').box(*n)\n", "type", "bad_star"),
    ("argument-star-workplane", "argument", H + f"a = {BOX}\nresult = a.cut(*a)\n", "type", "bad_star"),
    ("argument-unknown-keyword", "argument", H + f"result = {BOX}.extrude(1, until2=3)\n", "type", "unknown_keyword"),
    ("argument-duplicate", "argument", H + f"result = {BOX}.circle(1, radius=2)\n", "type", "duplicate_argument"),
    ("argument-workplane-in-list", "argument", H + f"a = {BOX}\nx = [a]\n" + R, "type", "bad_item"),
    ("argument-workplane-as-number", "argument", H + f"a = {BOX}\nresult = a.circle(a)\n", "type", "bad_argument"),
    ("argument-method-on-number", "argument", H + "n = 3\nresult = n.box(1, 1, 1)\n", "type", "not_a_workplane"),
    ("argument-method-on-list", "argument", H + "n = (1, 2)\nresult = n.faces()\n", "type", "not_a_workplane"),
    ("argument-index-out-of-range", "argument", H + "n = (1, 2)\nx = n[5]\n" + R, "type", "bad_index"),
    ("argument-index-on-workplane", "argument", H + f"a = {BOX}\nx = a[0]\n" + R, "type", "bad_index"),
    ("argument-index-variable", "argument", H + "n = (1, 2)\ni = 0\nx = n[i]\n" + R, "reduce", "bad_index"),
    (
        "resource-points-over-list-limit",
        "resource",
        H + f"result = {BOX}.pushPoints([" + "(1,1)," * 600 + "])\n",
        "reduce",
        "sequence_too_long",
    ),
    (
        "resource-points-over-point-limit",
        "resource",
        H + f"result = {BOX}.pushPoints([" + "(1,1)," * 505 + "])\n",
        "type",
        "bad_argument",
    ),
    (
        "resource-points-do-not-fit",
        "resource",
        H + f"result = {BOX}.pushPoints([" + "(1,1)," * 100000 + "])\n",
        "source",
        "source_too_large",
    ),
    ("deferred-rarray", "deferred method", H + f"result = {BOX}.rarray(1, 1, 100, 100)\n", "reduce", "method_deferred"),
    (
        "deferred-polar-array",
        "deferred method",
        H + f"result = {BOX}.polarArray(1, 0, 360, 1000)\n",
        "reduce",
        "method_deferred",
    ),
    (
        "deferred-cylinder",
        "deferred method",
        H + "result = cq.Workplane('XY').cylinder(1, 1)\n",
        "reduce",
        "method_deferred",
    ),
    ("deferred-chamfer", "deferred method", H + f"result = {BOX}.edges().chamfer(0.1)\n", "reduce", "method_deferred"),
    (
        "deferred-inside-argument",
        "deferred method",
        H + f"result = {BOX}.cut(cq.Workplane('XY').polygon(6, 1).extrude(1))\n",
        "reduce",
        "method_deferred",
    ),
    ("syntax-error", "syntax", H + "result = (\n", "parse", "syntax_error"),
    ("syntax-indentation", "syntax", H + "  result = 1\n", "parse", "syntax_error"),
    ("syntax-python2-print", "syntax", H + "print 1\n", "parse", "syntax_error"),
    ("syntax-backslash-continuation", "syntax", H + "result = \\\n", "parse", "syntax_error"),
    ("empty-source", "syntax", "", "reduce", "bad_import"),
    ("comment-only", "syntax", "# import cadquery as cq\n", "reduce", "bad_import"),
]
CLASSES = sorted({item[1] for item in SCENARIOS})


def test_there_are_enough_scenarios_and_classes():
    assert len(SCENARIOS) >= 40
    assert len({item[0] for item in SCENARIOS}) == len(SCENARIOS)
    assert set(CLASSES) >= {
        "import",
        "builtin name",
        "indirect call",
        "attribute",
        "control flow",
        "operator",
        "number",
        "size",
        "nesting",
        "string",
        "name",
        "argument",
        "resource",
        "deferred method",
    }


@pytest.mark.parametrize("label, group, source, stage, code", SCENARIOS, ids=[item[0] for item in SCENARIOS])
def test_scenario_is_rejected_at_the_expected_stage(label, group, source, stage, code):
    with pytest.raises(RcqError) as caught:
        compile_source(source)
    assert caught.value.stage == stage, caught.value.as_dict()
    if code is not None:
        assert caught.value.code == code, caught.value.as_dict()


def test_size_limit_is_checked_before_parsing():
    source = "(" * (MAX_SOURCE_BYTES + 1)
    with pytest.raises(RcqError) as caught:
        compile_source(source)
    assert (caught.value.stage, caught.value.code) == ("source", "source_too_large")


def test_largest_allowed_source_still_compiles():
    padding = "#" + "a" * 98 + "\n"
    source = H + R
    source += padding * ((MAX_SOURCE_BYTES - len(source)) // len(padding))
    assert MAX_SOURCE_BYTES - 100 < len(source) <= MAX_SOURCE_BYTES
    compile_source(source)


def test_comments_cannot_carry_code():
    compiled = compile_source(H + "# import os; os.system('id')\n" + R + "# result = eval('1')\n")
    assert len(compiled.assigns) == 1


def test_string_escapes_are_resolved_before_validation():
    compiled = compile_source(H + f"result = {BOX}.faces('\\x3eZ')\n")
    assert compiled.assigns[0].chain.calls[-1].args == (("selector", ">Z"),)


EXTREME = {
    "cubic list nesting": H + "a = [" + "1," * 512 + "]\nb = [" + "a," * 512 + "]\nc = [" + "b," * 512 + "]\n" + R,
    "star flood": H + "a = [" + "1," * 512 + "]\nresult = cq.Workplane('XY').box(" + "*a," * 3000 + ")\n",
    "unary chain": H + "x = " + "-" * 8000 + "1\n" + R,
    "nested lists": H + "x = " + "[" * 5000 + "]" * 5000 + "\n" + R,
    "nested calls": H + "x = " + "f(" * 2000 + ")" * 2000 + "\n" + R,
    "attribute chain": H + "x = a" + ".b" * 3000 + "\n" + R,
    "method chain": H + "result = cq.Workplane('XY')" + ".close()" * 2000 + "\n",
    "long sum": H + "x = " + "1+" * 5000 + "1\n" + R,
    "subscript chain": H + "x = a" + "[0]" * 3000 + "\n" + R,
    "nested lambdas": H + "x = " + "lambda: " * 1500 + "1\n" + R,
    "nested conditionals": H + "x = " + "1 if 1 else " * 1200 + "1\n" + R,
    "not chain": H + "x = " + "not " * 3000 + "1\n" + R,
    "nested dicts": H + "x = " + "{1:" * 3000 + "1" + "}" * 3000 + "\n" + R,
    "nested format strings": H + "x = f'" + "{" * 12 + "1" + "}" * 12 + "'\n" + R,
    "only newlines": "\n" * 16000,
    "only parentheses": "(" * 16000,
    "only backslashes": "\\\n" * 8000,
    "long comment": H + "#" * 17000 + "\n" + R,
}


@pytest.mark.parametrize("label", sorted(EXTREME))
def test_extreme_inputs_are_rejected_without_crashing(label):
    with pytest.raises(RcqError) as caught:
        compile_source(EXTREME[label])
    assert caught.value.stage in ("source", "parse", "reduce", "type")


def test_repeated_large_lists_stay_cheap():
    source = H + "a = [" + "1," * 512 + "]\n" + ("b = [" + "a," * 512 + "]\n") * 12 + R
    assert len(source) <= MAX_SOURCE_BYTES
    assert len(compile_source(source).assigns) == 1
