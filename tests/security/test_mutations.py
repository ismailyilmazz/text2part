import json

from scripts.stress_rcq import DEFAULT_COUNT, DEFAULT_SEED, EXECUTION_CODES, classify, mutants
from text2part.rcq.executor import Execution, Limits, run_program
from text2part.rcq.grammar import ENABLED, check_program
from text2part.rcq.ir import STAGES, Chain, RcqError, decode_program, encode_program

SAMPLE = 6


def methods_of(program):
    found = []

    def walk(chain):
        for call in chain.calls:
            found.append(call.method)
            for _, value in call.args:
                if isinstance(value, Chain):
                    walk(value)

    for assign in program.assigns:
        walk(assign.chain)
    return found


def survey():
    stages = {}
    accepted = {}
    for source in mutants(DEFAULT_COUNT, DEFAULT_SEED):
        stage, outcome = classify(source)
        stages[stage] = stages.get(stage, 0) + 1
        if stage == "accepted":
            accepted.setdefault(source, outcome)
        else:
            assert isinstance(outcome, RcqError)
            assert set(outcome.as_dict()) == {"stage", "line", "col", "code", "message"}
            assert len(outcome.message) <= 300
    return stages, accepted


def test_mutants_are_reproducible():
    assert list(mutants(50, 7)) == list(mutants(50, 7))
    assert list(mutants(50, 7)) != list(mutants(50, 8))


def test_a_thousand_mutants_never_crash_the_compiler():
    assert DEFAULT_COUNT >= 1000
    stages, accepted = survey()
    assert sum(stages.values()) == DEFAULT_COUNT
    assert set(stages) <= set(STAGES) | {"accepted"}
    assert {"source", "parse", "reduce", "type"} <= set(stages)
    assert stages["accepted"] >= 30
    assert DEFAULT_COUNT - stages["accepted"] >= 700
    for program in accepted.values():
        check_program(program)
        assert set(methods_of(program)) <= ENABLED
        assert decode_program(json.loads(json.dumps(encode_program(program)))) == program


def test_accepted_mutants_run_in_the_executor_without_hurting_the_parent():
    _, accepted = survey()
    programs = list(accepted.values())
    step = max(1, len(programs) // SAMPLE)
    for program in programs[::step][:SAMPLE]:
        execution = run_program(program, Limits(wall_seconds=120.0, cpu_seconds=15))
        assert isinstance(execution, Execution)
        if not execution.ok:
            assert execution.error.stage == "execute"
            assert execution.error.code in EXECUTION_CODES
