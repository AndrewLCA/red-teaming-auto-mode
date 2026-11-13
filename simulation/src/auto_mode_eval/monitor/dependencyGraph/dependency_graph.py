
import argparse
import json
import ntpath
import posixpath
import re
import shlex
from collections import defaultdict
from pathlib import Path
#testing 


def extractPrecompactionNodes(path):
    nodes = {}
    boundary = None
    current_cwd = None

    with open(path, "r", encoding="utf-8-sig") as f:
        for line_num, line in enumerate(f, 1):
            if not line.strip():
                continue

            record = json.loads(line)

            record_cwd = record.get("cwd")
            if isinstance(record_cwd, str) and record_cwd:
                current_cwd = record_cwd

            if (
                record.get("type") == "system"
                and record.get("subtype") == "compact_boundary"
            ):
                boundary = line_num
                break

            record_type = record.get("type")
            if record_type not in ("assistant", "user"):
                continue

            message = record.get("message") or {}
            content = message.get("content", [])

            if not isinstance(content, list):
                continue

            for block in content:
                if not isinstance(block, dict):
                    continue

                if (
                    record_type == "assistant"
                    and block.get("type") == "tool_use"
                ):
                    tool_id = block.get("id")

                    if not tool_id:
                        continue

                    if tool_id in nodes:
                        # Duplicate records sometimes contain
                        # metadata absent from the original.
                        if record_cwd and not nodes[tool_id]["cwd"]:
                            nodes[tool_id]["cwd"] = record_cwd
                        continue

                    nodes[tool_id] = {
                        "id": tool_id,
                        "toolName": block.get("name"),
                        "input": block.get("input", {}),
                        "output": None,
                        "sequence": len(nodes) + 1,
                        "timestamp": record.get("timestamp"),
                        "cwd": record_cwd or current_cwd,
                        "callLine": line_num,
                        "resultLine": None,
                    }

                elif (
                    record_type == "user"
                    and block.get("type") == "tool_result"
                ):
                    tool_id = block.get("tool_use_id")

                    if tool_id not in nodes:
                        continue

                    node = nodes[tool_id]

                    if node["resultLine"] is None:
                        node["output"] = block.get("content")
                        node["resultLine"] = line_num
                        node["isError"] = block.get(
                            "is_error", False
                        )

    if boundary is None:
        raise ValueError(
            "No compaction boundary found in transcript"
        )

    return list(nodes.values()), boundary


def isWindowsPath(path):
    return bool(re.match(r"^[A-Za-z]:[\\/]", path))


def normalizeFile(name, cwd=None):
    if not isinstance(name, str):
        return None

    name = name.strip().strip("\"'").lstrip("@")
    name = name.replace("\\", "/")

    if (
        not name
        or "://" in name
        or name.startswith("-")
        or name in (".", "..", "/dev/null")
    ):
        return None

    # Ignore shell artifacts and string literals.
    if any(c in name for c in ("\n", "\r", ">", "<", "|")):
        return None

    if "$" in name:
        return None

    if cwd:
        cwd = cwd.replace("\\", "/")

    # Absolute Windows path
    if isWindowsPath(name):
        return ntpath.normpath(name).replace("\\", "/").lower()

    # Absolute Unix path
    if name.startswith("/"):
        return posixpath.normpath(name).lower()

    # Relative path with working directory
    if cwd:
        if isWindowsPath(cwd):
            return ntpath.normpath(
                ntpath.join(cwd, name)
            ).replace("\\", "/").lower()

        return posixpath.normpath(
            posixpath.join(cwd, name)
        ).lower()

    return posixpath.normpath(name).lower()


def extractBashFiles(command):
    if not isinstance(command, str):
        return []

    # Exclude heredoc content.
    command = re.split(
        r"<<\s*['\"]?\w+",
        command,
        maxsplit=1
    )[0]

    parts = re.split(
        r"\s*(?:&&|\|\||[;|])\s*",
        command
    )

    files = []

    for part in parts:
        try:
            lexer = shlex.shlex(
                part,
                posix=True,
                punctuation_chars="<>"
            )
            lexer.whitespace_split = True
            tokens = list(lexer)
        except ValueError:
            tokens = part.split()

        if not tokens:
            continue

        executable = tokens[0]
        i = 1

        while i < len(tokens):
            token = tokens[i]

            # Redirection target
            if token in (">", ">>", "<", "<<"):
                if i + 1 < len(tokens):
                    target = tokens[i + 1]
                    if target != "/dev/null":
                        files.append(target)
                i += 2
                continue

            # Ignore attached redirection fragments.
            if re.match(r"^\d*>", token):
                i += 1
                continue

            # echo/printf arguments usually contain
            # literal text rather than filenames.
            if executable in ("echo", "printf"):
                i += 1
                continue

            if (
                i > 0
                and tokens[i - 1] in ("-c", "-m", "-E", "-e")
            ):
                i += 1
                continue

            if token.startswith("--") and "=" in token:
                token = token.split("=", 1)[1]

            token = token.lstrip("@").strip(",;()")

            if (
                not token
                or token.startswith("-")
                or "://" in token
                or token == "/dev/null"
                or "$" in token
            ):
                i += 1
                continue

            if (
                re.search(r"\.[A-Za-z][A-Za-z0-9]*$", token)
                or "/" in token
                or "\\" in token
            ):
                files.append(token)

            i += 1

    return list(dict.fromkeys(files))


