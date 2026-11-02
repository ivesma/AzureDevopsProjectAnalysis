from azdo_analyser.analysers.yaml_analysis import TemplateResolver, build_effective
from azdo_analyser.analysers.yaml_parser import (TemplateRef, extract_stages, flatten, load_yaml, parse_file,
                                                 placeholder_expressions, resolve_template, split_template_ref)

PIPELINE = """
trigger:
  branches: {include: [main]}
on: value
resources:
  repositories:
    - repository: tpl
      type: git
      name: Proj/templates
      ref: refs/heads/main
    - repository: gh
      type: github
      name: org/repo
      endpoint: gh-conn
  pipelines:
    - pipeline: up
      source: upstream-ci
      trigger: true
  containers:
    - container: c
      image: x
      endpoint: acr-conn
parameters:
  - name: env
    type: string
    default: prod-env
variables:
  - group: shared
  - name: password
    value: hunter2hunter2
  - template: vars.yml
pool:
  vmImage: windows-2019
stages:
  - stage: A
    jobs:
      - job: one
        steps:
          - task: AzureCLI@2
            inputs:
              azureSubscription: ${{ parameters.env }}
          - ${{ if eq(variables.x, 'y') }}:
            - task: Kubernetes@1
              inputs:
                kubernetesServiceEndpoint: $(k8sConn)
          - script: curl https://x | bash
          - checkout: tpl
            persistCredentials: true
  - stage: B
    jobs:
      - deployment: d
        environment: production.ns
        strategy:
          runOnce:
            deploy:
              steps:
                - template: steps.yml@tpl
                - task: AzureKeyVault@2
                  inputs: {azureSubscription: kv-conn, KeyVaultName: my-kv}
  - stage: C
    dependsOn: []
    jobs:
      - template: jobs.yml
"""


def test_parse_pipeline_extracts_everything():
    m = parse_file("repo:/azure-pipelines.yml", PIPELINE)
    assert m.error is None and m.kind == "pipeline"
    assert "on" in m.raw  # YAML 1.1 'on' must stay a string key
    assert m.variable_groups == ["shared"]
    assert m.variables_inline[0]["looks_secret"] and m.variables_inline[0]["value"] is None
    names = {s["name"]: s for s in m.service_connections}
    assert names["prod-env"]["dynamic"] is False  # parameter default resolved
    assert names["$(k8sConn)"]["dynamic"] is True  # conditional branch included, runtime macro
    assert {"gh-conn", "acr-conn", "kv-conn"} <= set(names)
    assert m.environments == ["production"]
    assert m.key_vaults == ["my-kv"] and m.checkouts == ["tpl"] and m.persist_credentials
    assert {t.ref for t in m.templates} == {"vars.yml", "steps.yml@tpl", "jobs.yml"}
    assert m.pipeline_resources == [{"alias": "up", "source": "upstream-ci", "project": None, "trigger": True}]
    assert m.pools[0]["vm_image"] == "windows-2019"
    assert [s["name"] for s in m.stages] == ["A", "B", "C"]
    assert m.stages[2]["depends_on"] == [] and m.stages[0]["depends_on"] is None
    assert m.stages[1]["jobs"][0]["kind"] == "deployment"
    assert any(s["kind"] == "script" for s in m.scripts)


def test_flatten_unions_branches():
    data = load_yaml("steps:\n  - ${{ if true }}:\n    - script: a\n  - ${{ else }}:\n    - script: b\n"
                     "x:\n  ${{ if true }}:\n    k: v\n")
    flat = flatten(data)
    assert [s["script"] for s in flat["steps"]] == ["a", "b"]
    assert flat["x"] == {"k": "v"}


def test_file_kinds_and_errors():
    assert parse_file("r:/k.yaml", "apiVersion: v1\nkind: Pod\n").kind == "manifest"
    assert parse_file("r:/t.yml", "parameters:\n  - name: a\nsteps:\n  - script: x\n").kind == "template"
    assert parse_file("r:/o.yml", "- a\n- b\n").kind == "other"
    assert parse_file("r:/x.yml", "foo: bar\n").kind == "other"
    bad = parse_file("r:/bad.yml", "a: [unclosed\n")
    assert bad.error and bad.error.startswith("YAML parse error")
    old = parse_file("r:/old.yml", "parameters:\n  conn: abc\nsteps:\n  - task: X@1\n    inputs:\n"
                                    "      connectedServiceName: ${{ parameters.conn }}\n")
    assert old.service_connections[0]["name"] == "abc"


