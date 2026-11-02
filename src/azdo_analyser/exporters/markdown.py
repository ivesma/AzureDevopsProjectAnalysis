"""Generic Markdown documentation (```mermaid fences, renders on GitHub / VS Code)."""

from __future__ import annotations

from pathlib import Path

from azdo_analyser.core.base import Exporter
from azdo_analyser.core.config import Settings
from azdo_analyser.core.context import RunContext
from azdo_analyser.core.plugin import register_exporter
from azdo_analyser.core.utils import safe_filename
from azdo_analyser.exporters.docs_builder import DocsBuilder


@register_exporter("markdown", order=40)
class MarkdownExporter(Exporter):
    """Markdown/ documentation tree with README index."""

    folder = "Markdown"

    def enabled(self, settings: Settings) -> bool:
        return settings.generate_markdown

    def export(self, ctx: RunContext, output_dir: Path) -> list[Path]:
        target = self.target(output_dir)
        root = DocsBuilder(ctx, "markdown").build()
        files, index = [], [f"# {root.title}", ""]
        for parents, page in root.walk():
            rel_parts = [safe_filename(p) for p in parents[1:]]
            if page is root:
                rel = Path("README.md")
            elif page.children:
                rel = Path(*rel_parts, safe_filename(page.title), "README.md")
            else:
                rel = Path(*rel_parts, f"{safe_filename(page.title)}.md")
            path = target / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(page.content, encoding="utf-8")
            files.append(path)
            if page is not root:
                indent = "  " * (len(parents) - 1)
                index.append(f"{indent}- [{page.title}]({rel.as_posix().replace(' ', '%20')})")
        (target / "INDEX.md").write_text("\n".join(index) + "\n", encoding="utf-8")
        files.append(target / "INDEX.md")
        return files
