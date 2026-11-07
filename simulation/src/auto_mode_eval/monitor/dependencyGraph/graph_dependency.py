import argparse
import hashlib
import json
import ntpath
import posixpath
import re
import shlex
from collections import defaultdict
from pathlib import Path


def fileHash(name):
    return hashlib.sha256(name.encode("utf-8")).hexdigest()[:8]

def normalizeFile(name, root=None):
    name = str(name).strip().strip('"\'').lstrip("@").replace("\\", "/")
    name = name.removeprefix("./")
    if not name or "://" in name or name.startswith("-"):
        return None
    if root and name.lower().startswith(root.lower() + "/"):
        name = name[len(root) + 1:]
    name = posixpath.normpath(name)
    return None if name in ("", ".") else name.lower()


def inferRoot(nodes):
    paths = []
    for node in nodes:
        inputs = node.get("input") or {}
        p = inputs.get("file_path")
        if isinstance(p, str) and re.match(r"^[A-Za-z]:[\\/]", p):
            paths.append(ntpath.dirname(p))
    if not paths:
        return None
    try:
        return ntpath.commonpath(paths).replace("\\", "/")
    except ValueError:
        return None


def extractBashFiles(command):
    """Best-effort file tokens in shell commands, not a full shell parser."""
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
            "files": files
        })
    byFile = defaultdict(list)
    edges = []
    for i, node in enumerate(nodes):
        for name in node["files"]:
            for earlierIndex in byFile[name]:
                edges.append({
                    "parent": earlierIndex,
                    "child": i,
                    "file": name
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
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", type=Path, required=True, help="Parsed tool_nodes.json")
    parser.add_argument("--output", type=Path, help="Optional JSON output containing nodes and edges")
    args = parser.parse_args()

    rawNodes = json.loads(args.json.read_text(encoding="utf-8-sig"))
    if not isinstance(rawNodes, list):
        parser.error("Input must be a JSON array of tool-call nodes")
    nodes, edges, root = buildGraph(rawNodes)
    groups = components(nodes, edges)

    print(f"Input: {args.json}")
    print(f"Path root: {root or '(none inferred)'}")
    print(f"Nodes: {len(nodes)}")
    print(f"File-overlap edges: {len(edges)}")
    print(f"Connected components: {len(groups)}")
    unrecognized = sorted({n.get("toolName", n.get("tool_name")) for n in rawNodes if n.get("toolName", n.get("tool_name")) not in {"Read", "Write", "Edit", "NotebookEdit", "Bash", "bash", "Shell", "Glob", "Grep"}})
    if unrecognized:
        print(f"Unsupported tool types (no file extraction): {unrecognized}")
    
    print("\nNODES:")
    for node in nodes:
        print(f"  n{node['index']}: {node['toolName']} files={node['files']}")
    print("\nEDGES:")
    for edge in edges:
        print(f"  {edge['parent']} -> {edge['child']} via {edge['file']}")
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps({"nodes": nodes, "edges": edges, "components": groups}, indent=2), encoding="utf-8")
        print(f"\nWrote {args.output}")


if __name__ == "__main__":
    main()
