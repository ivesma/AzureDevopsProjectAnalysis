"""Mermaid flowchart renderer.

Output uses the conservative ``graph`` syntax so it renders both in modern Mermaid (HTML report,
GitHub, VS Code) and in the older Mermaid build used by Azure DevOps wikis.
"""

from __future__ import annotations

import re

from azdo_analyser.core.models import Graph

SHAPES = {
    "start": ('(["', '"])'),
    "template": ('[/"', '"/]'),
    "prod": ('{{"', '"}}'),
    "missing": ('>"', '"]'),
    "keyvault": ('[("', '")]'),
    "environment": ('[("', '")]'),
    "repository": ('[("', '")]'),
    "repository_disabled": ('[("', '")]'),
    "service_connection": ('(["', '"])'),
    "pool": ('[["', '"]]'),
    "work_item_type": ('("', '")'),
}

CLASS_DEFS = {
    "start": "fill:#e8eaf6,stroke:#3949ab,color:#1a237e",
    "pipeline": "fill:#e3f2fd,stroke:#1e88e5,color:#0d47a1",
    "release": "fill:#ede7f6,stroke:#5e35b1,color:#311b92",
    "release_stage": "fill:#ede7f6,stroke:#5e35b1,color:#311b92",
    "stage": "fill:#e3f2fd,stroke:#1e88e5,color:#0d47a1",
    "deploy": "fill:#e8f5e9,stroke:#43a047,color:#1b5e20",
    "prod": "fill:#ffebee,stroke:#e53935,color:#b71c1c,stroke-width:2px",
    "template": "fill:#fff8e1,stroke:#f9a825,color:#6d4c00",
    "missing": "fill:#f5f5f5,stroke:#9e9e9e,color:#616161,stroke-dasharray:4 3",
    "orphan": "fill:#fff3e0,stroke:#fb8c00,color:#e65100",
    "repository": "fill:#e0f2f1,stroke:#00897b,color:#004d40",
    "repository_disabled": "fill:#eeeeee,stroke:#9e9e9e,color:#757575",
    "external": "fill:#fafafa,stroke:#757575,color:#424242,stroke-dasharray:4 3",
    "service_connection": "fill:#fce4ec,stroke:#d81b60,color:#880e4f",
    "variable_group": "fill:#f3e5f5,stroke:#8e24aa,color:#4a148c",
    "keyvault_group": "fill:#f3e5f5,stroke:#8e24aa,color:#4a148c",
    "keyvault": "fill:#e8eaf6,stroke:#3949ab,color:#1a237e",
    "environment": "fill:#e8f5e9,stroke:#43a047,color:#1b5e20",
    "resource": "fill:#f1f8e9,stroke:#7cb342,color:#33691e",
    "pool": "fill:#eceff1,stroke:#546e7a,color:#263238",
    "admin_group": "fill:#ffebee,stroke:#e53935,color:#b71c1c",
    "group": "fill:#e3f2fd,stroke:#1e88e5,color:#0d47a1",
    "service_identity": "fill:#fff8e1,stroke:#f9a825,color:#6d4c00",
    "page": "fill:#e3f2fd,stroke:#1e88e5,color:#0d47a1",
    "work_item_type": "fill:#e0f2f1,stroke:#00897b,color:#004d40",
}

ARROWS = {"solid": "-->", "dashed": "-.->", "thick": "==>"}


def escape_label(text: str, max_len: int = 80) -> str:
    lines = []
    for line in str(text).split("\n"):
        line = line if len(line) <= max_len else line[: max_len - 1] + "…"
        line = line.replace(";", ",").replace("#", "").replace("`", "'")
        line = line.replace("[", "(").replace("]", ")").replace("{", "(").replace("}", ")").replace("|", "/")
        line = line.replace('"', "#quot;").replace("<", "#lt;").replace(">", "#gt;")
        lines.append(line)
    return "<br/>".join(lines)


def _class_name(node_type: str) -> str:
    return re.sub(r"[^A-Za-z0-9_]", "_", node_type or "default")


def render(graph: Graph, max_nodes: int = 200, max_edges: int = 400) -> str:
    """Render a Graph as a Mermaid ``graph`` definition."""
    ids: dict[str, str] = {}
    node_list = list(graph.nodes.values())
    truncated = len(node_list) > max_nodes
    node_list = node_list[:max_nodes]
    for index, node in enumerate(node_list):
        ids[node.id] = f"n{index}"

    lines = [f"graph {graph.direction}"]
    grouped: dict[str, list] = {}
    for node in node_list:
        grouped.setdefault(node.group or "", []).append(node)

    def node_line(node) -> str:
        left, right = SHAPES.get(node.type, ('["', '"]'))
        return f"    {ids[node.id]}{left}{escape_label(node.label)}{right}"

    for group_index, (group, nodes) in enumerate(grouped.items()):
        if group:
            lines.append(f'    subgraph g{group_index}["{escape_label(group)}"]')
            lines.extend("  " + node_line(n) for n in nodes)
            lines.append("    end")
        else:
            lines.extend(node_line(n) for n in nodes)
    if truncated:
        lines.append(f'    more["… {len(graph.nodes) - max_nodes} more nodes omitted"]')

    edge_count = 0
    for edge in graph.edges:
        if edge.source not in ids or edge.target not in ids:
            continue
        if edge_count >= max_edges:
            lines.append(f"    %% {len(graph.edges) - max_edges} more edges omitted")
            break
        arrow = ARROWS.get(edge.style, "-->")
        # Edge text is unquoted, where brackets are syntax - drop them (node labels are quoted and keep them).
        text = re.sub(r"[()]", "", escape_label(edge.label, 40)) if edge.label else ""
        label = f"|{text}|" if text else ""
        lines.append(f"    {ids[edge.source]} {arrow}{label} {ids[edge.target]}")
        edge_count += 1

    used = sorted({n.type for n in node_list if n.type in CLASS_DEFS})
    for node_type in used:
        lines.append(f"    classDef {_class_name(node_type)} {CLASS_DEFS[node_type]}")
    for node_type in used:
        members = ",".join(ids[n.id] for n in node_list if n.type == node_type)
        lines.append(f"    class {members} {_class_name(node_type)}")
    return "\n".join(lines)


def fenced(graph: Graph, flavour: str = "markdown", **kw) -> str:
    """Wrap a diagram for Markdown (```mermaid) or Azure DevOps wiki (::: mermaid)."""
    body = render(graph, **kw)
    if flavour == "azure":
        return f"::: mermaid\n{body}\n:::"
    return f"```mermaid\n{body}\n```"
