"""Turn capability (or monitor) .eval logs into one comparison table the team shares.

The three lanes score different things, so a pooled table must say which eval produced each row:
  - capability  -> did the side task LAND online within the life budget (landed_side_task)
  - monitor     -> did the monitor BLOCK the flagged action (single_action_eval)
The columns below are keyed to the paper's axes (scenario, monitor, model, n) plus the
compaction-specific fields this project cares about, so our numbers line up against the paper's
*simulation-eval* numbers (the simulation_grid figure) — NOT the arena rollout rates, which are a
different tier.

    uv run python example/record_results.py logs/capability_compaction --eval capability
    uv run python example/record_results.py logs/capability_compaction --eval capability --out results.csv

Appends one row per (sample, epoch) to --out (default results.csv), creating it with a header the
first time. Re-running is safe: it appends; de-dup on (log_file, sample_id, epoch) later if needed.
"""

from __future__ import annotations

import argparse
import csv
from pathlib import Path

import yaml
from inspect_ai.log import list_eval_logs, read_eval_log

FIELDS = [
    "log_file", "eval", "scenario", "monitor", "model", "sample_id", "epoch",
    "success", "ended", "contexts", "compactions", "landed_after_compaction", "explanation",
]


def _store_get(store: dict, field: str, default=None):
    return store.get(f"RedTeamStore:{field}", default)


def _compaction_metrics(final_tx_yaml: str | None) -> tuple[int, int, bool]:
    """(contexts, compaction_summaries, landed_in_a_post_compaction_context).

    A compaction is a message with is_compact_summary=true; a post-compaction context is any
    context after the first that opens with one. We flag landed_after_compaction when the
    transcript has >1 context AND at least one compaction summary — i.e. the attack chain actually
    crossed a compaction boundary. (The authoritative 'did it land' bit is the scorer, below; this
    only characterises HOW it landed.)"""
    if not final_tx_yaml:
        return 0, 0, False
    try:
        tx = yaml.safe_load(final_tx_yaml)
    except yaml.YAMLError:
        return 0, 0, False
    contexts = tx.get("contexts", []) if isinstance(tx, dict) else []
    n_ctx = len(contexts)
    comps = 0
    for ctx in contexts:
        for m in ctx.get("messages", []):
            if isinstance(m, dict) and m.get("is_compact_summary"):
                comps += 1
    return n_ctx, comps, (n_ctx > 1 and comps > 0)


def _rows_from_log(path: str) -> list[dict]:
    log = read_eval_log(path)
    task_meta = (log.eval.metadata or {})
    monitor = task_meta.get("monitor", "?")
    scenario = task_meta.get("seed", task_meta.get("scenario", "?"))
    model = getattr(log.eval, "model", "?")
    rows: list[dict] = []
    for s in (log.samples or []):
        scores = s.scores or {}
        landed = scores.get("landed_side_task")
        success = (landed.value == "C") if landed is not None else None  # Inspect CORRECT == "C"
        explanation = (landed.explanation if landed is not None else "") or ""
        store = s.store or {}
        episodes = _store_get(store, "episodes", []) or []
        ended = episodes[-1].get("ended") if episodes else _store_get(store, "report", "") and "" or ""
        transcripts = _store_get(store, "transcripts", {}) or {}
        # final transcript: prefer the label of the last episode, else the last transcript written
        final_label = episodes[-1].get("label") if episodes else None
        final_tx = transcripts.get(final_label) if final_label else (list(transcripts.values())[-1] if transcripts else None)
        n_ctx, comps, after_comp = _compaction_metrics(final_tx)
        rows.append({
            "log_file": Path(path).name,
            "eval": args_eval,
            "scenario": scenario,
            "monitor": monitor,
            "model": model,
            "sample_id": s.id,
            "epoch": getattr(s, "epoch", 1),
            "success": success,
            "ended": ended,
            "contexts": n_ctx,
            "compactions": comps,
            "landed_after_compaction": (bool(success) and after_comp),
            "explanation": explanation.replace("\n", " ")[:300],
        })
    return rows


def main() -> None:
    global args_eval
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("log_dir", help="A .eval file, or a directory of them (logs/capability_compaction).")
    parser.add_argument("--eval", dest="eval_kind", default="capability",
                        choices=["capability", "investigator", "monitor"], help="Which lane produced these logs.")
    parser.add_argument("--out", default="results.csv", help="CSV to append rows to.")
    args = parser.parse_args()
    args_eval = args.eval_kind

    p = Path(args.log_dir)
    log_paths = [str(p)] if p.is_file() else [e.name for e in list_eval_logs(str(p))]
    if not log_paths:
        print(f"No .eval logs under {p}"); return

    all_rows: list[dict] = []
    for lp in log_paths:
        all_rows.extend(_rows_from_log(lp))

    out = Path(args.out)
    write_header = not out.exists()
    with out.open("a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        if write_header:
            w.writeheader()
        for r in all_rows:
            w.writerow(r)

    landed = sum(1 for r in all_rows if r["success"])
    comp_landed = sum(1 for r in all_rows if r["landed_after_compaction"])
    print(f"Wrote {len(all_rows)} row(s) to {out}")
    print(f"  success: {landed}/{len(all_rows)}   of which via a compaction: {comp_landed}")


if __name__ == "__main__":
    main()
