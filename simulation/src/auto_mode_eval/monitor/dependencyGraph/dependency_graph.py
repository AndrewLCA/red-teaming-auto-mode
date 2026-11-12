import argparse
import json
import ntpath
import posixpath
import re
import shlex
from collections import defaultdict
from pathlib import Path


# --- OLD: pre-compaction only (kept for reference, superseded by extractAllToolNodes) ----------
# def extractPrecompactionNodes(path):
#     nodes = {}
#     Boundary = None
#
#     with open(path, "r", encoding="utf-8-sig") as f:
#         for lineNum, line in enumerate(f, 1):
#             if not line.strip():
#                 continue
#
#             record = json.loads(line)
#             if record.get("type") == "system" and record.get("subtype") == "compact_boundary":
#                 Boundary = lineNum
#                 break
#
#             recordType = record.get("type")
#             if recordType not in ("assistant", "user"):
#                 continue
#
#             message = record.get("message") or {}
#             content = message.get("content", [])
#             if not isinstance(content, list):
#                 continue
#
#             for block in content:
#                 if not isinstance(block, dict):
#                     continue
#
#                 if recordType == "assistant" and block.get("type") == "tool_use":
#                     toolId = block.get("id")
#                     if not toolId or toolId in nodes:
#                         continue
#
#                     nodes[toolId] = {
#                         "id": toolId,
#                         "toolName": block.get("name"),
#                         "input": block.get("input", {}),
#                         "output": None,
#                         "sequence": len(nodes) + 1,
#                         "timestamp": record.get("timestamp"),
#                         "callLine": lineNum,
#                         "resultLine": None,
#                     }
#
#                 elif recordType == "user" and block.get("type") == "tool_result":
#                     toolId = block.get("tool_use_id")
#                     if toolId not in nodes:
#                         continue
#                     node = nodes[toolId]
#                     if node["resultLine"] is None:
#                         node["output"] = block.get("content")
#                         node["resultLine"] = lineNum
#                         node["isError"] = block.get("is_error", False)
#
#     if Boundary is None:
#         raise ValueError("No compaction boundary found in transcript")
#
#     return list(nodes.values()), Boundary


def extractAllToolNodes(path):
    nodes = {}
    Boundary = None

    with open(path, "r", encoding="utf-8-sig") as f:
        for lineNum, line in enumerate(f, 1):
            if not line.strip():
                continue

            record = json.loads(line)
            if record.get("type") == "system" and record.get("subtype") == "compact_boundary":
                Boundary = lineNum
                continue  # record the boundary but keep collecting ALL tool calls (pre- and post-compaction)

            recordType = record.get("type")
            if recordType not in ("assistant", "user"):
                continue

            message = record.get("message") or {}
            content = message.get("content", [])
            if not isinstance(content, list):
                continue

            for block in content:
                if not isinstance(block, dict):
                    continue

                if recordType == "assistant" and block.get("type") == "tool_use":
                    toolId = block.get("id")
                    if not toolId or toolId in nodes:
                        continue

                    nodes[toolId] = {
                        "id": toolId,
                        "toolName": block.get("name"),
                        "input": block.get("input", {}),
                        "output": None,
                        "sequence": len(nodes) + 1,
                        "timestamp": record.get("timestamp"),
                        "callLine": lineNum,
                        "resultLine": None,
                    }

                elif recordType == "user" and block.get("type") == "tool_result":
                    toolId = block.get("tool_use_id")
                    if toolId not in nodes:
                        continue
                    node = nodes[toolId]
                    if node["resultLine"] is None:
                        node["output"] = block.get("content")
                        node["resultLine"] = lineNum
                        node["isError"] = block.get("is_error", False)

    return list(nodes.values()), Boundary


def normalizeFile(name, root=None):
    name = str(name).strip().strip('"\'').lstrip("@").replace("\\", "/")
    name = name.removeprefix("./")
    if not name or "://" in name or name.startswith("-"):
        return None
    if root and name.lower().startswith(root.lower() + "/"):
        name = name[len(root) + 1:]
    name = posixpath.normpath(name)
    return None if name in ("", ".") else name.lower()


# def inferRoot(nodes):
#     paths = []
#     for node in nodes:
#         inputs = node.get("input") or {}
#         p = inputs.get("file_path")
#         if isinstance(p, str) and re.match(r"^[A-Za-z]:[\\/]", p):
#             paths.append(ntpath.dirname(p))
#     if not paths:
#         return None
#     try:
#         return ntpath.commonpath(paths).replace("\\", "/")
#     except ValueError:
#         return None
def inferRoot(nodes):
    winDirs, posixDirs = [], []
    for node in nodes:
        for name in extractFiles(node):
            if not isinstance(name, str):
                continue
            s = name.strip().strip('"\'').lstrip("@").replace("\\", "/")
            if re.match(r"^[A-Za-z]:/", s):
                winDirs.append(posixpath.dirname(s))
            elif s.startswith("/"):
                posixDirs.append(posixpath.dirname(s))
    for dirs in (posixDirs, winDirs):
        dirs = [d for d in dirs if d and d != "/"]
        if not dirs:
            continue
        try:
            root = posixpath.commonpath(dirs)
        except ValueError:
            continue
        if root and root != "/":
            return root
    return None

