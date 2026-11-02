from datetime import datetime, timedelta, timezone

from azdo_analyser.analysers import risk_engine
from azdo_analyser.analysers.hierarchy_graphs import analyse_wiki_links, work_item_hierarchy
from azdo_analyser.analysers.metrics import is_hosted_build, peak_concurrency
from azdo_analyser.analysers.risk_engine import RiskEngine, build_recommendations, risk_score
from azdo_analyser.analysers.scoring import anti_patterns, complexity, naming_outliers, naming_style
from azdo_analyser.analysers.trends import compare, load_previous
from azdo_analyser.collectors.pipelines import normalise_triggers, run_stats
from azdo_analyser.collectors.repositories import (detect_technologies, normalise_policy, parse_gitmodules,
                                                   policy_applies, summarise_branch_policies)
from azdo_analyser.collectors.security import decode_bits, subject_kind
from azdo_analyser.collectors.wiki import extract_links, normalise_page_path
from azdo_analyser.core.models import Finding, Severity
from azdo_analyser.core.utils import parse_date, percentile, safe_filename, short_ref, slug, summarise

T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)


def test_utils():
    assert parse_date("2026-01-02T03:04:05.1234567Z").microsecond == 123456
    assert parse_date("0001-01-01T00:00:00") is None and parse_date("garbage") is None and parse_date(None) is None
    assert parse_date(datetime(2026, 1, 1)).tzinfo is not None
    assert percentile([1, 2, 3, 4], 50) == 2.5 and percentile([], 50) is None and percentile([7], 85) == 7
    assert summarise([1, 3])["mean"] == 2 and summarise([])["count"] == 0
    assert slug("A b/C") == "a-b-c" and safe_filename('a:b?') == "a_b_" and short_ref("refs/heads/x") == "x"


def test_peak_concurrency_and_hosted():
    h = timedelta(hours=1)
    assert peak_concurrency([(T0, T0 + 2 * h), (T0 + h, T0 + 3 * h), (T0 + 3 * h, T0 + 4 * h)]) == 2
    assert peak_concurrency([(T0, None)]) == 0
    assert is_hosted_build({"pool": "Azure Pipelines"}) and not is_hosted_build({"pool": "OnPrem"})
    assert is_hosted_build({"pool_hosted": False, "pool": "Azure Pipelines"}) is False


def test_policies():
    cfg = {"id": 1, "type": {"displayName": "Minimum number of reviewers"}, "isEnabled": True, "isBlocking": True,
           "settings": {"minimumApproverCount": 2, "creatorVoteCounts": True,
                        "scope": [{"repositoryId": None, "refName": "refs/heads/", "matchKind": "Prefix"}]}}
    p = normalise_policy(cfg)
    assert policy_applies(p, "r1", "refs/heads/main") and not policy_applies(p, "r1", None)
    exact = normalise_policy({**cfg, "settings": {"scope": [{"repositoryId": "r2", "refName": "refs/heads/main"}]}})
    assert not policy_applies(exact, "r1", "refs/heads/main") and policy_applies(exact, "r2", "refs/heads/main")
    merge = normalise_policy({"type": {"displayName": "Require a merge strategy"}, "isEnabled": True,
                              "settings": {"allowSquash": True, "allowRebase": True}})
    build = normalise_policy({"type": {"displayName": "Build"}, "isEnabled": True, "settings": {"buildDefinitionId": 9}})
    s = summarise_branch_policies([p, merge, build])
    assert s["min_reviewers"] == 2 and s["creator_vote_counts"] and s["build_validation_definitions"] == [9]
    assert s["merge_strategies"] == ["squash", "rebase"] and s["blocking_count"] == 1


def test_repo_helpers():
    mods = parse_gitmodules('[submodule "a"]\n path = a\n url = https://x/_git/lib\n')
    assert mods == [{"name": "a", "path": "a", "url": "https://x/_git/lib"}]
    assert detect_technologies(["/src/x.csproj", "/Dockerfile", "/infra/main.tf"]) == [".NET", "Docker", "Terraform"]


