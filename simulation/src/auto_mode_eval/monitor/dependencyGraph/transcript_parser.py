import argparse
import json
from pathlib import Path

def extractPrecompactionNodes(path):
    nodes = {} #dictionary that stores tool call information (allows dupe 
    Boundary = None #tells us where compaction happened 

    with open(path, "r", encoding="utf-8-sig") as f:
        for lineNum, line in enumerate(f, 1):
            if not line.strip():
                continue

            record = json.loads(line)

            if record.get("type") == "system" and record.get("subtype") == "compact_boundary": #stops at compaction bound, we only want to look at what is being compacted 
                Boundary = lineNum
                break

            recordType = record.get("type")
            if recordType not in ("assistant", "user"): #only want assistant tool calls and tool results
                continue

            message = record.get("message") or {}
            content = message.get("content", [])

            if not isinstance(content, list):
                continue

            for block in content:
                if not isinstance(block, dict):
                    continue

                if recordType == "assistant" and block.get("type") == "tool_use": #where nodes are created
                    toolId = block.get("id")

                    if not toolId or toolId in nodes:
                        continue

                    nodes[toolId] = {
                        "id": toolId,
                        "toolName": block.get("name"),
                        "input": block.get("input", {}),
                        "output": None,
                        "sequence": len(nodes) + 1,

                        #nodes do not need this (used to debug parser)
                        "timestamp": record.get("timestamp"),
                        "callLine": lineNum,
                        "resultLine": None
                    }

                #match tool results to their nodes
                elif recordType == "user" and block.get("type") == "tool_result":
                    toolId = block.get("tool_use_id")

                    if toolId not in nodes:
                        continue

                    node = nodes[toolId]

                    if node["resultLine"] is None:
                        node["output"] = block.get("content")
                        node["resultLine"] = lineNum
                        node["isError"] = block.get("is_error", False)

    if Boundary is None:
        raise ValueError("No compaction boundary found in transcript")

    return list(nodes.values()), Boundary


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("transcript", type=Path)
    parser.add_argument("-o", "--output", type=Path, default=Path("tool_nodes.json"))
    args = parser.parse_args()

    nodes, Boundary = extractPrecompactionNodes(args.transcript)

    args.output.parent.mkdir(parents=True, exist_ok=True)

    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(nodes, f, indent=2, ensure_ascii=False)

    matched = sum(1 for node in nodes if node["resultLine"] is not None)

    print(f"Compaction boundary: line {Boundary}")
    print(f"Unique pre-compaction tool calls: {len(nodes)}")
    print(f"Matched tool results: {matched}")
    print(f"Unmatched tool calls: {len(nodes) - matched}")
    print(f"Output: {args.output.resolve()}")


if __name__ == "__main__":
    main()

# uv run --no-dev python src/auto_mode_eval/monitor/dependencyGraph/transcript_parser.py `../compaction_data/auto_compact_benign_1/3c3c398c-6ec6-427b-8302-d0e6f36d9906.jsonl `-o ../compaction_data/auto_compact_benign_1/tool_nodes.json