def extractFiles(node):
    tool = node.get(
        "toolName",
        node.get("tool_name")
    )
    inputs = node.get("input") or {}
    files = []

    if tool in ("Read", "Write", "Edit", "NotebookEdit"):
        path = (
            inputs.get("file_path")
            or inputs.get("notebook_path")
        )
        if path:
            files.append(path)

    elif tool in ("Bash", "bash", "Shell", "PowerShell"):
        files.extend(
            extractBashFiles(inputs.get("command", ""))
        )

    elif tool in ("Glob", "Grep"):
        if inputs.get("path"):
            files.append(inputs["path"])

    return files


def buildGraph(rawNodes):
    nodes = []

    for raw in sorted(
        rawNodes,
        key=lambda n: n.get("sequence", 0)
    ):
        files = []
        cwd = raw.get("cwd")

        for name in extractFiles(raw):
            normalized = normalizeFile(name, cwd)

            if normalized and normalized not in files:
                files.append(normalized)

        nodes.append({
            "index": len(nodes),
            "toolName": raw.get("toolName"),
            "files": files,
            "cwd": cwd,
        })

    by_file = defaultdict(list)
    edges = []

    for i, node in enumerate(nodes):
        for name in node["files"]:
            for earlier_index in by_file[name]:
                edges.append({
                    "parent": earlier_index,
                    "child": i,
                    "file": name,
                })

            by_file[name].append(i)

    return nodes, edges


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

    return sorted(
        groups.values(),
        key=len,
        reverse=True
    )


def main():
    parser = argparse.ArgumentParser(
        description=(
            "Extract pre-compaction tool calls and "
            "build a shared-file dependency graph"
        )
    )

    parser.add_argument(
        "transcript",
        type=Path,
        help="Original Claude Code JSONL transcript"
    )

    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=Path("dependency_graph.json")
    )

    parser.add_argument(
        "--nodes-output",
        type=Path,
        help="Optionally save complete extracted tool nodes"
    )

    args = parser.parse_args()

    raw_nodes, boundary = extractPrecompactionNodes(
        args.transcript
    )

    nodes, edges = buildGraph(raw_nodes)
    groups = components(nodes, edges)

    args.output.parent.mkdir(
        parents=True,
        exist_ok=True
    )

    args.output.write_text(
        json.dumps({
            "nodes": nodes,
            "edges": edges,
            "components": groups
        }, indent=2),
        encoding="utf-8"
    )

    if args.nodes_output:
        args.nodes_output.parent.mkdir(
            parents=True,
            exist_ok=True
        )

        args.nodes_output.write_text(
            json.dumps(
                raw_nodes,
                indent=2,
                ensure_ascii=False
            ),
            encoding="utf-8"
        )

    matched = sum(
        1 for node in raw_nodes
        if node["resultLine"] is not None
    )

    print(f"Compaction boundary: line {boundary}")
    print(f"Unique pre-compaction tool calls: {len(raw_nodes)}")
    print(f"Matched tool results: {matched}")
    print(
        f"Unmatched tool calls: "
        f"{len(raw_nodes) - matched}"
    )
    print(f"Nodes: {len(nodes)}")
    print(f"File-overlap edges: {len(edges)}")
    print(f"Connected components: {len(groups)}")

    supported = {
        "Read", "Write", "Edit", "NotebookEdit",
        "Bash", "bash", "Shell", "PowerShell",
        "Glob", "Grep"
    }

    unrecognized = sorted({
        node.get("toolName")
        for node in raw_nodes
        if node.get("toolName")
        and node.get("toolName") not in supported
    })

    if unrecognized:
        print(f"Unsupported tool types: {unrecognized}")

    print("\nNODES:")
    for node in nodes:
        print(
            f"  n{node['index']}: "
            f"{node['toolName']} "
            f"cwd={node['cwd']} "
            f"files={node['files']}"
        )

    print("\nEDGES:")
    for edge in edges:
        print(
            f"  n{edge['parent']} -> "
            f"n{edge['child']} via {edge['file']}"
        )

    print("\nCOMPONENTS:")
    for i, group in enumerate(groups):
        print(f"  Component {i}: {group}")

    print(f"\nWrote {args.output}")

    if args.nodes_output:
        print(f"Wrote {args.nodes_output}")


if __name__ == "__main__":
    main()
