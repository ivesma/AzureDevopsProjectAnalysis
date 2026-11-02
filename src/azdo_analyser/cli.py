"""Command line interface: ``azdo-analyse``."""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from rich.console import Console
from rich.logging import RichHandler
from rich.table import Table

from azdo_analyser import __version__
from azdo_analyser.core.auth import AuthError, build_auth
from azdo_analyser.core.client import AzureDevOpsClient
from azdo_analyser.core.config import ConfigError, Settings, load_settings
from azdo_analyser.core.orchestrator import FatalCollectionError, Orchestrator, RunResult
from azdo_analyser.core.plugin import plugins

console = Console(stderr=True)

SEV_STYLE = {"CRITICAL": "bold magenta", "HIGH": "bold red", "MEDIUM": "dark_orange", "LOW": "yellow", "INFO": "dim"}


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="azdo-analyse",
        description="Discover, analyse, document and visualise an Azure DevOps project.",
        epilog="Example: azdo-analyse --org https://dev.azure.com/company --project Platform --output ./assessment",
    )
    p.add_argument("--config", "-c", help="YAML configuration file")
    p.add_argument("--org", dest="organisation", help="Organisation URL or name")
    p.add_argument("--project", "-p", help="Project name")
    p.add_argument("--output", "-o", dest="output_folder", help="Output folder (default ./assessment)")
    p.add_argument("--pat", help="Personal access token (prefer the AZDO_PAT environment variable)")
    p.add_argument("--auth", choices=["pat", "azcli", "managed_identity"], help="Authentication method")
    p.add_argument("--history-days", type=int, help="Activity analysis window in days (default 90)")
    p.add_argument("--collectors", help="Comma-separated collectors to run (default: all)")
    p.add_argument("--skip", dest="skip_collectors", help="Comma-separated collectors to skip")
    p.add_argument("--no-excel", action="store_true", help="Do not generate the Excel workbook")
    p.add_argument("--no-html", action="store_true", help="Do not generate the HTML report")
    p.add_argument("--no-mermaid", action="store_true", help="Do not write Mermaid files")
    p.add_argument("--no-json", action="store_true", help="Do not write the JSON export")
    p.add_argument("--no-markdown", action="store_true", help="Do not write Markdown / wiki files")
    p.add_argument("--no-drawio", action="store_true", help="Do not write draw.io / Graphviz files")
    p.add_argument("--powerbi", action="store_true", help="Also write Power BI CSV star schema")
    p.add_argument("--wiki-publish", action="store_true", help="Publish the assessment pages to the project wiki")
    p.add_argument("--wiki-name", help="Target wiki name (default: the project wiki)")
    p.add_argument("--expand-yaml", action="store_true",
                   help="Use the pipeline preview API to expand templates server-side (needs queue permission)")
    p.add_argument("--llm-summary", action="store_true", help="Generate the executive summary with Claude")
    p.add_argument("--demo", action="store_true", help="Run against the built-in synthetic dataset (no connection)")
    p.add_argument("--list-plugins", action="store_true", help="List registered collectors/analysers/exporters")
    p.add_argument("--verbose", "-v", action="count", default=0, help="Increase log verbosity")
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return p


def overrides_from_args(args: argparse.Namespace) -> dict:
    o = {
        "organisation": args.organisation, "project": args.project, "output_folder": args.output_folder,
        "pat": args.pat, "auth": args.auth, "history_days": args.history_days, "collectors": args.collectors,
        "skip_collectors": args.skip_collectors, "wiki_name": args.wiki_name,
    }
    flags = {"no_excel": "generate_excel", "no_html": "generate_html", "no_mermaid": "generate_mermaid",
             "no_json": "generate_json", "no_markdown": "generate_markdown", "no_drawio": "generate_drawio"}
    for flag, key in flags.items():
        if getattr(args, flag):
            o[key] = False
    for flag, key in (("powerbi", "generate_powerbi"), ("wiki_publish", "wiki_publish"),
                      ("expand_yaml", "expand_yaml_via_preview"), ("llm_summary", "llm_summary")):
        if getattr(args, flag):
            o[key] = True
    return o