def test_pipeline_helpers():
    triggers, schedules = normalise_triggers([
        {"triggerType": "schedule", "schedules": [{"branchFilters": ["+main"], "startHours": 3, "startMinutes": 5}]},
        {"triggerType": "buildCompletion", "definition": {"id": 2, "name": "up"}},
    ])
    assert schedules[0]["time"] == "03:05" and triggers[1]["source_definition"] == "up"
    stats = run_stats([
        {"result": "succeeded", "queueTime": "2026-01-02T00:00:00Z", "startTime": "2026-01-02T00:00:00Z",
         "finishTime": "2026-01-02T00:10:00Z"},
        {"result": "failed", "queueTime": "2026-01-01T00:00:00Z"},
    ])
    assert stats["success_rate"] == 0.5 and stats["avg_duration_min"] == 10 and stats["last_result"] == "succeeded"
    assert run_stats([])["success_rate"] is None


def test_security_helpers():
    assert subject_kind({"descriptor": "svc.x"}) == "build_identity"
    assert subject_kind({"descriptor": "aadsp.x"}) == "service_principal"
    assert subject_kind({"descriptor": "vssgp.x"}) == "group"
    assert subject_kind({"descriptor": "aad.x", "subjectKind": "user"}) == "user"
    assert decode_bits(5, [{"bit": 1, "name": "A"}, {"bit": 2, "name": "B"}, {"bit": 4, "name": "C"}]) == ["A", "C"]


def test_wiki_links():
    assert normalise_page_path("/Parent/Child-Page.md#anchor") == "/parent/child page"
    links = extract_links("[a](/X) [b](../Y) [c](https://e.com) [d](#top) ![img](/.attachments/i.png) "
                          "[e](sub%20page)", "/Docs/Page")
    assert links == ["/x", "/y", "/docs/sub page"]
    wiki = {"pages": [
        {"path": "/A", "depth": 0, "links": ["/b", "/missing"], "content_scanned": True, "is_parent": True},
        {"path": "/A/B", "depth": 1, "links": [], "view_count_30d": 0},
        {"path": "/B", "depth": 0, "links": [], "view_count_30d": 3},
        {"path": "/A/C", "depth": 1, "links": [], "view_count_30d": 0},
    ]}
    broken, orphans, inbound = analyse_wiki_links(wiki)
    assert broken == {"/A": ["/missing"]} and orphans == {"/A/B", "/A/C"} and inbound == {"/B": 1}


def test_work_item_hierarchy_without_links():
    g = work_item_hierarchy({"work_items": [{"id": 1, "type": "Epic"}],
                             "backlog_hierarchy": [{"name": "Epics", "types": ["Epic"]},
                                                   {"name": "Features", "types": ["Feature"]}]})
    assert ("t:Epic", "t:Feature") in {(e.source, e.target) for e in g.edges}


def test_scoring():
    assert naming_style("my-pipe") == "kebab-case" and naming_style("My Pipe") == "Title Case"
    assert naming_style("MyPipe") == "PascalCase" and naming_style("a_b") == "snake_case"
    assert naming_style("??") == "other"
    dominant, outliers = naming_outliers(["a-b", "c-d", "e-f", "Some Name"])
    assert dominant == "kebab-case" and outliers == ["Some Name"]
    assert naming_outliers(["a", "B"]) == (None, [])
    assert naming_outliers(["a b", "C D", "e_f", "GH", "i.j"])[1] == []
    model = {"stages": [{"jobs": [{"step_count": 40, "condition": "x"}], "condition": "y"}] * 5,
             "templates": ["t"] * 6, "max_template_depth": 6, "parameters": [], "service_connections": [],
             "environments": [], "variables_inline": [{"name": f"v{i}"} for i in range(31)], "pipeline_resources": []}
    assert complexity(model, {})["band"] == "Very high"
    assert complexity(None, {"classic_steps": [{"phase": "a"}], "variables": []})["band"] == "Low"
    model.update({"root_key": "r:/p.yml", "trigger": "default", "pools": [{"vm_image": "ubuntu-latest"}],
                  "tasks": [{"task": "Foo", "version": None}, {"task": "AzureCLI", "version": "1"}],
                  "scripts": [{"kind": "script", "text": "\n" * 60 + "echo $(System.AccessToken)", "env": []}],
                  "unpinned_templates": ["a@b"], "unresolved_templates": ["x@y"], "parse_error": "bad",
                  "continue_on_error": 1, "persist_credentials": True})
    rules = {a["rule"] for a in anti_patterns({"name": "p"}, model, {"r:/p.yml": {"kind": "pipeline"}})}
    assert {"AP003", "AP004", "AP005", "AP007", "AP008", "AP011", "AP012", "AP013", "AP014", "AP015", "AP016",
            "AP020", "AP021"} <= rules


