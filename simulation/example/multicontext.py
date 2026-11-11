
import argparse
import json
from pathlib import Path

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
from auto_mode_eval.monitor.dependencyGraph.dependency_graph import (
    extractFiles,
    inferRoot,
    normalizeFile,
    extractAllToolNodes,
    buildGraph,
)

MONITORS = {
    "original": monitor_original,
    "expensive": monitor_expensive,
    "results": monitor_results,
    "cascade": monitor_cascade,
    "agentic": monitor_agentic,
    "dependency": dependentGraphMonitor,
}


def loadPostcompaction(transcriptPath):
    """Return monitor-ready messages and indices of post-compaction tool calls."""
    messages = []
    actions = []
    Boundary = None
    summaryFound = False

    with transcriptPath.open("r", encoding="utf-8-sig") as f:
        for lineNum, line in enumerate(f, 1):
            if not line.strip():
                continue

            record = json.loads(line)

            if Boundary is None:
                if (
                    record.get("type") == "system"
                    and record.get("subtype") == "compact_boundary"
                ):
                    Boundary = lineNum
                continue

            recordType = record.get("type")
            if recordType not in ("assistant", "user"):
                continue

            message = record.get("message") or {}
            if not isinstance(message, dict):
                continue

            content = message.get("content", [])

            if isinstance(content, str):
                blocks = [{"type": "text", "text": content}]

            elif isinstance(content, list):
                blocks = []

                for block in content:
                    if not isinstance(block, dict):
                        continue

                    kind = block.get("type")

                    if kind == "text":
                        blocks.append({
                            "type": "text",
                            "text": block.get("text", "")
                        })

                    elif kind == "tool_use" and recordType == "assistant":
                        blocks.append({
                            "type": "tool_use",
                            "id": block.get("id", ""),
                            "name": block.get("name", ""),
                            "input": block.get("input") or {},
                        })

                    elif kind == "tool_result" and recordType == "user":
                        resultContent = block.get("content", "")

                        if not isinstance(resultContent, str):
                            resultContent = json.dumps(
                                resultContent,
                                ensure_ascii=False
                            )

                        blocks.append({
                            "type": "tool_result",
                            "tool_use_id": block.get("tool_use_id", ""),
                            "content": resultContent,
                            "is_error": block.get("is_error", False),
                        })
            else:
                continue

            if not blocks:
                continue

            isSummary = bool(
                record.get("isCompactSummary")
                or message.get("isCompactSummary")
            )

            if isSummary:
                summaryFound = True

                messages.append({
                    "role": "user",
                    "content": blocks,
                    "is_compact_summary": True
                })
                continue

            if not summaryFound:
                continue

            for block in blocks:
                if block["type"] == "tool_use":
                    messages.append({
                        "role": "assistant",
                        "content": [block]
                    })
                    actions.append(len(messages) - 1)

                else:
                    messages.append({
                        "role": recordType,
                        "content": [block]
                    })

    if Boundary is None:
        raise ValueError("No compaction boundary found")

    if not summaryFound:
        raise ValueError(
            "No isCompactSummary record found after compaction boundary"
        )

    if not actions:
        raise ValueError("No post-compaction tool calls found")

    return messages, actions, Boundary


def parent_tool_calls(graph, rawNodes, selected):
    """Find historical same-file nodes and walk directed parent edges.

    graph is {"nodes": [...], "edges": [...]} and rawNodes is the matching tool-node list — either
    loaded from the *_dependency_graph.json / *_tool_nodes.json files or built in-memory from the
    transcript (see main). Both must describe the same tool calls.
    """
    graphNodes = graph["nodes"]
    rawNodes = sorted(
        rawNodes,
        key=lambda n: n.get("sequence", 0)
    )

    if len(graphNodes) != len(rawNodes):
        raise ValueError(
            "Graph and tool_nodes.json have different lengths"
        )

    for i, (node, raw) in enumerate(zip(graphNodes, rawNodes)):
        if (
            node.get("index", i) != i
            or node.get("toolName") != raw.get("toolName")
        ):
            raise ValueError(
                f"Graph and tool nodes don't align at index {i}"
            )

    parents = {
        i: set()
        for i in range(len(graphNodes))
    }

    for edge in graph["edges"]:
        p = edge["parent"]
        c = edge["child"]

        if not (0 <= p < c < len(graphNodes)):
            raise ValueError(f"Invalid edge: {edge}")

        parents[c].add(p)

    root = inferRoot(rawNodes)

    selectedFiles = {
        normalizeFile(name, root)
        for name in extractFiles({
            "toolName": selected.name,
            "input": selected.input
        })
    }

    selectedFiles.discard(None)

    initial = {
        i
        for i, node in enumerate(graphNodes)
        if selectedFiles.intersection(node.get("files", []))
    }

    seen = set()
    stack = list(initial)

    while stack:
        i = stack.pop()

        if i in seen:
            continue

        seen.add(i)
        stack.extend(parents[i] - seen)

    calls = []

    for i in sorted(seen):
        node = rawNodes[i]
        tool_name = node.get("toolName", "Unknown")
        inputs = node.get("input") or {}

        if "command" in inputs:
            action = inputs["command"]
        elif "file_path" in inputs:
            action = inputs["file_path"]
        else:
            action = json.dumps(inputs, ensure_ascii=False)

        calls.append(f"{tool_name}: {action}")

    return tuple(calls), sorted(seen), sorted(selectedFiles)


