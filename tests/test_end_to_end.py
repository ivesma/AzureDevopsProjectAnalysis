"""Full runs against the synthetic demo dataset (collectors -> analysers -> exporters)."""

import json
import sys
import types

from openpyxl import load_workbook

from azdo_analyser import cli, demo
from azdo_analyser.analysers import summary
from azdo_analyser.core.orchestrator import Orchestrator
from azdo_analyser.core.plugin import plugins
from azdo_analyser.exporters.wiki import WikiPublisher, wiki_file_name
from tests.conftest import demo_settings


def test_all_plugins_ran(demo_run):
    result, out, client, ctx = demo_run
    assert result.failures == {}
    assert not [c for c in ctx.assessment.coverage if c.status in ("error", "denied")]
    assert {"json", "mermaid", "excel", "markdown", "html", "wiki", "powerbi"} <= set(result.outputs)
    assert client.request_count > 100


def test_snapshot_contents(demo_run):
    snap = demo_run[3].snapshot
    repos = {r["name"]: r for r in snap["repositories"]}
    assert repos["platform-api"]["default_branch_policies"]["min_reviewers"] == 2
    assert repos["platform-api"]["contributor_count"] == 4 and repos["platform-api"]["commits_in_window"] == 140
    assert repos["sandbox"]["is_empty"] and repos["legacy-tools"]["is_disabled"]
    assert repos["platform-web"]["submodules"][0]["path"] == "assets"
    assert ".NET" in repos["platform-api"]["technologies"]
    assert len(snap["pipelines"]) == 6 and snap["pipelines"][4]["type"] == "classic"
    assert snap["release_pipelines"][0]["stages"][2]["pre_approvals"]["automated"] is True
    prod = next(e for e in snap["environments"] if e["name"] == "production")
    assert [c["type"] for c in prod["checks"]] == ["Approval", "Branch control"]
    assert prod["resources"][0]["namespace"] == "platform"
    assert len(snap["boards"]["work_items"]) == 74 and snap["boards"]["backlog_hierarchy"][0]["name"] == "Epics"
    assert snap["test_plans"]["plans"][0]["automation_coverage"] == round(6 / 13, 3)
    assert {f["name"] for f in snap["feeds"]} == {"platform-packages", "org-shared"}
    assert len(snap["security"]["project_admin_users"]) == 7
    assert snap["security"]["pat_events"]


def test_analysis(demo_run):
    an = demo_run[3].analysis
    cd = an["yaml"]["pipelines"]["2"]
    assert cd["service_connections"] == ["sc-azure-nonprod", "sc-azure-prod"]
    assert cd["environments"] == ["dev", "test", "production"]
    assert [s["name"] for s in an["yaml"]["pipelines"]["1"]["stages"]] == ["Build", "Test"]
    assert "platform-web:/old-pipeline.yml" in an["yaml"]["unregistered_pipelines"]
    deps = {(d["source"], d["target"], d["relation"]) for d in an["pipeline_dependencies"]}
    assert ("platform-api-ci", "platform-api-cd", "triggers") in deps
    assert ("Legacy-Build", "Legacy Release", "artifact (CD trigger)") in deps
    assert ("platform-api-ci", "docs-publish", "build completion") in deps
    sc_users = {(u["resource"], u["consumer"]) for u in an["service_connection_usage"]}
    assert ("sc-azure-legacy", "Legacy Release / Dev") in sc_users and ("sc-k8s-prod", "production") in sc_users
    assert {"platform-packages"} <= set(an["feed_consumers"])
    assert an["metrics"]["activity"]["builds"] == 128 and an["metrics"]["boards"]["total"] == 74
    assert an["risk"]["score"] > 50 and an["technical_debt"]["grade"] in "ABCDE"
    assert an["summary"]["source"] == "deterministic"


def test_expected_findings(demo_run):
    ids = {f.id for f in demo_run[0].assessment.findings}
    for expected in ("SC001:repository:pipeline-templates", "DP010:release stage:Legacy Release / Production",
                     "DP020:pipeline:infra-deploy", "AP001:pipeline:Platform Web CI",
                     "RS001:service connection:sc-unused-generic", "SE001:group:Project Administrators",
                     "AP007:pipeline:platform-api-cd", "DO001:wiki:Platform.wiki", "TE001:test plans:Test plans"):
        assert expected in ids, expected
    assert "RS001:service connection:sc-azure-prod" not in ids


def test_graphs(demo_run):
    graphs = demo_run[0].assessment.graphs
    for name in ("stage-flow-2", "release-flow-1", "pipeline-dependencies", "template-usage", "service-connections",
                 "variable-groups", "environments", "repository-dependencies", "agent-pools", "work-item-hierarchy",
                 "wiki-sitemap-Platform.wiki", "security-groups"):
        assert name in graphs and not graphs[name].is_empty, name
    assert graphs["stage-flow-2"].nodes["s_DeployProd"].type == "prod"