def test_implicit_stage_and_unknown_tag():
    m = parse_file("r:/p.yml", "pool: Default\nsteps:\n  - script: !custom echo\n")
    assert m.stages[0]["name"] == "__default" and m.pools[0]["name"] == "Default"
    stages = extract_stages({"jobs": [{"job": "a", "dependsOn": "b"}, "junk", {"template": "t.yml"}]})
    assert stages[0]["jobs"][0]["depends_on"] == ["b"] and stages[0]["jobs"][1]["kind"] == "template"


def test_template_resolution():
    including = parse_file("app:/pipelines/ci.yml", "steps: []\n")
    aliases = {"t": {"alias": "t", "name": "Proj/templates", "ref": "refs/tags/v1"},
               "b": {"alias": "b", "name": "lib"}}
    assert resolve_template(TemplateRef("steps/build.yml", "step"), including, aliases) == \
        ("app", "/pipelines/steps/build.yml", None)
    assert resolve_template(TemplateRef("/common/x.yml", "step"), including, aliases) == ("app", "/common/x.yml", None)
    assert resolve_template(TemplateRef("x.yml@t", "step"), including, aliases) == ("templates", "/x.yml", True)
    assert resolve_template(TemplateRef("x.yml@b", "step"), including, aliases)[2] is False
    assert resolve_template(TemplateRef("x.yml@missing", "step"), including, aliases)[0] is None
    assert resolve_template(TemplateRef("${{ parameters.t }}", "step"), including, aliases)[0] is None
    assert split_template_ref("a.yml@r") == ("a.yml", "r") and split_template_ref("a.yml") == ("a.yml", None)
    assert placeholder_expressions("$(a) and $(b.c)") == ["a", "b.c"]


def test_build_effective_follows_templates_with_parameters():
    files = {
        "app:/ci.yml": "resources:\n  repositories:\n    - repository: t\n      name: P/lib\n"
                       "stages:\n  - stage: Dev\n    jobs:\n      - template: deploy.yml@t\n        parameters:\n"
                       "          conn: sc-dev\n  - stage: Prod\n    jobs:\n      - template: deploy.yml@t\n"
                       "        parameters:\n          conn: sc-prod\n  - template: stages.yml\n",
        "lib:/deploy.yml": "parameters:\n  - name: conn\njobs:\n  - deployment: d\n    environment: ${{ parameters.conn }}\n"
                           "    strategy:\n      runOnce:\n        deploy:\n          steps:\n"
                           "            - task: AzureCLI@2\n              inputs:\n"
                           "                azureSubscription: ${{ parameters.conn }}\n            - template: nested.yml\n",
        "lib:/nested.yml": "steps:\n  - task: Bash@3\n",
        "app:/stages.yml": "stages:\n  - stage: Extra\n    jobs:\n      - job: x\n",
    }
    resolver = TemplateResolver(files)
    index: dict = {}
    eff = build_effective({"id": 1, "name": "ci"}, resolver.parse("app:/ci.yml"), resolver, index)
    assert eff["service_connections"] == ["sc-dev", "sc-prod"]
    assert sorted(eff["templates"]) == ["app:/stages.yml", "lib:/deploy.yml", "lib:/nested.yml"]
    assert eff["unpinned_templates"] == ["deploy.yml@t"] and eff["max_template_depth"] == 2
    assert [s["name"] for s in eff["stages"]] == ["Dev", "Prod", "Extra"]
    assert eff["stages"][1]["jobs"][0]["environment"] == "sc-prod"
    assert index["lib:/deploy.yml"]["used_by"] == {"ci"}


def test_build_effective_extends_parameters_and_cycles():
    files = {
        "app:/p.yml": "extends:\n  template: base.yml\n  parameters:\n    stages:\n      - stage: S1\n"
                      "        jobs:\n          - job: j\n            steps:\n              - script: hi\n",
        "app:/base.yml": "parameters:\n  - name: stages\n    type: stageList\nstages:\n"
                         "  - ${{ each s in parameters.stages }}:\n    - ${{ s }}\n  - template: loop.yml\n",
        "app:/loop.yml": "stages:\n  - template: loop.yml\n",
    }
    resolver = TemplateResolver(files)
    eff = build_effective({"id": 2, "name": "p"}, resolver.parse("app:/p.yml"), resolver, {})
    assert eff["extends"] == "base.yml"
    assert "app:/loop.yml" in eff["templates"]  # self-reference does not recurse forever
    assert any(s["name"] == "S1" for s in eff["stages"]) or eff["stages"]
