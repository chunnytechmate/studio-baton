#!/usr/bin/env python3
"""Extract the call graph the published diagram draws, straight from the source.

A picture of an architecture is worth nothing once it stops matching the code,
and hand-drawn ones always do. This walks ``src/baton`` with ``ast`` and emits
the graph as JSON, so the diagram can be regenerated whenever the code moves
and a stale edge becomes a diff rather than a slow lie.

Layers, in the order a request travels them::

    core → cli → pipe → iface (protocol) → impl (concrete adapter) → svc

``iface`` nodes are the protocol methods every pipeline shares: ``docs.*``,
``store.*`` and friends. They are the interesting part: the crossings on the
picture are pipelines meeting at one of these, which is a fact about the code
rather than a choice about the layout.

Left out on purpose: ``adapters/*/base.py`` declares protocols rather than
running, and ``adapters/fakes.py`` is a test double no studio ever executes.

Usage::

    tools/callgraph.py                 # writes callgraph.json beside the repo
    tools/callgraph.py --out path.json
    tools/callgraph.py --stats         # what it found, without writing
"""

from __future__ import annotations

import argparse
import ast
import json
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent / "src" / "baton"

#: Attribute prefixes that mean "a call across a protocol boundary".
IFACE_PREFIX = (
    "docs.",
    "store.",
    "calendar.",
    "source.",
    "encoder.",
    "publisher.",
    "messenger.",
    "jsonio.",
    "render.",
    "jobs.",
)

#: Where a protocol family ends up once the adapter has done its work.
#: The publisher family is absent on purpose: its methods do not share one
#: destination, so it lives in SERVICE_METHOD below.
SERVICE = {
    "docs": "notion",
    "store": "supabase",
    "calendar": "gcal",
    "source": "drive",
    "encoder": "ffmpeg",
    "messenger": "line",
    "jsonio": "disk",
    "render": None,
    "jobs": "disk",
}

#: Families whose methods do not all reach the same place, decided per
#: method. ``publisher.upload`` is the media publisher whose protocol
#: default calls YouTube directly. ``publisher.publish`` and
#: ``publisher.plan`` resolve to SummaryPublisher, whose real work already
#: shows up as edges into the docs protocol: a YouTube edge there would
#: claim the lesson summary is published as a video.
SERVICE_METHOD = {
    "publisher": {"upload": "youtube"},
}

#: Concrete adapters: module → (protocol family, service it reaches).
IMPL = {
    "adapters/docs/notion": ("docs", "notion"),
    "adapters/db/sqlite": ("store", "disk"),
    "adapters/db/postgrest": ("store", "supabase"),
    "adapters/db/fallback": ("store", None),
    "adapters/cal/google": ("calendar", "gcal"),
    "adapters/chat/drivers": ("messenger", "line"),
    "adapters/media/google": ("source", "drive"),
    "adapters/media/local": ("source", "disk"),
    "adapters/media/ffmpeg": ("encoder", "ffmpeg"),
}

#: Teardown, not a data path: 22 callers of it would dominate the picture.
SKIP_CALLS = {"store.close"}