def list_plugins() -> None:
    table = Table(title="Registered plugins")
    for col in ("Kind", "Name", "Order", "Description"):
        table.add_column(col)
    for kind in ("collector", "analyser", "exporter"):
        for spec in plugins(kind):
            table.add_row(kind, spec.name, str(spec.order), spec.description)
    Console().print(table)


def print_summary(result: RunResult, settings: Settings) -> None:
    a = result.assessment
    risk = a.analysis.get("risk", {})
    debt = a.analysis.get("technical_debt", {})
    out = Console()
    out.print(f"\n[bold]Assessment of {a.organisation}/{a.project}[/bold]")
    out.print(f"Risk score [bold]{risk.get('score')}[/bold] (grade {risk.get('grade')}) · "
              f"technical debt {debt.get('score')} (grade {debt.get('grade')})")
    counts = a.severity_counts()
    out.print("Findings: " + "  ".join(f"[{SEV_STYLE[s]}]{s} {n}[/]" for s, n in counts.items() if n))
    recs = a.analysis.get("recommendations", [])[:8]
    if recs:
        table = Table(title="Top recommendations", show_lines=False)
        table.add_column("#", justify="right")
        table.add_column("Severity")
        table.add_column("Recommendation")
        table.add_column("Count", justify="right")
        for i, r in enumerate(recs, 1):
            table.add_row(str(i), f"[{SEV_STYLE[r['severity']]}]{r['severity']}[/]", r["title"], str(r["occurrences"]))
        out.print(table)
    gaps = [c for c in a.coverage if c.status in ("denied", "error")]
    if gaps:
        out.print(f"[yellow]{len(gaps)} area(s) could not be collected - see the Coverage sheet / Data Coverage page.[/]")
    if result.failures:
        out.print(f"[red]{len(result.failures)} plugin(s) failed:[/] " + ", ".join(result.failures))
    out.print("\n[bold]Outputs[/bold]")
    root = Path(settings.output_folder)
    for name, files in result.outputs.items():
        main = files[0]
        try:
            shown = main.relative_to(root)
        except ValueError:
            shown = main
        out.print(f"  {name:<10} {root / shown}" + (f"  (+{len(files) - 1} files)" if len(files) > 1 else ""))


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    level = logging.WARNING - 10 * min(args.verbose, 2)
    logging.basicConfig(level=level, format="%(message)s", handlers=[RichHandler(console=console, show_path=False)])
    if args.list_plugins:
        list_plugins()
        return 0
    try:
        overrides = overrides_from_args(args)
        if args.demo:
            from azdo_analyser import demo

            settings = load_settings(args.config, overrides)
            settings.organisation, settings.project = demo.ORG, demo.PROJECT
            client = demo.demo_client()
        else:
            settings = load_settings(args.config, overrides)
            settings.validate()
            client = AzureDevOpsClient(settings.organisation_url, build_auth(settings.auth, settings.pat),
                                       api_version=settings.api_version, timeout=settings.timeout,
                                       max_retries=settings.max_retries, verify_ssl=settings.verify_ssl)
    except (ConfigError, AuthError) as exc:
        console.print(f"[red]Configuration error:[/] {exc}")
        return 2

    with console.status("Starting…") as status:
        def progress(phase: str, name: str, state: str) -> None:
            if state == "start":
                status.update(f"[bold]{phase}[/] {name}…")
            elif state == "failed":
                console.print(f"[red]✗[/] {phase} {name} failed")
            elif args.verbose:
                console.print(f"[green]✓[/] {phase} {name}")

        orchestrator = Orchestrator(settings, client, progress)
        try:
            result = orchestrator.run()
        except FatalCollectionError as exc:
            console.print(f"[red]Cannot read project '{settings.project}':[/] {exc}")
            return 1
    print_summary(result, settings)
    if client is not None:
        console.print(f"[dim]{client.request_count} API requests[/]")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
