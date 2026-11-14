import math
import subprocess
import sys

from text2part.envcheck import CHECK_NAMES, build_reference_plate, expected_plate_volume, run_checks


def test_expected_volume_formula():
    assert math.isclose(expected_plate_volume(), 12000 - 180 * math.pi)


def test_reference_plate_is_single_valid_solid():
    part = build_reference_plate()
    assert part.val().isValid()
    assert len(part.solids().vals()) == 1


def test_all_checks_pass_in_order():
    report = run_checks()
    failed = [check for check in report["checks"] if not check["ok"]]
    assert report["ok"], failed
    assert [check["name"] for check in report["checks"]] == list(CHECK_NAMES)


def test_measured_size_and_volume():
    measure = next(check for check in run_checks()["checks"] if check["name"] == "measure")
    assert measure["size"] == [60.0, 40.0, 5.0]
    assert math.isclose(measure["volume"], expected_plate_volume(), abs_tol=1e-3)


def test_cli_exit_code_is_zero():
    result = subprocess.run([sys.executable, "-m", "text2part.envcheck"], capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
