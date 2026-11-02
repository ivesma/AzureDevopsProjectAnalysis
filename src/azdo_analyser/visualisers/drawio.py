"""draw.io (diagrams.net) and Graphviz DOT renderers.

draw.io files open in diagrams.net and can be exported from there to Visio (.vsdx).
"""

from __future__ import annotations

from collections import defaultdict, deque
from xml.sax.saxutils import escape as _escape

from azdo_analyser.core.models import Graph

STYLES = {
    "prod": "rounded=1;whiteSpace=wrap;fillColor=#ffebee;strokeColor=#e53935;fontColor=#b71c1c;strokeWidth=2;",
    "deploy": "rounded=1;whiteSpace=wrap;fillColor=#e8f5e9;strokeColor=#43a047;",
    "template": "shape=parallelogram;whiteSpace=wrap;fillColor=#fff8e1;strokeColor=#f9a825;",
    "missing": "rounded=1;whiteSpace=wrap;dashed=1;fillColor=#f5f5f5;strokeColor=#9e9e9e;",
    "start": "ellipse;whiteSpace=wrap;fillColor=#e8eaf6;strokeColor=#3949ab;",
    "repository": "shape=cylinder3;whiteSpace=wrap;fillColor=#e0f2f1;strokeColor=#00897b;",
    "service_connection": "rounded=1;arcSize=50;whiteSpace=wrap;fillColor=#fce4ec;strokeColor=#d81b60;",
    "keyvault": "shape=cylinder3;whiteSpace=wrap;fillColor=#e8eaf6;strokeColor=#3949ab;",
    "environment": "shape=cylinder3;whiteSpace=wrap;fillColor=#e8f5e9;strokeColor=#43a047;",
    "pool": "shape=process;whiteSpace=wrap;fillColor=#eceff1;strokeColor=#546e7a;",
}
def escape(text: str) -> str:
    """Escape for use inside an XML attribute value."""
    return _escape(str(text), {'"': "&quot;"})


DEFAULT_STYLE = "rounded=1;whiteSpace=wrap;fillColor=#e3f2fd;strokeColor=#1e88e5;"
EDGE_STYLES = {"dashed": "dashed=1;", "thick": "strokeWidth=3;", "solid": ""}


def layers(graph: Graph) -> dict[str, int]:
    """Longest-path layering from sources (cycle-safe)."""
    incoming = defaultdict(int)
    outgoing = defaultdict(list)
    for e in graph.edges:
        if e.source in graph.nodes and e.target in graph.nodes and e.source != e.target:
            outgoing[e.source].append(e.target)
            incoming[e.target] += 1
    layer = {n: 0 for n in graph.nodes}
    queue = deque(n for n in graph.nodes if incoming[n] == 0)
    remaining = dict(incoming)
    visited = set()
    while queue:
        node = queue.popleft()
        visited.add(node)
        for nxt in outgoing[node]:
            layer[nxt] = max(layer[nxt], layer[node] + 1)
            remaining[nxt] -= 1
            if remaining[nxt] == 0:
                queue.append(nxt)
    return layer  # nodes in cycles keep their best-known layer


def to_drawio(graph: Graph) -> str:
    layer = layers(graph)
    rows: dict[int, int] = defaultdict(int)
    horizontal = graph.direction in ("LR", "RL")
    cells = ['<mxCell id="0"/>', '<mxCell id="1" parent="0"/>']
    ids = {}
    for index, node in enumerate(graph.nodes.values()):
        cid = f"n{index}"
        ids[node.id] = cid
        lvl = layer.get(node.id, 0)
        pos = rows[lvl]
        rows[lvl] += 1
        x, y = (lvl * 240, pos * 100) if horizontal else (pos * 240, lvl * 110)
        label = escape(node.label).replace("\n", "&lt;br&gt;")
        style = STYLES.get(node.type, DEFAULT_STYLE) + "html=1;"
        cells.append(f'<mxCell id="{cid}" value="{label}" style="{style}" vertex="1" parent="1">'
                     f'<mxGeometry x="{x}" y="{y}" width="180" height="60" as="geometry"/></mxCell>')
    for index, edge in enumerate(graph.edges):
        if edge.source not in ids or edge.target not in ids:
            continue
        style = "edgeStyle=orthogonalEdgeStyle;rounded=1;html=1;" + EDGE_STYLES.get(edge.style, "")
        cells.append(f'<mxCell id="e{index}" value="{escape(edge.label or "")}" style="{style}" edge="1" parent="1" '
                     f'source="{ids[edge.source]}" target="{ids[edge.target]}"><mxGeometry relative="1" as="geometry"/>'
                     f'</mxCell>')
    return ('<?xml version="1.0" encoding="UTF-8"?>\n'
            f'<mxfile host="azdo-analyser"><diagram name="{escape(graph.title)}"><mxGraphModel><root>'
            + "".join(cells) + "</root></mxGraphModel></diagram></mxfile>\n")


def to_dot(graph: Graph) -> str:
    def q(text: str) -> str:
        return '"' + str(text).replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n") + '"'

    lines = [f"digraph {q(graph.name)} {{", f"  rankdir={graph.direction};", "  node [shape=box, style=rounded];"]
    for node in graph.nodes.values():
        color = {"prod": "red", "missing": "gray", "template": "orange", "deploy": "darkgreen"}.get(node.type, "steelblue")
        lines.append(f"  {q(node.id)} [label={q(node.label)}, color={color}];")
    for e in graph.edges:
        attrs = [f"label={q(e.label)}"] if e.label else []
        if e.style == "dashed":
            attrs.append("style=dashed")
        elif e.style == "thick":
            attrs.append("penwidth=2")
        lines.append(f"  {q(e.source)} -> {q(e.target)}" + (f" [{', '.join(attrs)}]" if attrs else "") + ";")
    lines.append("}")
    return "\n".join(lines) + "\n"
