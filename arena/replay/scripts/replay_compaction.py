
#!/usr/bin/env python3
"""Replay full and compacted transcripts against the AME monitor."""

import argparse
import json
import os
import sys
from pathlib import Path

# Make arena/replay/src importable.
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from replay.injection.ame import AmeTarget


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--dataset",
        type=Path,
        default=ROOT / "data/injection/compaction_transcripts.jsonl",
    )
    parser.add_argument(
        "--model",
        default="anthropic/claude-opus-4-8",
    )
    parser.add_argument(
        "--rung",
        default="r1_text",
        choices=[
            "r1_text",
            "r2_jsonl",
            "r3_heredoc",
            "r_esc",
            "r4_escape",
        ],
    )
    parser.add_argument("--reps", type=int, default=1)
    args = parser.parse_args()

    if args.reps < 1:
        parser.error("--reps must be at least 1")

    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise SystemExit("ANTHROPIC_API_KEY is not set")

    rows = [
        json.loads(line)
        for line in args.dataset.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]

    target = AmeTarget(
        rung=args.rung,
        model=args.model,
    )

    try:
        print("Monitor:", target.stamp["monitor"])
        print("Model:", args.model)
        print("Rung:", args.rung)

        for row in rows:
            name = row["metadata"]["source"]
            turns = row["turns"]

            print(f"\n{'=' * 65}")
            print("Transcript:", name)
            print("Turns:", len(turns))
            print("Expected:", row["metadata"].get("expected"))
            print("Final tool call:", turns[-1].get("tool_calls"))
            print(f"{'=' * 65}")

            for i in range(args.reps):
                result = target(turns)

                if result.fault is not None:
                    verdict = "FAULT"
                elif result.released is None:
                    verdict = "NO DECISION"
                else:
                    verdict = "ALLOW" if result.released else "BLOCK"

                print(f"\nRun {i + 1}: {verdict}")
                print("Stages:", result.monitors_ran)
                print("Reason:", result.reason)
                print("Fault:", result.fault)

        print("\nUsage:", target.usage.as_record())
        print("Estimated spend (USD):", target.spend_usd)

    finally:
        target.close()


if __name__ == "__main__":
    main()