def _finding(sev, cat="Security", rule="X"):
    return Finding(rule, "t", sev, cat, "r", f"res-{sev.value}", "d", "i", "rec", ["s"])


def test_risk_score_and_recommendations():
    assert risk_score([])["score"] == 0 and risk_score([])["grade"] == "A"
    many = risk_score([_finding(Severity.HIGH)] * 10)
    assert many["score"] > risk_score([_finding(Severity.LOW)])["score"] and many["grade"] in ("D", "F")
    recs = build_recommendations([_finding(Severity.LOW, rule="A"), _finding(Severity.HIGH, rule="A"),
                                  _finding(Severity.MEDIUM, rule="B")])
    assert recs[0]["rule_id"] == "A" and recs[0]["severity"] == "HIGH" and recs[0]["occurrences"] == 2
    assert Severity.CRITICAL.weight > Severity.HIGH.weight and Severity.INFO.rank == 4


def test_risk_engine_rule_isolation(ctx, monkeypatch):
    def broken(_):
        raise RuntimeError("bug")

    monkeypatch.setitem(risk_engine.RULES, "ZZ999", broken)
    ctx.snapshot["repositories"] = [{"name": "r", "default_branch": "main", "web_url": None, "is_empty": False,
                                     "default_branch_policies": summarise_branch_policies([])}]
    RiskEngine().analyse(ctx)
    assert any(f.rule_id == "SC001" for f in ctx.assessment.findings)
    assert any(c.area == "rule:ZZ999" and c.status == "error" for c in ctx.assessment.coverage)


def test_service_connection_rules(ctx):
    ctx.snapshot["service_connections"] = [
        {"name": "old", "type": "azurerm", "auth_scheme": "ServicePrincipal", "execution_count": 2,
         "last_used": (ctx.now - timedelta(days=400)).isoformat(), "open_access": True, "checks": [],
         "expiry": (ctx.now - timedelta(days=1)).isoformat(), "is_ready": False},
    ]
    ctx.analysis["service_connection_usage"] = [{"resource": "old"}]
    rules = {f.rule_id for f in risk_engine.service_connection_hygiene(ctx)}
    assert {"RS002", "RS003", "RS004", "RS005", "RS006"} <= rules


def test_trends_compare(tmp_path):
    prev = {"project": "P", "generated_at": "t0", "risk_score": 50, "technical_debt": 10,
            "inventory": {"repositories": 2}, "activity": {"commits": 5}, "findings": ["a", "b"],
            "edges": {"pipeline-dependencies": ["x -> y"]}}
    cur = {**prev, "risk_score": 40, "inventory": {"repositories": 3}, "findings": ["b", "c"],
           "edges": {"pipeline-dependencies": ["x -> z"]}}
    out = compare(prev, cur)
    assert out["inventory"]["repositories"]["delta"] == 1 and out["new_findings"] == ["c"]
    assert out["resolved_findings"] == ["a"]
    assert out["drift"]["pipeline-dependencies"] == {"added": ["x -> z"], "removed": ["x -> y"]}
    hist = tmp_path / "History"
    hist.mkdir()
    (hist / "run-1.json").write_text('{"project": "P", "n": 1}')
    (hist / "run-2.json").write_text("not json")
    (hist / "run-0.json").write_text('{"project": "Other"}')
    assert load_previous(hist, "P") == {"project": "P", "n": 1}
    assert load_previous(tmp_path / "none", "P") is None
