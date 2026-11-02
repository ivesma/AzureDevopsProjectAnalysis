"""Azure Boards: work item types, backlog hierarchy and work items."""

from __future__ import annotations

from azdo_analyser.core.base import Collector
from azdo_analyser.core.client import seg
from azdo_analyser.core.context import RunContext
from azdo_analyser.core.plugin import register_collector
from azdo_analyser.core.utils import chunks, identity_name

FIELDS = [
    "System.Id", "System.WorkItemType", "System.Title", "System.State", "System.AssignedTo",
    "System.CreatedDate", "System.ChangedDate", "Microsoft.VSTS.Common.ClosedDate",
    "Microsoft.VSTS.Common.ActivatedDate", "Microsoft.VSTS.Common.StateChangeDate",
    "System.AreaPath", "System.IterationPath", "System.Tags", "System.Parent",
    "Microsoft.VSTS.Common.Priority", "Microsoft.VSTS.Scheduling.StoryPoints", "System.Reason",
]


@register_collector("boards", order=90, depends_on=("project",))
class BoardsCollector(Collector):
    """Work item types & states, backlog hierarchy and work item details (for flow metrics)."""

    section = "boards"

    def collect(self, ctx: RunContext) -> dict:
        c, p, s = ctx.client, ctx.project, ctx.settings
        types = ctx.safe("work-item-types", lambda: c.get_all(f"{p}/_apis/wit/workitemtypes"), [])
        type_records, category_of = [], {}
        for t in types:
            states = ctx.safe(f"states:{t.get('name')}",
                              lambda tt=t: c.get_all(f"{p}/_apis/wit/workitemtypes/{seg(tt['name'])}/states"), [])
            for st in states:
                category_of[(t.get("name"), st.get("name"))] = st.get("category")
            type_records.append({
                "name": t.get("name"),
                "reference_name": t.get("referenceName"),
                "is_disabled": bool(t.get("isDisabled")),
                "states": [{"name": st.get("name"), "category": st.get("category")} for st in states],
            })

        team = (ctx.snapshot.get("project") or {}).get("default_team")
        hierarchy = []
        if team:
            backlog = ctx.safe("backlog-configuration",
                               lambda: c.get(f"{p}/{seg(team)}/_apis/work/backlogconfiguration"), {})
            levels = list(reversed(backlog.get("portfolioBacklogs") or []))
            levels += [backlog.get("requirementBacklog"), backlog.get("taskBacklog")]
            for level in filter(None, levels):
                hierarchy.append({"name": level.get("name"),
                                  "types": [w.get("name") for w in level.get("workItemTypes") or []]})

        wiql = {
            "query": "SELECT [System.Id] FROM WorkItems WHERE [System.TeamProject] = @project "
                     "AND [System.WorkItemType] <> '' ORDER BY [System.ChangedDate] DESC"
        }
        ids_result = ctx.safe("wiql", lambda: c.post(f"{p}/_apis/wit/wiql", wiql, params={"$top": s.max_work_items}), {})
        ids = [w["id"] for w in ids_result.get("workItems", [])][: s.max_work_items]
        oldest = ctx.safe("wiql-oldest", lambda: c.post(
            f"{p}/_apis/wit/wiql",
            {"query": "SELECT [System.Id], [System.CreatedDate] FROM WorkItems WHERE [System.TeamProject] = @project "
                      "ORDER BY [System.CreatedDate] ASC"},
            params={"$top": 1}), {})

        items = []
        for batch in chunks(ids, 200):
            data = ctx.safe("work-items-batch",
                            lambda b=batch: c.post("_apis/wit/workitemsbatch", {"ids": b, "fields": FIELDS}), {})
            for wi in data.get("value", []):
                f = wi.get("fields", {})
                wtype, state = f.get("System.WorkItemType"), f.get("System.State")
                items.append({
                    "id": wi.get("id"),
                    "type": wtype,
                    "title": f.get("System.Title"),
                    "state": state,
                    "state_category": category_of.get((wtype, state)),
                    "reason": f.get("System.Reason"),
                    "assigned_to": identity_name(f.get("System.AssignedTo")),
                    "created": f.get("System.CreatedDate"),
                    "changed": f.get("System.ChangedDate"),
                    "activated": f.get("Microsoft.VSTS.Common.ActivatedDate"),
                    "closed": f.get("Microsoft.VSTS.Common.ClosedDate"),
                    "area": f.get("System.AreaPath"),
                    "iteration": f.get("System.IterationPath"),
                    "parent": f.get("System.Parent"),
                    "tags": [t.strip() for t in (f.get("System.Tags") or "").split(";") if t.strip()],
                    "priority": f.get("Microsoft.VSTS.Common.Priority"),
                    "story_points": f.get("Microsoft.VSTS.Scheduling.StoryPoints"),
                })
        oldest_ids = [w["id"] for w in oldest.get("workItems", [])]
        return {
            "work_item_types": type_records,
            "backlog_hierarchy": hierarchy,
            "work_items": items,
            "total_ids": len(ids_result.get("workItems", [])),
            "truncated": len(ids_result.get("workItems", [])) >= s.max_work_items,
            "oldest_work_item_id": oldest_ids[0] if oldest_ids else None,
        }
