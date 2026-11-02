"""Wikis: page hierarchy, content links, view statistics and ownership."""

from __future__ import annotations

import posixpath
import re
from urllib.parse import unquote

from azdo_analyser.core.base import Collector
from azdo_analyser.core.context import RunContext
from azdo_analyser.core.plugin import register_collector

LINK_RE = re.compile(r"(?<!!)\[[^\]]*\]\(\s*<?([^)\s>]+)>?(?:\s+\"[^\"]*\")?\s*\)")


def normalise_page_path(path: str) -> str:
    path = unquote(path).split("#", 1)[0].split("?", 1)[0]
    if path.lower().endswith(".md"):
        path = path[:-3]
    path = path.replace("-", " ").replace("\\", "/")
    path = re.sub(r"/+", "/", path)
    return ("/" + path.strip("/")).lower()


def extract_links(content: str, page_path: str) -> list[str]:
    """Return normalised internal wiki link targets found in Markdown content."""
    links = []
    for target in LINK_RE.findall(content or ""):
        if re.match(r"^[a-z][a-z0-9+.-]*:", target, re.I) or target.startswith("#"):
            continue
        if "/.attachments/" in target or target.startswith(".attachments"):
            continue
        if not target.startswith("/"):
            target = posixpath.normpath(posixpath.join(posixpath.dirname(page_path) or "/", target))
        links.append(normalise_page_path(target))
    return links


def flatten_pages(node: dict, out: list[dict], depth: int = 0) -> list[dict]:
    if node.get("path") and node.get("path") != "/":
        out.append({
            "id": node.get("id"),
            "path": node.get("path"),
            "order": node.get("order"),
            "git_item_path": node.get("gitItemPath"),
            "is_parent": bool(node.get("isParentPage") or node.get("subPages")),
            "depth": depth,
        })
    for child in node.get("subPages") or []:
        flatten_pages(child, out, depth + 1)
    return out


@register_collector("wiki", order=100, depends_on=("project",))
class WikiCollector(Collector):
    """Project & code wikis with page tree, links, page views and last authors."""

    section = "wiki"

    def collect(self, ctx: RunContext) -> dict:
        c, p = ctx.client, ctx.project
        wikis = ctx.safe("wikis", lambda: c.get_all(f"{p}/_apis/wiki/wikis"), [])
        results = []
        budget = ctx.settings.max_wiki_pages
        for wiki in wikis:
            wid = wiki["id"]
            base = f"{p}/_apis/wiki/wikis/{wid}/pages"
            tree = ctx.safe(f"pages:{wiki.get('name')}",
                            lambda: c.get(base, {"path": "/", "recursionLevel": "full"}), {})
            pages = flatten_pages(tree, [])
            views = {}
            stats = ctx.safe(f"page-stats:{wiki.get('name')}",
                             lambda: self._page_stats(ctx, wid), [])
            for st in stats:
                views[st.get("path")] = sum(v.get("count", 0) for v in st.get("viewStats") or [])
            for page in pages:
                page["view_count_30d"] = views.get(page["path"], 0 if stats else None)
                page["links"] = []
                page["last_author"] = page["last_modified"] = None
                if budget <= 0:
                    page["content_scanned"] = False
                    continue
                budget -= 1
                data = ctx.safe(f"page:{page['path']}",
                                lambda pg=page: c.get(base, {"path": pg["path"], "includeContent": "true"}), {})
                page["links"] = extract_links(data.get("content", ""), page["path"])
                page["content_length"] = len(data.get("content", "") or "")
                page["content_scanned"] = True
                if wiki.get("repositoryId") and page.get("git_item_path"):
                    commits = ctx.safe(
                        f"page-history:{page['path']}",
                        lambda pg=page: c.get(
                            f"{p}/_apis/git/repositories/{wiki['repositoryId']}/commits",
                            {"searchCriteria.itemPath": pg["git_item_path"], "searchCriteria.$top": 1,
                             **self._version(wiki)},
                        ).get("value", []),
                        [],
                    )
                    if commits:
                        page["last_author"] = (commits[0].get("author") or {}).get("name")
                        page["last_modified"] = (commits[0].get("author") or {}).get("date")
            results.append({
                "id": wid,
                "name": wiki.get("name"),
                "type": wiki.get("type"),
                "repository_id": wiki.get("repositoryId"),
                "mapped_path": wiki.get("mappedPath"),
                "url": wiki.get("remoteUrl"),
                "pages": pages,
            })
        return {"wikis": results}

    @staticmethod
    def _version(wiki: dict) -> dict:
        versions = wiki.get("versions") or []
        if wiki.get("type") == "codeWiki" and versions:
            return {"searchCriteria.itemVersion.version": versions[0].get("version")}
        return {}

    @staticmethod
    def _page_stats(ctx: RunContext, wiki_id: str) -> list[dict]:
        out, token = [], None
        while True:
            body = {"top": 100, "pageViewsForDays": 30}
            if token:
                body["continuationToken"] = token
            resp = ctx.client.request("POST", f"{ctx.project}/_apis/wiki/wikis/{wiki_id}/pagesbatch", json=body)
            data = resp.json() if resp.content else {}
            batch = data.get("value", [])
            out.extend(batch)
            token = resp.headers.get("x-ms-continuationtoken")
            if not token or not batch:
                return out
