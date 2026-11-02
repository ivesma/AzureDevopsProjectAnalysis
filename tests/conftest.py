from __future__ import annotations

from datetime import datetime, timezone

import pytest

from azdo_analyser import demo
from azdo_analyser.core.config import Settings
from azdo_analyser.core.context import RunContext
from azdo_analyser.core.models import Assessment
from azdo_analyser.core.orchestrator import Orchestrator


def demo_settings(output) -> Settings:
    return Settings(organisation=demo.ORG, project=demo.PROJECT, output_folder=output, generate_powerbi=True)


@pytest.fixture(scope="session")
def demo_run(tmp_path_factory):
    """One full demo run shared by the end-to-end tests."""
    out = tmp_path_factory.mktemp("assessment")
    client = demo.demo_client()
    orchestrator = Orchestrator(demo_settings(out), client)
    result = orchestrator.run()
    return result, out, client, orchestrator.ctx


@pytest.fixture
def ctx(tmp_path) -> RunContext:
    """An empty run context for unit-testing analysers and rules."""
    settings = Settings(organisation="contoso", project="Platform", output_folder=tmp_path)
    now = datetime(2026, 11, 2, tzinfo=timezone.utc)
    context = RunContext(settings, None, Assessment("contoso", "Platform", generated_at=now), now=now)
    return context
