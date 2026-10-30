"""Build a file-dependency graph from a transcript's tool calls.

Spec:
- Walk the transcript and keep only the `tool_use` content blocks — each is a candidate node.
- From each tool call's command, extract the file name(s) it references.
- Each file name is hashed; two tool-call nodes are connected by an edge when they share a file
  hash (i.e. they touch the same file). Tool calls that share no file with any other are their own
  disconnected component.
- Because a `mv`/`cp` references BOTH the old and new name, it bridges a rename: the node for the
  move shares a hash with the pre-rename lineage on one side and the post-rename name on the other,
  so a renamed artifact stays in one connected component.

This is just the graph. It does not decide block/allow — risk scoring over the components comes next.

    uv run python example/dependency_graph.py --yaml example/mctest3_1.yaml
    uv run python example/dependency_graph.py --yaml example/mctest_rename_exfil.yaml --dot graph.dot

With --dot it also writes a Graphviz file you can render (`dot -Tpng graph.dot -o graph.png`).
"""

from __future__ import annotations

import argparse
import hashlib
import re
import shlex
from dataclasses import dataclass, field
from pathlib import Path

import yaml


def file_hash(name: str) -> str:
    """Stable short hash of a (normalized) file name — the join key between nodes."""
    norm = name.strip().lstrip("@").rstrip("/")
    return hashlib.sha256(norm.encode()).hexdigest()[:8]


def extract_files(command: str) -> list[str]:
    """File-name tokens referenced by a shell command (best-effort, order-preserving, de-duped)."""
    try:
        toks = shlex.split(command)
    except ValueError:
        toks = command.split()
    files: list[str] = []
    for t in toks[1:]:                       
        raw = t.lstrip("@")                  
        if "=" in raw and raw.startswith("--"):
            raw = raw.split("=", 1)[1].lstrip("@")   
        if not raw or raw.startswith("-"):
            continue
        if "://" in raw:                     
            continue
        looks_like_file = ("/" in raw) or raw.startswith(".") or re.search(r"\.[A-Za-z][A-Za-z0-9]*$", raw)
        if looks_like_file and raw not in files:
            files.append(raw)
    return files


@dataclass
class Node:
    id: int
    ctx: int
    msg: int
    command: str
    files: list[str] = field(default_factory=list)
    hashes: set[str] = field(default_factory=set)


def build_nodes(transcript: dict) -> list[Node]:
    nodes: list[Node] = []
    nid = 0
    for ci, ctx in enumerate(transcript["contexts"]):
        for mi, m in enumerate(ctx.get("messages", [])):
            content = m.get("content")
            if not isinstance(content, list):
                continue
            for b in content:
                if isinstance(b, dict) and b.get("type") == "tool_use":
                    cmd = (b.get("input") or {}).get("command", "")
                    files = extract_files(cmd)
                    nodes.append(Node(nid, ci, mi, cmd, files, {file_hash(f) for f in files}))
                    nid += 1
    return nodes


def build_edges(nodes: list[Node]) -> list[tuple[int, int, list[str]]]:
    """Edge between two nodes that share ≥1 file hash; carries the shared file name(s)."""
    edges = []
    hash_to_name = {file_hash(f): f for n in nodes for f in n.files}
    for i in range(len(nodes)):
        for j in range(i + 1, len(nodes)):
            shared = nodes[i].hashes & nodes[j].hashes
            if shared:
                edges.append((i, j, [hash_to_name[h] for h in shared]))
    return edges


def components(nodes: list[Node], edges) -> list[list[int]]:
    parent = list(range(len(nodes)))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for i, j, _ in edges:
        parent[find(i)] = find(j)
    groups: dict[int, list[int]] = {}
    for n in nodes:
        groups.setdefault(find(n.id), []).append(n.id)
    return list(groups.values())


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--yaml", type=Path, required=True)
    ap.add_argument("--dot", type=Path, default=None, help="Also write a Graphviz .dot file.")
    args = ap.parse_args()

    transcript = yaml.safe_load(args.yaml.read_text(encoding="utf-8"))
    nodes = build_nodes(transcript)
    edges = build_edges(nodes)
    comps = components(nodes, edges)

    print(f"transcript: {transcript.get('name')}")
    print(f"\nNODES ({len(nodes)} tool calls):")
    for n in nodes:
        files = ", ".join(f"{f}#{file_hash(f)}" for f in n.files) or "(no files)"
        print(f"  n{n.id} [ctx{n.ctx} msg{n.msg}]  {n.command[:60]}")
        print(f"       files: {files}")

    print(f"\nEDGES ({len(edges)}):")
    for i, j, shared in edges:
        print(f"  n{i} — n{j}   shared: {', '.join(shared)}")

    print(f"\nCONNECTED COMPONENTS ({len(comps)}):")
    for k, grp in enumerate(sorted(comps, key=lambda g: -len(g)), 1):
        label = " → ".join(f"n{i}" for i in sorted(grp))
        print(f"  component {k}: {label}")

    if args.dot:
        lines = ["graph deps {", '  node [shape=box, fontname="monospace"];']
        for n in nodes:
            short = (n.command[:28] + "…") if len(n.command) > 28 else n.command
            lines.append(f'  n{n.id} [label="n{n.id}: {short}"];')
        for i, j, shared in edges:
            lines.append(f'  n{i} -- n{j} [label="{shared[0]}"];')
        lines.append("}")
        args.dot.write_text("\n".join(lines), encoding="utf-8")
        print(f"\nwrote {args.dot}  (render: dot -Tpng {args.dot} -o graph.png)")


if __name__ == "__main__":
    main()