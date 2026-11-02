"""Shared data model: findings, graphs and the overall assessment container."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any


class Severity(str, Enum):
    CRITICAL = "CRITICAL"
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"
    INFO = "INFO"

    @property
    def weight(self) -> int:
        return {"CRITICAL": 25, "HIGH": 10, "MEDIUM": 4, "LOW": 1, "INFO": 0}[self.value]

    @property
    def rank(self) -> int:
        return ["CRITICAL", "HIGH", "MEDIUM", "LOW", "INFO"].index(self.value)


@dataclass
class Finding:
    rule_id: str
    title: str
    severity: Severity
    category: str
    resource_type: str
    resource: str
    description: str
    impact: str
    recommendation: str
    remediation: list[str] = field(default_factory=list)
    evidence: dict[str, Any] = field(default_factory=dict)
    url: str | None = None

    @property
    def id(self) -> str:
        return f"{self.rule_id}:{self.resource_type}:{self.resource}"

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["severity"] = self.severity.value
        data["id"] = self.id
        return data


@dataclass
class Node:
    id: str
    label: str
    type: str = "default"
    group: str | None = None
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass
class Edge:
    source: str
    target: str
    label: str | None = None
    style: str = "solid"  # solid | dashed | thick


@dataclass
class Graph:
    """A directed graph that every visualiser (Mermaid, draw.io, DOT) can render."""

    name: str
    title: str
    category: str  # pipelines | boards | dependencies | environments | wiki | security
    direction: str = "LR"
    nodes: dict[str, Node] = field(default_factory=dict)
    edges: list[Edge] = field(default_factory=list)
    description: str = ""

    def add_node(self, node_id: str, label: str | None = None, type: str = "default",
                 group: str | None = None, **meta: Any) -> str:
        if node_id not in self.nodes:
            self.nodes[node_id] = Node(node_id, label or node_id, type, group, meta)
        return node_id

    def add_edge(self, source: str, target: str, label: str | None = None, style: str = "solid") -> None:
        edge = Edge(source, target, label, style)
        if not any(e.source == source and e.target == target and e.label == label for e in self.edges):
            self.edges.append(edge)

    @property
    def is_empty(self) -> bool:
        return not self.nodes

    def edge_set(self) -> set[tuple[str, str]]:
        return {(self.nodes[e.source].label if e.source in self.nodes else e.source,
                 self.nodes[e.target].label if e.target in self.nodes else e.target) for e in self.edges}

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "title": self.title,
            "category": self.category,
            "direction": self.direction,
            "description": self.description,
            "nodes": [asdict(n) for n in self.nodes.values()],
            "edges": [asdict(e) for e in self.edges],
        }


@dataclass
class CoverageEntry:
    collector: str
    area: str
    status: str  # ok | denied | not_found | error | skipped
    message: str = ""


@dataclass
class Assessment:
    """Everything collected and derived during a run."""

    organisation: str
    project: str
    generated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    snapshot: dict[str, Any] = field(default_factory=dict)
    analysis: dict[str, Any] = field(default_factory=dict)
    findings: list[Finding] = field(default_factory=list)
    graphs: dict[str, Graph] = field(default_factory=dict)
    coverage: list[CoverageEntry] = field(default_factory=list)

    def add_graph(self, graph: Graph) -> Graph:
        self.graphs[graph.name] = graph
        return graph

    def graphs_in(self, category: str) -> list[Graph]:
        return [g for g in self.graphs.values() if g.category == category]

    def sorted_findings(self) -> list[Finding]:
        return sorted(self.findings, key=lambda f: (f.severity.rank, f.category, f.resource))

    def severity_counts(self) -> dict[str, int]:
        counts = {s.value: 0 for s in Severity}
        for f in self.findings:
            counts[f.severity.value] += 1
        return counts

    def section(self, key: str, default: Any = None) -> Any:
        value = self.snapshot.get(key)
        return default if value is None else value
