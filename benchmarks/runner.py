import json
from pathlib import Path


BENCHMARK_FILE = Path(
    "benchmarks/benchmark_suite.json"
)


def load_benchmarks():

    data = json.loads(
        BENCHMARK_FILE.read_text(
            encoding="utf-8"
        )
    )

    return data


def show_benchmarks():

    data = load_benchmarks()

    print("=" * 60)
    print("CODEPILOT-AI BENCHMARK SUITE")
    print("=" * 60)

    print(
        f"\nSuite: {data['benchmark_suite']}"
    )

    print("\nSafety Requirements")
    print("-" * 40)

    requirements = data["global_requirements"]

    for key, value in requirements.items():
        print(f"{key}: {value}")

    print("\nBenchmark Tasks")
    print("-" * 40)

    for task in data["tasks"]:

        print(
            f"\n{task['id']} - {task['name']}"
        )

        print(
            f"Expected result: {task['expected']}"
        )

        print(
            f"Description: {task['description']}"
        )

        print("Validation:")

        for test in task["tests"]:
            print(f"  - {test}")


if __name__ == "__main__":
    show_benchmarks()
