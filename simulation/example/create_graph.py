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
        # print(f"       files: {files}")

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


# """Live, incremental dependency graph with ancestor tracing.

# Unlike the one-shot builder, this adds one tool call at a time (as the agent acts) and keeps the
# graph as persistent state — so it survives a compaction: the pre-compaction nodes stay in the graph
# even after the agent's own context window drops them.

# Edges are DIRECTED: producer --file--> consumer. When a new action arrives, for each file it reads
# we add an edge from whichever node last WROTE that file. So at any point you can take a node and
# walk UP its parents to recover every tool call that contributed to it — the provenance lineage.

# Rename survives automatically: `tar` writes release.tgz, `mv` reads release.tgz (edge tar->mv) and
# writes app-build.tgz, `curl` reads app-build.tgz (edge mv->curl). ancestors(curl) = {mv, tar} even
# though the file was renamed and the compaction dropped the history.

#     python example/live_dependency_graph.py --yaml example/mctest_rename_exfil.yaml
#     python example/live_dependency_graph.py --yaml example/mctest3_1.yaml --trace-last
# """

# from __future__ import annotations

# import argparse
# import re
# import shlex
# from dataclasses import dataclass, field
# from pathlib import Path

# import yaml


# def parse_io(command: str) -> tuple[list[str], list[str], str | None]:
#     """Return (inputs, outputs, external_dest) for a shell command. external_dest is set when the
#     command sends a file off-box (an exfil sink). Heuristic but covers the common vehicles."""
#     try:
#         toks = shlex.split(command)
#     except ValueError:
#         toks = command.split()
#     if not toks:
#         return [], [], None
#     exe = toks[0]

#     if exe == "tar":
#         out, ins, i = None, [], 1
#         while i < len(toks):
#             t = toks[i]
#             if t.startswith("-") and "f" in t:
#                 out = toks[i + 1]; i += 2; continue
#             if t.startswith("-"):
#                 i += 1; continue
#             ins.append(t); i += 1
#         return ins, ([out] if out else []), None

#     if exe in ("cp", "mv"):
#         args = [t for t in toks[1:] if not t.startswith("-")]
#         if len(args) >= 2:
#             *srcs, dst = args
#             return srcs, [dst], None

#     if exe == "curl":
#         file, url = None, None
#         for i, t in enumerate(toks):
#             if t in ("--data-binary", "-d", "--data", "-T", "--upload-file") and i + 1 < len(toks):
#                 v = toks[i + 1].lstrip("@")
#                 if v and not v.startswith("-"):
#                     file = v
#             if re.match(r"[a-z]+://", t):
#                 url = t
#         dest = url if (url and _external(url)) else None
#         return ([file] if file else []), [], dest

#     if exe == "scp":
#         args = [t for t in toks[1:] if not t.startswith("-")]
#         if len(args) >= 2:
#             *srcs, dst = args
#             host = dst.split(":")[0].split("@")[-1] if ":" in dst else None
#             dest = dst if host and host not in _INTERNAL else None
#             return srcs, [], dest

#     # generic: file-looking tokens are reads; no declared output
#     reads = [t.lstrip("@") for t in toks[1:]
#              if not t.startswith("-") and "://" not in t and ("/" in t or "." in t)]
#     return reads, [], None


# _INTERNAL = {"localhost", "127.0.0.1", "::1", "0.0.0.0"}


# def _external(url: str) -> bool:
#     m = re.match(r"[a-z]+://([^/]+)", url)
#     if not m:
#         return False
#     return m.group(1).split(":")[0].split("@")[-1] not in _INTERNAL


# @dataclass
# class LiveDependencyGraph:
#     nodes: dict[int, dict] = field(default_factory=dict)
#     edges: list[tuple[int, int, str]] = field(default_factory=list)   # (parent, child, file)
#     last_writer: dict[str, int] = field(default_factory=dict)          # file -> node that wrote it
#     _next: int = 0

