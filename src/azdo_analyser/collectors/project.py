"""Project metadata and teams."""

from __future__ import annotations

from azdo_analyser.core.base import Collector
from azdo_analyser.core.context import RunContext
from azdo_analyser.core.plugin import register_collector
from azdo_analyser.core.utils import identity_name


@register_collector("project", order=10)
class ProjectCollector(Collector):
    """Project metadata, process template and teams."""

    section = "project"
    fatal = True

    def collect(self, ctx: RunContext) -> dict:
        c = ctx.client
        project = c.get(f"_apis/projects/{ctx.project}", {"includeCapabilities": "true"})
        pid = project["id"]
        caps = project.get("capabilities", {})
        props = ctx.safe(
            "project-properties",
            lambda: c.get(f"_apis/projects/{pid}/properties", api_version="7.1-preview.1").get("value", []),
            [],
        )
        properties = {p.get("name"): p.get("value") for p in props}

        teams = []
        for team in ctx.safe("teams", lambda: c.get_all(f"_apis/projects/{pid}/teams", {"$top": 500}), []):
            members = ctx.safe(
                f"team-members:{team.get('name')}",
                lambda t=team: c.get_all(f"_apis/projects/{pid}/teams/{t['id']}/members", {"$top": 1000}),
                [],
            )
            teams.append({
                "id": team.get("id"),
                "name": team.get("name"),
                "description": team.get("description") or "",
                "member_count": len(members),
                "members": sorted(filter(None, (identity_name(m.get("identity")) for m in members))),
                "admins": sorted(identity_name(m.get("identity")) for m in members if m.get("isTeamAdmin")),
            })

        org_url = ctx.settings.organisation_url
        return {
            "id": pid,
            "name": project.get("name"),
            "description": project.get("description") or "",
            "url": project.get("url"),
            "web_url": f"{org_url}/{ctx.project}",
            "state": project.get("state"),
            "visibility": project.get("visibility"),
            "revision": project.get("revision"),
            "last_update_time": project.get("lastUpdateTime"),
            "process_template": caps.get("processTemplate", {}).get("templateName")
            or properties.get("System.Process Template"),
            "process_template_id": caps.get("processTemplate", {}).get("templateTypeId"),
            "source_control": caps.get("versioncontrol", {}).get("sourceControlType"),
            "default_team": (project.get("defaultTeam") or {}).get("name"),
            "organisation": ctx.settings.organisation_name,
            "organisation_url": org_url,
            "properties": properties,
            "teams": teams,
        }
