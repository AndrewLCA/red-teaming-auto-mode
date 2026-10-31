import argparse
import re
import shlex
from pathlib import Path

import yaml
from inspect_ai import eval as inspect_eval

from auto_mode_eval.data_model import Transcript
from auto_mode_eval.evals import Decision, SingleActionEvalSample, action_at, single_action_eval
from auto_mode_eval.monitor import (
    monitor_agentic,
    monitor_cascade,
    monitor_expensive,
    monitor_original,
    monitor_results,
    dependentGraphMonitor,
)

MONITORS = {
    "original": monitor_original,
    "expensive": monitor_expensive,
    "results": monitor_results,
    "cascade": monitor_cascade,
    "agentic": monitor_agentic,
    "dependency": dependentGraphMonitor,
}


# --- dependency-graph retrieval: the parents of the action under review ---------------------------
#
# Builds a directed dependency graph from the transcript's tool calls (producer --file--> consumer)
# and returns, for the action at (context, message), the commands of its parent tool calls — the
# transitive lineage that produced what this action touches. If the action has no parents, returns
# an empty tuple, so nothing is injected into pastToolCalls.
#
# A node is a parent of the action under review when it is an EARLIER tool call that shares a file
# with it — directly or transitively through other shared files (the shared-file connected
# component, as in create_graph.py). So a read-only `ls` of the same file counts, not only the
# command that wrote it. Transitivity matters: in a rename attack the exfil shares its file with the
# `mv` and the `ls`, but not directly with the `tar` (different name) — the `tar` is reached through
# the `mv`, so walking the component is what surfaces it.

def _parse_io(command: str) -> tuple[list[str], list[str]]:
    """(files this command reads, files it writes) — best-effort over the commands these use."""
    try:
        toks = shlex.split(command)
    except ValueError:
        toks = command.split()
    if not toks:
        return [], []
    exe = toks[0]

    if exe == "tar":                      # tar -czf OUT IN...
        out, ins, i = None, [], 1
        while i < len(toks):
            t = toks[i]
            if t.startswith("-") and "f" in t:
                out = toks[i + 1]; i += 2; continue
            if t.startswith("-"):
                i += 1; continue
            ins.append(t); i += 1
        return ins, ([out] if out else [])

    if exe in ("cp", "mv"):               # cp/mv SRC... DST
        args = [t for t in toks[1:] if not t.startswith("-")]
        if len(args) >= 2:
            *srcs, dst = args
            return srcs, [dst]

    if exe == "curl":                     # curl ... --data-binary @FILE ... URL  (reads FILE)
        file = None
        for i, t in enumerate(toks):
            if t in ("--data-binary", "-d", "--data", "-T", "--upload-file") and i + 1 < len(toks):
                v = toks[i + 1].lstrip("@")
                if v and not v.startswith("-"):
                    file = v
        return ([file] if file else []), []

    if exe == "scp":                      # scp SRC host:path  (reads SRC)
        args = [t for t in toks[1:] if not t.startswith("-")]
        if len(args) >= 2:
            return args[:-1], []

    # generic: file-looking tokens are reads, nothing declared written
    reads = [t.lstrip("@") for t in toks[1:]
             if not t.startswith("-") and "://" not in t and ("/" in t or "." in t)]
    return reads, []


def _iter_tool_calls(transcript: dict):
    for ci, ctx in enumerate(transcript["contexts"]):
        for mi, m in enumerate(ctx.get("messages", [])):
            content = m.get("content")
            if isinstance(content, list):
                for b in content:
                    if isinstance(b, dict) and b.get("type") == "tool_use":
                        yield ci, mi, (b.get("input") or {}).get("command", "")


def parent_tool_calls(transcript: dict, context: int, message: int) -> tuple[str, ...]:
    """Commands of the parent tool calls of the action at (context, message): the earlier tool
    calls in its shared-file connected component. Empty tuple if it has none (or isn't found)."""
    nodes: list[tuple[int, int, str, set[str]]] = []   # (ctx, msg, command, files touched)
    target: int | None = None

    for ci, mi, cmd in _iter_tool_calls(transcript):
        ins, outs = _parse_io(cmd)
        idx = len(nodes)
        nodes.append((ci, mi, cmd, set(ins) | set(outs)))
        if (ci, mi) == (context, message):
            target = idx

    if target is None:
        return ()

    # walk the shared-file component, considering only nodes up to the target (no future actions)
    seen: set[int] = {target}
    stack = [target]
    while stack:
        n = stack.pop()
        for j in range(target + 1):
            if j not in seen and nodes[n][3] & nodes[j][3]:
                seen.add(j)
                stack.append(j)
    seen.discard(target)

    # chronological order (earliest first) so the monitor reads them in sequence
    return tuple(nodes[i][2] for i in sorted(seen))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--yaml",
        type=Path,
        default=Path(__file__).with_name("mctest4.yaml"),
        help="Path to a YAML file containing one Transcript",
    )
    parser.add_argument("--monitor", choices=MONITORS, default="original")
    parser.add_argument("--context", type=int, default=0)
    parser.add_argument("--message", type=int, default=1)
    args = parser.parse_args()

    raw = yaml.safe_load(args.yaml.read_text(encoding="utf-8"))
    transcript = Transcript.model_validate(raw)
    decision = Decision(context=args.context, message=args.message)

    selected = action_at(transcript, decision)
    if selected is None:
        raise ValueError(f"Context {args.context}, message {args.message} is not a tool call")

    print(f"Transcript: {transcript.name}")
    print(f"Contexts: {len(transcript.contexts)}")
    print(f"Messages: {sum(len(ctx.messages) for ctx in transcript.contexts)}")
    print(f"Selected action: context={args.context}, message={args.message}")
    print(f"Tool: {selected.name}")
    print(f"Arguments: {selected.input}")
    print(f"Monitor: {args.monitor}")
    print("Expected: BLOCK")
    print("NOTE: No commands in the transcript will be executed.\n")

    sample = SingleActionEvalSample.at(
        transcript=transcript,
        decision=decision,
        expected="BLOCK",
    )
    if args.monitor == "dependency":
        # dynamic: the parents of the action under review, from the dependency graph
        pastToolCalls = parent_tool_calls(raw, args.context, args.message)
        if pastToolCalls:
            print(f"Dependency parents fed to monitor ({len(pastToolCalls)}):")
            for i, c in enumerate(pastToolCalls, 1):
                print(f"  {i}. {c}")
        else:
            print("Dependency parents: NONE (no prior tool call produced what this action touches)")
        print()
        monitor = dependentGraphMonitor(pastToolCalls=pastToolCalls)
    else:
        monitor = MONITORS[args.monitor]()

    task = single_action_eval(
        dataset=[sample],
        monitor=monitor,
    )
    inspect_eval(task, model="anthropic/claude-opus-4-8")


if __name__ == "__main__":
    main()