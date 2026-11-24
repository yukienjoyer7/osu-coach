"""Print a blind coaching prompt or its human-review rubric; no model integration required."""
import argparse
import json
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("case", nargs="?", help="case ID; omit to list IDs")
    parser.add_argument("--rubric", action="store_true", help="show criteria for a reviewer")
    args = parser.parse_args()
    cases = json.loads(Path(__file__).with_name("cases.json").read_text(encoding="utf-8"))
    if args.case is None:
        for case in cases:
            print(f"{case['id']} ({case['mode']})")
        return
    case = next((case for case in cases if case["id"] == args.case), None)
    if case is None:
        parser.error(f"unknown case: {args.case}")
    if args.rubric:
        for criterion in case["criteria"]:
            print(f"- {criterion}")
    else:
        print(case["prompt"])


if __name__ == "__main__":
    main()