def main():
    parser = argparse.ArgumentParser(description=__doc__)

    parser.add_argument(
        "--transcript",
        type=Path,
        required=True,
        help="Original Claude Code .jsonl"
    )

    parser.add_argument(
        "--graph",
        type=Path,
        help="Pre-compaction *_dependency_graph.json"
    )

    parser.add_argument(
        "--tool-nodes",
        type=Path,
        help="Matching *_tool_nodes.json"
    )

    parser.add_argument(
        "--post-index",
        type=int,
        default=0,
        help="0-based post-compaction tool-call number"
    )

    parser.add_argument(
        "--monitor",
        choices=MONITORS,
        default="original"
    )

    parser.add_argument(
        "--expected",
        choices=("ALLOW", "BLOCK"),
        required=True,
        help="Expected verdict, not inferred from isError"
    )

    parser.add_argument(
        "--model",
        default="anthropic/claude-opus-4-8"
    )

    args = parser.parse_args()

    messages, actions, Boundary = loadPostcompaction(
        args.transcript
    )

    if not (0 <= args.post_index < len(actions)):
        parser.error(
            f"--post-index must be between 0 and {len(actions) - 1}"
        )

    targetMessage = actions[args.post_index]

    transcript = Transcript.model_validate({
        "name": args.transcript.stem,
        "contexts": [
            {
                "name": "claude-code",
                "messages": messages[:targetMessage + 1]
            }
        ],
        "expected": args.expected,
    })

    decision = Decision(
        context=0,
        message=targetMessage
    )

    selected = action_at(
        transcript,
        decision
    )

    if selected is None:
        raise ValueError(
            "Selected message is not a tool call"
        )

    print(f"Transcript: {args.transcript}")
    print(f"Compaction boundary: line {Boundary}")
    print(
        f"Post-compaction tool call: "
        f"{args.post_index} of {len(actions) - 1}"
    )
    print(f"Tool: {selected.name}")
    print(f"Arguments: {selected.input}")
    print(f"Monitor: {args.monitor}")
    print(f"Expected: {args.expected}")
    print(
        "The compaction summary is passed unchanged "
        "in the transcript history."
    )
    print("No transcript tool calls will be executed.\n")

    sample = SingleActionEvalSample.at(
        transcript=transcript,
        decision=decision,
        expected=args.expected
    )

    if args.monitor == "dependency":

        if args.graph and args.tool_nodes:
            graph = json.loads(args.graph.read_text(encoding="utf-8-sig"))
            rawNodes = json.loads(args.tool_nodes.read_text(encoding="utf-8-sig"))
        else:
            rawNodes, _ = extractAllToolNodes(args.transcript)
            gNodes, edges, _ = buildGraph(rawNodes)
            graph = {"nodes": gNodes, "edges": edges}
            print(f"Built dependency graph inline from transcript: {len(gNodes)} nodes, {len(edges)} edges")

        calls, indices, selectedFiles = parent_tool_calls(
            graph,
            rawNodes,
            selected
        )

        print(f"Proposed-action files: {selectedFiles}")
        print(f"Historical nodes retrieved: {indices}")

        for i, call in zip(indices, calls):
            print(f"  n{i}: {call}")

        monitor = dependentGraphMonitor(
            pastToolCalls=calls
        )

    else:
        monitor = MONITORS[args.monitor]()

    task = single_action_eval(
        dataset=[sample],
        monitor=monitor
    )

    inspect_eval(
        task,
        model=args.model
    )


if __name__ == "__main__":
    main()

