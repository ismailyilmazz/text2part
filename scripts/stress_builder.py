import argparse
import collections
import random
import sys
import time

from text2part.core.builder import BuildError, build_reference
from text2part.core.spec import check_proposal, expand_holes, nominal_volume

DEFAULT_RUNS = ((1, 800), (7, 800), (3, 400))
ATTEMPTS_PER_SPEC = 40


def is_valid(data):
    return check_proposal({"spec": data}).status == "VALID"


def crowded_spec(rng):
    length = round(rng.uniform(4, 80), 2)
    width = round(rng.uniform(4, 60), 2)
    height = round(rng.uniform(1.5, 30), 2)
    data = {
        "schema_version": "1.0",
        "units": "mm",
        "family": "block",
        "body": {"length": length, "width": width, "height": height},
        "corner_radius": rng.choice([0, round(rng.uniform(0.1, min(length, width) / 2 - 0.05), 2)]),
        "holes": [],
        "pockets": [],
    }
    if not is_valid(data):
        data["corner_radius"] = 0
    for _ in range(ATTEMPTS_PER_SPEC):
        if rng.random() < 0.35:
            size_x = round(rng.uniform(0.5, length * 0.6), 2)
            size_y = round(rng.uniform(0.5, width * 0.6), 2)
            kind = "pockets"
            item = {
                "center": [
                    round(rng.uniform(-length / 2, length / 2), 2),
                    round(rng.uniform(-width / 2, width / 2), 2),
                ],
                "length": size_x,
                "width": size_y,
                "depth": round(rng.uniform(0.1, height), 2),
                "corner_radius": rng.choice([0, round(rng.uniform(0.1, min(size_x, size_y) / 2), 2)]),
            }
        else:
            kind = "holes"
            diameter = round(rng.uniform(0.5, min(length, width) * 0.5), 2)
            depth = rng.choice(["through", round(rng.uniform(0.1, height), 2)])
            center = [round(rng.uniform(-length / 2, length / 2), 2), round(rng.uniform(-width / 2, width / 2), 2)]
            item = {"diameter": diameter, "depth": depth, "placement": {"kind": "explicit", "centers": [center]}}
        data[kind].append(item)
        if not is_valid(data):
            data[kind].pop()
    return check_proposal({"spec": data}).spec


def run(seed, count):
    rng = random.Random(seed)
    outcomes = collections.Counter()
    features = collections.Counter()
    worst = 0.0
    failures = []
    for _ in range(count):
        spec = crowded_spec(rng)
        features[min(len(expand_holes(spec)) + len(spec.pockets), 9)] += 1
        try:
            solid = build_reference(spec)
            nominal = nominal_volume(spec)
            worst = max(worst, abs(solid.Volume() - nominal) / nominal)
            outcomes["built"] += 1
        except BuildError as error:
            outcomes["BuildError"] += 1
            failures.append((str(error), spec.model_dump_json()))
        except Exception as error:
            outcomes[type(error).__name__] += 1
            failures.append((f"{type(error).__name__}: {error}", spec.model_dump_json()))
    return outcomes, features, worst, failures


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--seed", type=int)
    parser.add_argument("--count", type=int, default=100)
    arguments = parser.parse_args()
    runs = DEFAULT_RUNS if arguments.seed is None else ((arguments.seed, arguments.count),)
    started = time.perf_counter()
    failed = 0
    total = 0
    for seed, count in runs:
        outcomes, features, worst, failures = run(seed, count)
        total += count
        failed += len(failures)
        print(f"seed {seed}, specs {count}: {dict(outcomes)}")
        print(f"  features per spec (9 means 9 or more): {dict(sorted(features.items()))}")
        print(f"  worst relative volume error: {worst:.3g}")
        for message, spec in failures[:3]:
            print("  FAILURE", message[:200], "\n   ", spec[:800])
    print(f"total specs: {total}, failures: {failed}, seconds: {time.perf_counter() - started:.0f}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