#     def add_action(self, ctx: int, msg: int, command: str) -> tuple[int, list[tuple[int, int, str]]]:
#         """Add one tool call; wire edges from the last writer of each file it reads."""
#         ins, outs, dest = parse_io(command)
#         nid = self._next
#         self._next += 1
#         self.nodes[nid] = dict(ctx=ctx, msg=msg, command=command, inputs=ins, outputs=outs, dest=dest)
#         new_edges = []
#         for f in ins:
#             if f in self.last_writer:
#                 e = (self.last_writer[f], nid, f)
#                 self.edges.append(e)
#                 new_edges.append(e)
#         for f in outs:
#             self.last_writer[f] = nid     # this node now owns that file name
#         return nid, new_edges

#     def ancestors(self, nid: int) -> list[int]:
#         """Every tool call upstream of `nid` (walk parents), nearest first."""
#         parents: dict[int, list[tuple[int, str]]] = {}
#         for p, c, f in self.edges:
#             parents.setdefault(c, []).append((p, f))
#         seen, order, stack = set(), [], [nid]
#         while stack:
#             n = stack.pop()
#             for p, _f in parents.get(n, []):
#                 if p not in seen:
#                     seen.add(p); order.append(p); stack.append(p)
#         return order

#     def lineage_str(self, nid: int) -> str:
#         """Readable parent-chain from the sources down to `nid`."""
#         parents: dict[int, list[tuple[int, str]]] = {}
#         for p, c, f in self.edges:
#             parents.setdefault(c, []).append((p, f))
#         lines = []
#         for c in [nid] + self.ancestors(nid):
#             for p, f in parents.get(c, []):
#                 lines.append(f"    n{p} ({self._short(p)})  --{f}-->  n{c} ({self._short(c)})")
#         return "\n".join(reversed(lines)) or "    (no parents — root action)"

#     def _short(self, nid: int) -> str:
#         return self.nodes[nid]["command"].split()[0]


# def iter_tool_calls(transcript: dict):
#     for ci, ctx in enumerate(transcript["contexts"]):
#         for mi, m in enumerate(ctx.get("messages", [])):
#             content = m.get("content")
#             if isinstance(content, list):
#                 for b in content:
#                     if isinstance(b, dict) and b.get("type") == "tool_use":
#                         yield ci, mi, (b.get("input") or {}).get("command", "")


# def main() -> None:
#     ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
#     ap.add_argument("--yaml", type=Path, required=True)
#     ap.add_argument("--trace-last", action="store_true",
#                     help="After the replay, print the full ancestry of the last action (default on).")
#     args = ap.parse_args()

#     transcript = yaml.safe_load(args.yaml.read_text(encoding="utf-8"))
#     g = LiveDependencyGraph()

#     print(f"transcript: {transcript.get('name')}\n")
#     print("LIVE REPLAY (one action at a time, as the agent would act):")
#     last = None
#     for ci, mi, cmd in iter_tool_calls(transcript):
#         nid, new_edges = g.add_action(ci, mi, cmd)
#         last = nid
#         tag = "  EXFIL->" + g.nodes[nid]["dest"] if g.nodes[nid]["dest"] else ""
#         print(f"  + n{nid} [ctx{ci} msg{mi}] {cmd[:52]}{tag}")
#         for p, c, f in new_edges:
#             print(f"        edge: n{p} --{f}--> n{c}")
#         if new_edges == [] and g.nodes[nid]["inputs"]:
#             print("        (no parent yet — inputs not produced by a prior tracked call)")

#     if last is not None:
#         print(f"\nANCESTORS of the last action (n{last}) — searching up all its parent tool calls:")
#         anc = g.ancestors(last)
#         if anc:
#             for a in anc:
#                 n = g.nodes[a]
#                 print(f"  n{a} [ctx{n['ctx']} msg{n['msg']}]  {n['command'][:60]}")
#             print("\n  lineage:")
#             print(g.lineage_str(last))
#         else:
#             print("  (none — this action has no tracked provenance)")


# if __name__ == "__main__":
#     main()