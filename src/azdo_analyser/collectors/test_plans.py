"""Test plans, suites, test cases (automation status) and recent test runs."""

from __future__ import annotations

from datetime import timedelta

from azdo_analyser.core.base import Collector
from azdo_analyser.core.context import RunContext
from azdo_analyser.core.plugin import register_collector
from azdo_analyser.core.utils import identity_name

AUTOMATION_FIELD = "Microsoft.VSTS.TCM.AutomationStatus"


def _field(work_item: dict, name: str):
    for entry in work_item.get("workItemFields") or []:
        if name in entry:
            return entry[name]
    return None


@register_collector("test_plans", order=110, depends_on=("project",))
class TestPlanCollector(Collector):
    """Test plans, suites and test case automation coverage."""

    section = "test_plans"

    def collect(self, ctx: RunContext) -> dict:
        c, p = ctx.client, ctx.project
        plans = ctx.safe("test-plans", lambda: c.get_all(f"{p}/_apis/testplan/plans", {"includePlanDetails": "true"}), [])
        results = []
        for plan in plans:
            pid = plan["id"]
            suites = ctx.safe(f"suites:{plan.get('name')}",
                              lambda: c.get_all(f"{p}/_apis/testplan/Plans/{pid}/suites"), [])
            suite_records, seen_cases = [], {}
            for suite in suites:
                cases = ctx.safe(
                    f"test-cases:{suite.get('name')}",
                    lambda st=suite: c.get_all(f"{p}/_apis/testplan/Plans/{pid}/Suites/{st['id']}/TestCase"),
                    [],
                )
                automated = 0
                for case in cases:
                    wi = case.get("workItem") or {}
                    status = _field(wi, AUTOMATION_FIELD) or "Not Automated"
                    seen_cases[wi.get("id")] = status
                    automated += status == "Automated"
                suite_records.append({
                    "id": suite.get("id"),
                    "name": suite.get("name"),
                    "type": suite.get("suiteType"),
                    "parent_id": (suite.get("parentSuite") or {}).get("id"),
                    "test_case_count": len(cases),
                    "automated_count": automated,
                })
            total = len(seen_cases)
            automated_total = sum(1 for s in seen_cases.values() if s == "Automated")
            results.append({
                "id": pid,
                "name": plan.get("name"),
                "state": plan.get("state"),
                "area": plan.get("areaPath"),
                "iteration": plan.get("iteration"),
                "owner": identity_name(plan.get("owner")),
                "start_date": plan.get("startDate"),
                "end_date": plan.get("endDate"),
                "suites": suite_records,
                "test_case_count": total,
                "automated_count": automated_total,
                "automation_coverage": round(automated_total / total, 3) if total else None,
            })

        since = (ctx.now - timedelta(days=ctx.settings.history_days)).isoformat()
        runs = ctx.safe("test-runs", lambda: c.get_all(
            f"{p}/_apis/test/runs",
            {"minLastUpdatedDate": since, "maxLastUpdatedDate": ctx.now.isoformat(), "$top": 500}, limit=2000), [])
        passed = sum(r.get("passedTests", 0) for r in runs)
        total = sum(r.get("totalTests", 0) for r in runs)
        return {
            "plans": results,
            "runs": {
                "count": len(runs),
                "automated": sum(1 for r in runs if r.get("isAutomated")),
                "total_tests": total,
                "passed_tests": passed,
                "pass_rate": round(passed / total, 3) if total else None,
            },
        }