def extractBashFiles(command):
    if not isinstance(command, str):
        return []
    command = re.split(r"<<\s*['\"]?\w+", command, maxsplit=1)[0]
    parts = re.split(r"\s*(?:&&|\|\||[;|])\s*", command)
    files = []

    for part in parts:
        try:
            tokens = shlex.split(part, posix=True)
        except ValueError:
            tokens = part.split()
        if not tokens:
            continue
        for i, token in enumerate(tokens[1:], start=1):
            if tokens[i - 1] in ("-c", "-m", "-E", "-e"):
                continue
            if token.startswith("--") and "=" in token:
                token = token.split("=", 1)[1]
            token = token.lstrip("@").strip(",;()")
            if not token or token.startswith("-") or "://" in token:
                continue
            if token in ("/dev/null", ".", "..") or "$" in token:
                continue
            if re.match(r"^\d*[<>]", token):      # shell redirection: 2>/dev/null, >out.txt
                continue
            if re.search(r"\s", token):           # multi-word junk: printf '\nSecond line\n'
                continue
            if re.search(r"\.[A-Za-z][A-Za-z0-9]*$", token) or "/" in token or "\\" in token:
                files.append(token)
    return list(dict.fromkeys(files))

def extractFiles(node):
    tool = node.get("toolName", node.get("tool_name"))
    inputs = node.get("input") or {}
    files = []

    if tool in ("Read", "Write", "Edit", "NotebookEdit"):
        path = inputs.get("file_path") or inputs.get("notebook_path")
        if path:
            files.append(path)
    elif tool in ("Bash", "bash", "Shell", "PowerShell"):
        files.extend(extractBashFiles(inputs.get("command", "")))
    elif tool in ("Glob", "Grep"):
        if inputs.get("path"):
            files.append(inputs["path"])
    return files


def buildGraph(rawNodes):
    root = inferRoot(rawNodes)
    nodes = []

    for raw in sorted(rawNodes, key=lambda n: n.get("sequence", 0)):
        files = []
        for name in extractFiles(raw):
            normalized = normalizeFile(name, root)
            if normalized and normalized not in files:
                files.append(normalized)

        nodes.append({
            "index": len(nodes),
            "toolName": raw.get("toolName", raw.get("tool_name")),
            "files": files,
        })

    byFile = defaultdict(list)
    edges = []
    for i, node in enumerate(nodes):
        for name in node["files"]:
            for earlierIndex in byFile[name]:
                edges.append({
                    "parent": earlierIndex,
                    "child": i,
                    "file": name,
                })
            byFile[name].append(i)
    return nodes, edges, root


def components(nodes, edges):
    parent = list(range(len(nodes)))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for edge in edges:
        a = find(edge["parent"])
        b = find(edge["child"])
        if a != b:
            parent[b] = a

    groups = defaultdict(list)
    for i in range(len(nodes)):
        groups[find(i)].append(i)
    return sorted(groups.values(), key=len, reverse=True)


def main():
    parser = argparse.ArgumentParser(description="Extract all tool calls and build a shared-file dependency graph")
    parser.add_argument("transcript", type=Path, help="Original Claude Code .jsonl transcript")
    parser.add_argument("-o", "--output", type=Path, default=Path("dependency_graph.json"))
    parser.add_argument("--nodes-output", type=Path, help="Optionally save complete extracted tool nodes")
    args = parser.parse_args()

    rawNodes, Boundary = extractAllToolNodes(args.transcript)
    nodes, edges, root = buildGraph(rawNodes)
    groups = components(nodes, edges)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps({"nodes": nodes, "edges": edges, "components": groups}, indent=2), encoding="utf-8")

    if args.nodes_output:
        args.nodes_output.parent.mkdir(parents=True, exist_ok=True)
        args.nodes_output.write_text(json.dumps(rawNodes, indent=2, ensure_ascii=False), encoding="utf-8")

    matched = sum(1 for node in rawNodes if node["resultLine"] is not None)
    print(f"Compaction boundary: line {Boundary if Boundary is not None else '(none)'}")
    print(f"Unique tool calls (all): {len(rawNodes)}")
    print(f"Matched tool results: {matched}")
    print(f"Unmatched tool calls: {len(rawNodes) - matched}")
    print(f"Path root: {root or '(none inferred)'}")
    print(f"Nodes: {len(nodes)}")
    print(f"File-overlap edges: {len(edges)}")
    print(f"Connected components: {len(groups)}")

    supported = {"Read", "Write", "Edit", "NotebookEdit", "Bash", "bash", "Shell", "PowerShell", "Glob", "Grep"}
    unrecognized = sorted({n.get("toolName") for n in rawNodes if n.get("toolName") and n.get("toolName") not in supported})
    if unrecognized:
        print(f"Unsupported tool types (no file extraction): {unrecognized}")

    print("\nNODES:")
    for node in nodes:
        print(f"  n{node['index']}: {node['toolName']} files={node['files']}")
    print("\nEDGES:")
    for edge in edges:
        print(f"  n{edge['parent']} -> n{edge['child']} via {edge['file']}")
    print(f"\nWrote {args.output}")
    if args.nodes_output:
        print(f"Wrote {args.nodes_output}")

if __name__ == "__main__":
    main()