def test_output_files(demo_run):
    out = demo_run[1]
    data = json.loads((out / "Json" / "full-export.json").read_text(encoding="utf-8"))
    assert data["metadata"]["project"] == "Platform" and "pat" not in data["metadata"]["settings"]
    assert list((out / "History").glob("run-*.json"))
    wb = load_workbook(out / "Excel" / "ProjectAssessment.xlsx")
    for sheet in ("Index", "Summary", "Repositories", "Pipelines", "PipelineDependencies", "Templates", "Variables",
                  "VariableGroups", "ServiceConnections", "Environments", "ReleasePipelines", "AgentPools", "WorkItems",
                  "Wiki", "SecurityFindings", "RiskRegister", "Metrics", "Recommendations"):
        assert sheet in wb.sheetnames, sheet
    assert wb["RiskRegister"].tables and wb["RiskRegister"].conditional_formatting
    html = (out / "HTML" / "index.html").read_text(encoding="utf-8")
    assert "pre class=\"mermaid\"" in html and "Risk dashboard" in html and 'id="search"' in html
    assert (out / "Mermaid" / "pipelines" / "stage-flow-2.mmd").exists()
    assert (out / "DrawIO" / "dependencies" / "pipeline-dependencies.drawio").exists()
    assert (out / "Graphviz" / "dependencies" / "pipeline-dependencies.dot").exists()
    wiki_root = out / "Wiki" / "Project-Assessment"
    assert (out / "Wiki" / "Project-Assessment.md").exists()
    assert "Executive-Summary" in (wiki_root / ".order").read_text()
    assert "::: mermaid" in (wiki_root / "Pipelines" / "platform%2Dapi%2Dcd.md").read_text(encoding="utf-8")
    assert "```mermaid" in (out / "Markdown" / "README.md").read_text(encoding="utf-8")
    assert (out / "PowerBI" / "fact_findings.csv").exists() and (out / "PowerBI" / "model.json").exists()


def test_second_run_trends_and_wiki_publish(demo_run, tmp_path):
    settings = demo_settings(demo_run[1])
    settings.wiki_publish = True
    client = demo.demo_client()
    result = Orchestrator(settings, client).run()
    trends = result.assessment.analysis["trends"]
    assert trends and trends["new_findings"] == [] and trends["drift"] == {}
    published = client.session.published
    assert "/Project Assessment" in published and "/Project Assessment/Pipelines/infra-deploy" in published
    paths = list(published)
    assert paths.index("/Project Assessment") < paths.index("/Project Assessment/Pipelines")


def test_wiki_naming_and_named_wiki_missing(demo_run):
    assert wiki_file_name("Agent Pools") == "Agent-Pools" and wiki_file_name("a-b") == "a%2Db"
    ctx = demo_run[3]
    ctx.settings.wiki_name = "Nope"
    try:
        WikiPublisher(ctx).wiki_id()
        raise AssertionError("expected failure")
    except Exception as exc:
        assert "not found" in str(exc)
    finally:
        ctx.settings.wiki_name = None


def test_collector_selection_and_offline_wiki(tmp_path):
    settings = demo_settings(tmp_path)
    settings.collectors = ["project", "repositories"]
    settings.wiki_publish = True
    settings.generate_excel = settings.generate_html = False
    result = Orchestrator(settings, demo.demo_client()).run()
    skipped = {c.collector for c in result.assessment.coverage if c.status == "skipped"}
    assert "pipelines" in skipped and "excel" not in result.outputs
    offline = Orchestrator(demo_settings(tmp_path / "o"), None)
    offline.ctx.settings.wiki_publish = True
    res = offline.run(collect=False)
    assert any(c.area == "wiki-publish" and c.status == "skipped" for c in res.assessment.coverage)


def test_fatal_project_failure(tmp_path):
    settings = demo_settings(tmp_path)
    settings.project = "DoesNotExist"
    assert cli.main(["--demo", "--output", str(tmp_path / "x")]) == 0  # demo forces the demo project
    from azdo_analyser.core.orchestrator import FatalCollectionError

    try:
        Orchestrator(settings, demo.demo_client()).run()
        raise AssertionError("expected fatal error")
    except FatalCollectionError as exc:
        assert "404" in str(exc)


def test_llm_summary(ctx, monkeypatch):
    class Block:
        type = "text"
        text = "LLM summary"

    class Response:
        stop_reason = "end_turn"
        content = [Block()]

    calls = {}

    class Messages:
        def create(self, **kw):
            calls.update(kw)
            return Response()

    fake = types.SimpleNamespace(Anthropic=lambda: types.SimpleNamespace(beta=types.SimpleNamespace(messages=Messages())))
    monkeypatch.setitem(sys.modules, "anthropic", fake)
    ctx.settings.llm_summary = True
    summary.SummaryAnalyser().analyse(ctx)
    assert ctx.analysis["summary"]["llm_text"] == "LLM summary" and calls["model"] == "claude-opus-5-5"
    Response.stop_reason = "refusal"
    summary.SummaryAnalyser().analyse(ctx)
    assert "llm_text" not in ctx.analysis["summary"]
    monkeypatch.setitem(sys.modules, "anthropic", None)  # import fails
    assert summary.llm_summary(ctx) is None


def test_cli(tmp_path, capsys):
    assert cli.main(["--list-plugins"]) == 0
    assert cli.main(["--org", "x"]) == 2  # missing project
    assert cli.main(["--demo", "--output", str(tmp_path), "--no-excel", "--no-html", "--no-drawio", "-v"]) == 0
    assert (tmp_path / "Json" / "full-export.json").exists() and not (tmp_path / "Excel").exists()
    names = {s.name for s in plugins("collector")}
    assert {"repositories", "pipelines", "boards", "wiki", "security"} <= names


def test_cli_overrides():
    args = cli.build_parser().parse_args(["--no-markdown", "--powerbi", "--wiki-publish", "--expand-yaml",
                                          "--llm-summary", "--no-json", "--no-mermaid"])
    o = cli.overrides_from_args(args)
    assert o["generate_markdown"] is False and o["generate_powerbi"] and o["wiki_publish"]
    assert o["expand_yaml_via_preview"] and o["llm_summary"] and o["generate_json"] is False
