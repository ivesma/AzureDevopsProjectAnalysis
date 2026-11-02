# Azure DevOps Project Analysis Platform

`azdo-analyse` performs a complete technical assessment of an Azure DevOps project — repositories, pipelines
(YAML and classic), releases, environments, agent pools, library, service connections, boards, wiki, test plans,
artifacts and security — and publishes the findings as:

| Output | Location | Notes |
|---|---|---|
| Excel workbook | `Excel/ProjectAssessment.xlsx` | 27 sheets, Excel tables (filters, pivot-ready), conditional formatting, hyperlinks |
| HTML report | `HTML/index.html` | single file: search, severity/category filters, collapsible sections, sortable tables, Mermaid, KPI & risk charts, light/dark |
| Azure DevOps wiki | `Wiki/` (+ optional publish) | "Project Assessment" page tree with `::: mermaid` diagrams, `.order` files |
| Markdown docs | `Markdown/` | same tree with ```` ```mermaid ```` fences (GitHub / VS Code) |
| Mermaid diagrams | `Mermaid/<category>/*.mmd` | stage flows for every pipeline, dependency, template, service-connection, variable-group, environment, agent-pool, work-item hierarchy, wiki site-map and security diagrams |
| draw.io / Graphviz | `DrawIO/`, `Graphviz/` | the same graphs as `.drawio` (opens in diagrams.net, exports to Visio) and `.dot` |
| JSON | `Json/full-export.json` | everything collected and derived |
| History | `History/run-*.json` | per-run record used for trends & architectural-drift detection |
| Power BI (opt-in) | `PowerBI/*.csv` + `model.json` | star-schema CSVs |

## Quick start

```bash
python -m venv .venv && . .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -e .                                  # or: pip install -r requirements.txt

# Try it offline against the built-in synthetic project
azdo-analyse --demo --output ./assessment

# Analyse a real project
export AZDO_PAT=<personal access token>
azdo-analyse --org https://dev.azure.com/company --project Platform --output ./assessment

# Use your Azure CLI login instead of a PAT
az login
azdo-analyse --org company --project Platform --auth azcli
```

Open `assessment/HTML/index.html` in a browser, or `assessment/Excel/ProjectAssessment.xlsx` in Excel.

Requires Python 3.12+.

### Standalone executables (no Python required)

Pre-built single-file executables for Windows (x64), Linux (x64) and macOS (arm64 / x64) are attached to each
GitHub release (`azdo-analyse-<version>-<os>-<arch>.zip|.tar.gz`). They take exactly the same arguments as the
`azdo-analyse` command above.

To build one yourself (PyInstaller cannot cross-compile, so build on each target OS):

```bash
pip install ".[llm]" pyinstaller
python packaging/build.py          # -> dist/azdo-analyse[.exe] + release archive, after a --demo smoke test
```

The [build workflow](.github/workflows/build.yml) does this on all platforms for every push and pull request,
and publishes a GitHub release when a `v*` tag is pushed. For Azure DevOps, [azure-pipelines.yml](azure-pipelines.yml)
does the same; when queued manually, tick the operating systems to build (Windows, Linux, macOS). On macOS the binaries are unsigned: clear the
quarantine flag with `xattr -d com.apple.quarantine azdo-analyse` before first run.

## CLI

```text
azdo-analyse [--config FILE] [--org ORG] [--project NAME] [--output DIR]
             [--pat PAT] [--auth {pat,azcli,managed_identity}] [--history-days N]
             [--collectors a,b] [--skip a,b]
             [--no-excel] [--no-html] [--no-mermaid] [--no-json] [--no-markdown] [--no-drawio] [--powerbi]
             [--wiki-publish] [--wiki-name NAME] [--expand-yaml] [--llm-summary]
             [--demo] [--list-plugins] [-v] [--version]
```

Exit codes: `0` success, `1` the project could not be read, `2` configuration/authentication error.
Areas the identity cannot read are skipped and listed in the **Coverage** sheet / **Data Coverage** page rather
than failing the run.

## Configuration

All options can be set in YAML (see [config.example.yaml](config.example.yaml)); CLI flags override the file,
and `${ENV_VAR}` references are expanded:

```yaml
organisation: https://dev.azure.com/company
project: Platform
pat: ${AZDO_PAT}
output_folder: ./assessment
wiki_publish: false
generate_html: true
generate_excel: true
generate_mermaid: true
```

The PAT is never written to any output. See [docs/permissions.md](docs/permissions.md) for the scopes needed.

## What gets analysed

- **Project**: description, visibility, process template, teams & members, administrators.
- **Repositories**: default branch, branch policies (reviewers, build validation, comment resolution, work-item
  linking, merge strategies), locked branches, last commit, commit volume, contributors, pull-request throughput and
  merge strategies used, detected technologies, submodules, every YAML file on the default branch.
- **Pipelines**: folder, YAML path, repository, triggers (CI/PR/schedule/build-completion/pipeline resources),
  schedules, variables & secrets, variable groups, templates (resolved transitively across repositories, with
  parameter substitution), runtime parameters, service connections, agent pools & demands, environments, stages,
  run history and success rate, complexity score, YAML anti-patterns.
- **Releases** (classic): stages, approvals, gates, deploy phases, tasks, service connections, variables, artifacts.
- **Environments**: Kubernetes & VM resources, approvals and checks, pipeline permissions, deployment history.
- **Agent pools**: hosted vs self-hosted, agents, versions, capabilities, status, usage, open access.
- **Library**: variable groups (Key Vault-linked), secure files, pipeline authorisations, usage matrix.
- **Service connections**: type, auth scheme (secret vs workload identity), sharing, checks, execution history,
  consumers, unused connections.
- **Boards**: work item types & states, backlog hierarchy, counts, open vs closed, lead & cycle time, throughput,
  assignment.
- **Wiki**: page hierarchy, last author, page views, broken links, orphaned pages.
- **Test plans**: plans, suites, test cases, automation coverage, run pass rate.
- **Artifacts**: feeds, upstreams, packages and their age, retention, permissions, consuming pipelines.
- **Security**: groups & memberships, project administrators, service principals & build identities, explicit
  ACLs (Git, Build, Release, Project) decoded into permission names, user access levels & last access, PAT audit
  events.
- **Activity**: deployment frequency, production change-failure rate, commit frequency, PR throughput, active
  contributors, build success rate, estimated parallel-job cost.

The **risk engine** (60+ rules — see [docs/rules.md](docs/rules.md)) produces findings with severity, impact,
recommendation and remediation steps, a 0–100 **risk score**, a **technical debt** score and a prioritised
**recommendations** list. Re-running into the same output folder adds **trend** and **architectural drift**
analysis (new/resolved findings, added/removed dependencies).

## Optional extras

```bash
pip install -e ".[dashboard]"   # Streamlit dashboard: streamlit run dashboard/streamlit_app.py -- ./assessment
pip install -e ".[llm]"         # --llm-summary: executive summary written by Claude (needs ANTHROPIC_API_KEY)
pip install -e ".[dev]"         # tests
```

## Documentation

- [Architecture](docs/architecture.md) — plugin model, data flow, design decisions
- [Permissions](docs/permissions.md) — PAT scopes and what each area needs
- [Risk rules](docs/rules.md) — the rule catalogue
- [Extending](docs/extending.md) — writing collectors, analysers, exporters and rules
- [Output reference](docs/outputs.md) — every file and sheet

## Development

```bash
pip install -e ".[dev]"
pytest --cov            # coverage gate: 80% (currently ~97%)
```

The test-suite runs the real collectors against a synthetic REST backend (`azdo_analyser/demo.py`), so no
Azure DevOps organisation is needed.
