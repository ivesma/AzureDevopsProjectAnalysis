"""Azure Artifacts: feeds, upstream sources, packages and retention."""

from __future__ import annotations

from azdo_analyser.core.base import Collector
from azdo_analyser.core.context import RunContext
from azdo_analyser.core.plugin import register_collector
from azdo_analyser.core.utils import age_days, identity_name

PUBLIC_UPSTREAMS = ("nuget.org", "npmjs", "pypi.org", "maven.org", "crates.io", "powershellgallery")


@register_collector("artifacts", order=120, depends_on=("project",))
class ArtifactsCollector(Collector):
    """Project- and organisation-scoped feeds with packages, upstreams, permissions and retention."""

    section = "feeds"

    def collect(self, ctx: RunContext) -> list[dict]:
        c, p = ctx.client, ctx.project
        project_feeds = ctx.safe("project-feeds", lambda: c.get_all(f"{p}/_apis/packaging/feeds", service="feeds"), [])
        org_feeds = ctx.safe("org-feeds", lambda: c.get_all("_apis/packaging/feeds", service="feeds"), [])
        seen, results = set(), []
        for scope, feeds in (("project", project_feeds), ("organisation", org_feeds)):
            for feed in feeds:
                if feed["id"] in seen:
                    continue
                if scope == "organisation" and feed.get("project"):
                    continue
                seen.add(feed["id"])
                results.append(self._feed(ctx, feed, scope))
        return results

    def _feed(self, ctx: RunContext, feed: dict, scope: str) -> dict:
        c, p = ctx.client, ctx.project
        prefix = f"{p}/" if scope == "project" else ""
        base = f"{prefix}_apis/packaging/feeds/{feed['id']}"
        packages = ctx.safe(f"packages:{feed.get('name')}",
                            lambda: c.get_all(f"{base}/packages", {"includeAllVersions": "false", "$top": 1000},
                                              service="feeds", limit=5000), [])
        pkg_records = []
        for pkg in packages:
            latest = (pkg.get("versions") or [{}])[0]
            published = latest.get("publishDate")
            pkg_records.append({
                "id": pkg.get("id"),
                "name": pkg.get("name"),
                "protocol": pkg.get("protocolType"),
                "version": latest.get("version"),
                "publish_date": published,
                "age_days": round(age_days(published, ctx.now), 1) if published else None,
                "is_listed": latest.get("isListed", True),
            })
        permissions = ctx.safe(f"feed-permissions:{feed.get('name')}",
                               lambda: c.get_all(f"{base}/permissions", service="feeds",
                                                 api_version="7.1-preview.1"), [])
        retention = ctx.safe(f"feed-retention:{feed.get('name')}",
                             lambda: c.get(f"{base}/retentionpolicies", service="feeds",
                                           api_version="7.1-preview.1"), {})
        upstreams = []
        for up in feed.get("upstreamSources") or []:
            location = up.get("location") or ""
            upstreams.append({
                "name": up.get("name"),
                "protocol": up.get("protocol"),
                "location": location,
                "type": "public" if up.get("upstreamSourceType") == "public"
                or any(x in location for x in PUBLIC_UPSTREAMS) else "internal",
                "status": up.get("status"),
            })
        return {
            "id": feed["id"],
            "name": feed.get("name"),
            "scope": scope,
            "description": feed.get("description") or "",
            "upstream_enabled": bool(feed.get("upstreamEnabled", bool(upstreams))),
            "upstream_sources": upstreams,
            "hide_deleted_versions": feed.get("hideDeletedPackageVersions"),
            "badges_enabled": feed.get("badgesEnabled"),
            "packages": pkg_records,
            "package_count": len(pkg_records),
            "retention_count_limit": retention.get("countLimit"),
            "retention_days": retention.get("daysToKeepRecentlyDownloadedPackages"),
            "permissions": [
                {"identity": identity_name(x.get("identityDescriptor")) or x.get("displayName")
                 or x.get("identityDescriptor"), "role": x.get("role")}
                for x in permissions
            ],
            "web_url": f"{ctx.settings.organisation_url}/{p}/_artifacts/feed/{feed.get('name')}",
        }
