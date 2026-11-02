import xml.etree.ElementTree as ET

from azdo_analyser.core.models import Graph
from azdo_analyser.visualisers import drawio, mermaid


def sample_graph() -> Graph:
    g = Graph("g", "Sample", "dependencies")
    g.add_node("a", 'Build "app" [x]; #1', "pipeline")
    g.add_node("b", "Deploy\nProd", "prod", group="Stage | 1")
    g.add_node("c", "Tpl", "template")
    g.add_edge("a", "b", "deploys (CD)")
    g.add_edge("a", "b", "deploys (CD)")  # duplicate ignored
    g.add_edge("b", "c", None, "dashed")
    g.add_edge("c", "a", "loop", "thick")
    g.add_edge("a", "missing")
    return g


def test_mermaid_render():
    text = mermaid.render(sample_graph())
    assert text.startswith("graph LR")
    assert 'n0["Build #quot;app#quot; (x), 1"]' in text
    assert 'n1{{"Deploy<br/>Prod"}}' in text and 'subgraph g1["Stage / 1"]' in text
    assert "n0 -->|deploys CD| n1" in text and "n1 -.-> n2" in text and "n2 ==>|loop| n0" in text
    assert text.count("n0 -->") == 1  # edge to unknown node skipped, duplicate removed
    assert "classDef prod" in text and "class n1 prod" in text
    assert mermaid.fenced(sample_graph(), "azure").startswith("::: mermaid\ngraph")
    assert mermaid.fenced(sample_graph()).startswith("```mermaid\n")


def test_mermaid_truncation():
    g = Graph("big", "Big", "x")
    for i in range(10):
        g.add_node(str(i), "x" * 100)
        if i:
            g.add_edge(str(i - 1), str(i))
    text = mermaid.render(g, max_nodes=5, max_edges=2)
    assert "5 more nodes omitted" in text and "more edges omitted" in text and "…" in text


def test_drawio_and_dot():
    g = sample_graph()
    root = ET.fromstring(drawio.to_drawio(g).split("\n", 1)[1])
    cells = root.findall(".//mxCell")
    assert sum(1 for c in cells if c.get("vertex")) == 3 and sum(1 for c in cells if c.get("edge")) == 3
    td = Graph("td", "TD", "x", direction="TD")
    td.add_node("x", "X")
    assert "mxGraphModel" in drawio.to_drawio(td)
    dot = drawio.to_dot(g)
    assert dot.startswith('digraph "g"') and "style=dashed" in dot and "penwidth=2" in dot
    assert '\\"app\\"' in dot


def test_graph_helpers():
    g = sample_graph()
    assert not g.is_empty and ("Build \"app\" [x]; #1", "Tpl") not in g.edge_set()
    assert g.to_dict()["nodes"][0]["id"] == "a"
    assert drawio.layers(g)["a"] == 0
