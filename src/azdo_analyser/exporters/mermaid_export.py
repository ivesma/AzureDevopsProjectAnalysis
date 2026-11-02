"""Writes every graph as Mermaid (.mmd), plus draw.io (.drawio) and Graphviz (.dot) files."""

from __future__ import annotations

from pathlib import Path

from azdo_analyser.core.base import Exporter
from azdo_analyser.core.config import Settings
from azdo_analyser.core.context import RunContext
from azdo_analyser.core.plugin import register_exporter
from azdo_analyser.core.utils import safe_filename
from azdo_analyser.visualisers import drawio, mermaid


@register_exporter("mermaid", order=20)
class MermaidExporter(Exporter):
    """Mermaid/<category>/*.mmd, DrawIO/<category>/*.drawio and Graphviz/<category>/*.dot."""

    folder = "Mermaid"

    def enabled(self, settings: Settings) -> bool:
        return settings.generate_mermaid or settings.generate_drawio

    def export(self, ctx: RunContext, output_dir: Path) -> list[Path]:
        files: list[Path] = []
        index = ["# Diagram index", ""]
        for graph in sorted(ctx.assessment.graphs.values(), key=lambda g: (g.category, g.name)):
            if graph.is_empty:
                continue
            name = safe_filename(graph.name)
            if ctx.settings.generate_mermaid:
                folder = output_dir / "Mermaid" / graph.category
                folder.mkdir(parents=True, exist_ok=True)
                path = folder / f"{name}.mmd"
                path.write_text(mermaid.render(graph) + "\n", encoding="utf-8")
                files.append(path)
                index.append(f"- [{graph.title}]({graph.category}/{name}.mmd) - {graph.description}")
            if ctx.settings.generate_drawio:
                for ext, fn, root in (("drawio", drawio.to_drawio, "DrawIO"), ("dot", drawio.to_dot, "Graphviz")):
                    folder = output_dir / root / graph.category
                    folder.mkdir(parents=True, exist_ok=True)
                    path = folder / f"{name}.{ext}"
                    path.write_text(fn(graph), encoding="utf-8")
                    files.append(path)
        if ctx.settings.generate_mermaid:
            (output_dir / "Mermaid").mkdir(parents=True, exist_ok=True)
            readme = output_dir / "Mermaid" / "README.md"
            readme.write_text("\n".join(index) + "\n", encoding="utf-8")
            files.append(readme)
        return files
