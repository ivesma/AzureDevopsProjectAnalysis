"""Azure DevOps Wiki export: writes wiki-formatted files and optionally publishes them via the Wiki API.

Files follow the Git layout of an Azure DevOps wiki (``Page-Name.md`` + ``Page-Name/`` for children and
``.order`` files), so the ``Wiki`` folder can also be pushed into a code wiki repository directly.
Diagrams use the ``::: mermaid`` syntax supported by Azure DevOps wikis.
"""

from __future__ import annotations

import logging
from pathlib import Path
from urllib.parse import quote

from azdo_analyser.core.base import Exporter
from azdo_analyser.core.client import AzDoError, NotFound
from azdo_analyser.core.config import Settings
from azdo_analyser.core.context import RunContext
from azdo_analyser.core.plugin import register_exporter
from azdo_analyser.exporters.docs_builder import DocsBuilder, Page

log = logging.getLogger(__name__)


def wiki_file_name(title: str) -> str:
    """Azure DevOps wiki Git naming: '-' -> %2D, spaces -> '-', other specials percent-encoded."""
    return quote(title.replace("-", "%2D"), safe="%() ,.'&!+=@$~").replace("%252D", "%2D").replace(" ", "-")


class WikiPublisher:
    def __init__(self, ctx: RunContext):
        self.ctx = ctx
        self.client = ctx.client

    def wiki_id(self) -> str:
        c, p = self.client, self.ctx.project
        wikis = c.get_all(f"{p}/_apis/wiki/wikis")
        name = self.ctx.settings.wiki_name
        for wiki in wikis:
            if (name and wiki.get("name") == name) or (not name and wiki.get("type") == "projectWiki"):
                return wiki["id"]
        if name:
            raise AzDoError(f"Wiki '{name}' not found")
        project = self.ctx.snapshot.get("project") or {}
        created = c.post("_apis/wiki/wikis", {"name": f"{self.ctx.settings.project}.wiki", "type": "projectWiki",
                                              "projectId": project.get("id")})
        return created["id"]

    def put_page(self, wiki_id: str, path: str, content: str) -> None:
        base = f"{self.ctx.project}/_apis/wiki/wikis/{wiki_id}/pages"
        headers = {}
        try:
            existing = self.client.request("GET", base, params={"path": path})
            etag = existing.headers.get("ETag")
            if etag:
                headers["If-Match"] = etag
        except NotFound:
            pass
        self.client.request("PUT", base, params={"path": path}, json={"content": content}, headers=headers)

    def publish(self, root: Page) -> int:
        wiki_id = self.wiki_id()
        count = 0
        for parents, page in root.walk():  # parents before children
            path = "/" + "/".join(parents + (page.title,))
            self.put_page(wiki_id, path, page.content)
            count += 1
        return count


@register_exporter("wiki", order=60)
class WikiExporter(Exporter):
    """Wiki/ (Azure DevOps wiki layout) and optional publishing to the project wiki."""

    folder = "Wiki"

    def enabled(self, settings: Settings) -> bool:
        return settings.generate_markdown or settings.wiki_publish

    def export(self, ctx: RunContext, output_dir: Path) -> list[Path]:
        target = self.target(output_dir)
        root = DocsBuilder(ctx, "azure").build()
        files = self.write_tree(target, root)
        if ctx.settings.wiki_publish:
            if ctx.client is None:
                ctx.record("wiki-publish", "skipped", "no Azure DevOps connection (demo/offline mode)")
            else:
                count = ctx.safe("wiki-publish", lambda: WikiPublisher(ctx).publish(root), 0)
                if count:
                    ctx.record("wiki-publish", "ok", f"published {count} pages")
        return files

    @staticmethod
    def write_tree(folder: Path, page: Page) -> list[Path]:
        folder.mkdir(parents=True, exist_ok=True)
        name = wiki_file_name(page.title)
        path = folder / f"{name}.md"
        path.write_text(page.content, encoding="utf-8")
        files = [path]
        if page.children:
            child_dir = folder / name
            child_dir.mkdir(parents=True, exist_ok=True)
            (child_dir / ".order").write_text("\n".join(wiki_file_name(c.title) for c in page.children) + "\n",
                                              encoding="utf-8")
            for child in page.children:
                files += WikiExporter.write_tree(child_dir, child)
        return files