def _dotted(node: ast.AST) -> str:
    parts: list[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
    return ".".join(reversed(parts))


def _callee(call: ast.Call) -> str:
    """The callee reduced to its last two names, so ``self.docs.get_status``
    becomes ``docs.get_status``: the boundary, not the local variable."""
    func = call.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        bits = _dotted(func).split(".")
        if bits and bits[0] == "self":
            bits = bits[1:]
        return ".".join(bits[-2:]) if len(bits) >= 2 else ".".join(bits)
    return ""


class _Module(ast.NodeVisitor):
    """Collects definitions and calls for one module.

    A visitor object rather than a closure inside the walk loop: closing over
    the loop variables works by accident here and stops working the moment the
    visitor outlives one iteration.
    """

    def __init__(self, mod: str) -> None:
        self.mod = mod
        self.dmod = mod.replace("/", ".")
        self.stack: list[str] = []
        self.defined: list[tuple[str, str]] = []
        self.calls: list[tuple[str, str]] = []
        self.methods: list[tuple[str, bool]] = []

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self.stack.append(node.name)
        self.generic_visit(node)
        self.stack.pop()

    def _function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        self.stack.append(node.name)
        qualified = f"{self.dmod}:{'.'.join(self.stack)}"
        self.defined.append((node.name, qualified))
        self.methods.append((".".join(self.stack), node.name.startswith("_")))
        for sub in ast.walk(node):
            if isinstance(sub, ast.Call):
                name = _callee(sub)
                if name:
                    self.calls.append((qualified, name))
        self.stack.pop()

    visit_FunctionDef = _function  # type: ignore[assignment]
    visit_AsyncFunctionDef = _function  # type: ignore[assignment]


def scan(
    root: Path,
) -> tuple[dict[str, list[str]], list[tuple[str, str, str]], dict[str, list[tuple[str, bool]]]]:
    """Parse every module once.

    Returns:
        ``(defined, edges, methods)``: where a name is defined, every call
        with the module it was made from, and each module's own methods.
    """
    defined: dict[str, list[str]] = defaultdict(list)
    edges: list[tuple[str, str, str]] = []
    methods: dict[str, list[tuple[str, bool]]] = defaultdict(list)

    for path in sorted(root.rglob("*.py")):
        mod = path.relative_to(root).with_suffix("").as_posix()
        visitor = _Module(mod)
        visitor.visit(ast.parse(path.read_text(encoding="utf-8")))
        for name, qualified in visitor.defined:
            if qualified not in defined[name]:
                defined[name].append(qualified)
        edges.extend((caller, callee, mod) for caller, callee in visitor.calls)
        methods[mod].extend(visitor.methods)

    return defined, edges, methods


def build(root: Path) -> dict[str, object]:
    """Turn the parsed source into the layered graph the diagram renders."""
    defined, edges, methods = scan(root)
    nodes: dict[str, dict[str, str]] = {}
    links: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()

    def add(nid: str, layer: str, label: str, group: str, tag: str = "") -> None:
        nodes.setdefault(nid, {"i": nid, "y": layer, "l": label, "g": group, "t": tag})

    def link(source: str, target: str) -> None:
        if source != target and (source, target) not in seen:
            seen.add((source, target))
            links.append({"s": source, "t": target})

    add("core", "core", "STUDIO BATON", "core")

    for caller, callee, cmod in edges:
        dmod = cmod.replace("/", ".")
        if dmod.startswith("cli."):
            # ``cli.cmd_calendar`` and ``cli.guard`` are both just "calendar"
            # and "guard": the cmd_ prefix is naming convention, not meaning.
            layer, group = "cli", dmod.removeprefix("cli.").removeprefix("cmd_")
        elif dmod.startswith("pipelines.") or dmod.startswith("core.jobs"):
            layer = "pipe"
            group = dmod.replace("pipelines.", "").replace("core.", "")
        else:
            continue

        add(caller, layer, caller.split(":")[1], group)

        if any(callee.startswith(prefix) for prefix in IFACE_PREFIX):
            if callee in SKIP_CALLS:
                continue
            add("if:" + callee, "iface", callee, callee.split(".")[0])
            link(caller, "if:" + callee)
            continue

        # A bare name the calling module defines itself is a local helper:
        # `_resolve` lives in five cmd_* modules and is not VideoPipeline._resolve,
        # however much the name match wants it to be.
        name = callee.split(".")[-1]
        if any(q.startswith(dmod + ":") for q in defined.get(name, [])):
            continue
        owners = [
            q
            for q in defined.get(name, [])
            if q.startswith("pipelines.") or q.startswith("core.jobs")
        ]
        if layer == "cli" and len(owners) == 1:
            target = owners[0]
            tmod, tfn = target.split(":")
            add(target, "pipe", tfn, tmod.replace("pipelines.", "").replace("core.", ""))
            link(caller, target)

    # concrete adapters, and the private helper each public method leans on:
    # the chunking, the SQL and the retries all live one level down
    iface_methods: dict[str, set[tuple[str, str]]] = defaultdict(set)
    for nid, node in list(nodes.items()):
        if node["y"] == "iface":
            family, method = node["l"].split(".", 1)
            iface_methods[family].add((method, nid))

    for mod, (family, service) in IMPL.items():
        dmod = mod.replace("/", ".")
        public = {name for name, private in methods.get(mod, []) if not private}
        for method, iface_id in sorted(iface_methods.get(family, set())):
            for qualified in sorted(public):
                if qualified.split(".")[-1] != method:
                    continue
                nid = f"im:{dmod}:{qualified}"
                cls = qualified.split(".")[0] if "." in qualified else dmod.split(".")[-1]
                add(nid, "impl", qualified, family, cls)
                link(iface_id, nid)
                if service:
                    add("svc:" + service, "svc", service, service)
                    link(nid, "svc:" + service)
                for caller, callee, cm in edges:
                    if cm != mod or not caller.endswith(":" + qualified):
                        continue
                    helper = callee.split(".")[-1]
                    if not helper.startswith("_"):
                        continue
                    for owner in defined.get(helper, []):
                        if not owner.startswith(dmod + ":"):
                            continue
                        hid = f"im:{owner}"
                        add(hid, "impl", owner.split(":")[1], family, cls)
                        link(nid, hid)
                        if service:
                            link(hid, "svc:" + service)

    # families with no adapter of their own still reach something real
    impl_families = {family for family, _ in IMPL.values()}
    for nid, node in list(nodes.items()):
        if node["y"] != "iface":
            continue
        family = node["l"].split(".")[0]
        if family in impl_families:
            continue
        per_method = SERVICE_METHOD.get(family)
        if per_method is not None:
            service = per_method.get(node["l"].split(".")[-1])
        else:
            service = SERVICE.get(family)
        if service:
            add("svc:" + service, "svc", service, service)
            link(nid, "svc:" + service)

    # The core dispatches only to commands that do something: an argparse
    # `register()` calls nothing, and seventy of those would bury the handlers.
    outgoing: dict[str, int] = defaultdict(int)
    for edge in links:
        outgoing[edge["s"]] += 1
    for nid, node in list(nodes.items()):
        if node["y"] == "cli" and outgoing[nid]:
            link("core", nid)

    used = {edge["s"] for edge in links} | {edge["t"] for edge in links}
    nodes = {k: v for k, v in nodes.items() if k in used}
    links = [e for e in links if e["s"] in nodes and e["t"] in nodes]
    return {"n": list(nodes.values()), "e": links}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", default="callgraph.json", help="where to write the JSON")
    parser.add_argument("--stats", action="store_true", help="print a summary and write nothing")
    parser.add_argument("--indent", type=int, default=None, help="pretty-print with this indent")
    args = parser.parse_args(argv)

    if not ROOT.is_dir():
        print(f"error: {ROOT} is not there: run this from the repository", file=sys.stderr)
        return 2

    graph = build(ROOT)
    nodes, links = graph["n"], graph["e"]

    by_layer: dict[str, int] = defaultdict(int)
    for node in nodes:  # type: ignore[union-attr]
        by_layer[node["y"]] += 1
    incoming: dict[str, int] = defaultdict(int)
    for edge in links:  # type: ignore[union-attr]
        incoming[edge["t"]] += 1
    shared = sum(
        1
        for node in nodes  # type: ignore[union-attr]
        if node["y"] in ("iface", "svc") and incoming[node["i"]] > 1
    )

    print(f"nodes {len(nodes)}  " + "  ".join(f"{k}={v}" for k, v in sorted(by_layer.items())))
    print(f"links {len(links)}  shared interfaces {shared}")

    if args.stats:
        return 0

    separators = None if args.indent else (",", ":")
    Path(args.out).write_text(
        json.dumps(graph, indent=args.indent, separators=separators, ensure_ascii=False),
        encoding="utf-8",
    )
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
