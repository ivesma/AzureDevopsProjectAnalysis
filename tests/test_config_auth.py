import json
import subprocess
from pathlib import Path

import pytest

from azdo_analyser.core.auth import (AuthError, AzureCliAuth, ManagedIdentityAuth, PatAuth, build_auth)
from azdo_analyser.core.config import (ConfigError, Settings, load_settings, normalise_org_url, org_name_from_url)


@pytest.mark.parametrize("given,expected", [
    ("company", "https://dev.azure.com/company"),
    ("https://dev.azure.com/company/", "https://dev.azure.com/company"),
    ("https://dev.azure.com/company/Project/_git/x", "https://dev.azure.com/company"),
    ("company.visualstudio.com", "https://company.visualstudio.com"),
    ("https://tfs.local/tfs/DefaultCollection", "https://tfs.local/tfs/DefaultCollection"),
    ("", ""),
])
def test_normalise_org_url(given, expected):
    assert normalise_org_url(given) == expected


@pytest.mark.parametrize("url,name", [
    ("https://dev.azure.com/company", "company"),
    ("https://company.visualstudio.com", "company"),
    ("https://tfs.local/tfs/DefaultCollection", "DefaultCollection"),
])
def test_org_name(url, name):
    assert org_name_from_url(url) == name


def test_load_settings_yaml_env_and_overrides(tmp_path, monkeypatch):
    monkeypatch.setenv("MY_PAT", "secret")
    cfg = tmp_path / "c.yaml"
    cfg.write_text("organization: company\nproject: Platform\npat: ${MY_PAT}\noutput_folder: out\n"
                   "generate_html: 'false'\nhistory_days: '30'\ncollectors: repositories, pipelines\n"
                   "wiki_name: ${UNSET_VAR:-Fallback}\nmin_build_success_rate: 0.5\n")
    s = load_settings(cfg, {"project": "Other", "auth": None})
    assert s.organisation == "company" and s.project == "Other" and s.pat == "secret"
    assert s.output_folder == Path("out") and s.generate_html is False and s.history_days == 30
    assert s.collectors == ["repositories", "pipelines"] and s.wiki_name == "Fallback"
    assert s.min_build_success_rate == 0.5
    assert s.collector_enabled("pipelines") and not s.collector_enabled("wiki")
    s.validate()


def test_load_settings_errors(tmp_path, monkeypatch):
    with pytest.raises(ConfigError):
        load_settings(tmp_path / "missing.yaml")
    bad = tmp_path / "bad.yaml"
    bad.write_text("- a\n- b\n")
    with pytest.raises(ConfigError):
        load_settings(bad)
    unknown = tmp_path / "unknown.yaml"
    unknown.write_text("nonsense: 1\n")
    with pytest.raises(ConfigError, match="Unknown"):
        load_settings(unknown)


def test_pat_from_environment(monkeypatch):
    for var in ("AZDO_PAT", "AZURE_DEVOPS_EXT_PAT", "SYSTEM_ACCESSTOKEN"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("AZURE_DEVOPS_EXT_PAT", "from-env")
    assert load_settings(None, {"organisation": "x", "project": "y"}).pat == "from-env"


@pytest.mark.parametrize("kwargs,match", [
    ({"project": "p", "pat": "x"}, "organisation"),
    ({"organisation": "o", "pat": "x"}, "project"),
    ({"organisation": "o", "project": "p", "auth": "kerberos"}, "Unsupported"),
    ({"organisation": "o", "project": "p"}, "PAT"),
    ({"organisation": "o", "project": "p", "pat": "x", "production_pattern": "("}, "production_pattern"),
])
def test_validate(kwargs, match):
    with pytest.raises(ConfigError, match=match):
        Settings(**kwargs).validate()


def test_is_production():
    s = Settings()
    for name in ("Production", "prod", "DeployProd", "ApplyProduction", "prd-weu", "Live"):
        assert s.is_production(name), name
    for name in ("product-catalog", "dev", "reproduce", None, ""):
        assert not s.is_production(name), name
    assert s.skip_collectors == [] and s.collector_enabled("wiki")


def test_pat_auth():
    header = PatAuth("abc").headers()["Authorization"]
    assert header == "Basic OmFiYw=="
    with pytest.raises(AuthError):
        PatAuth("")


def test_azure_cli_auth_caches_and_refreshes():
    calls = []

    def runner(cmd, **_):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, json.dumps({"accessToken": f"t{len(calls)}", "expires_on": 1000}), "")

    clock = [0.0]
    auth = AzureCliAuth(runner=runner, clock=lambda: clock[0])
    assert auth.headers() == {"Authorization": "Bearer t1"}
    assert auth.headers() == {"Authorization": "Bearer t1"}
    clock[0] = 900  # within 5 minutes of expiry -> refresh
    assert auth.headers() == {"Authorization": "Bearer t2"}
    assert "--resource" in calls[0]


def test_azure_cli_auth_failures():
    fail = AzureCliAuth(runner=lambda cmd, **_: subprocess.CompletedProcess(cmd, 1, "", "not logged in"))
    with pytest.raises(AuthError, match="not logged in"):
        fail.headers()

    def missing(cmd, **_):
        raise FileNotFoundError

    with pytest.raises(AuthError, match="not found"):
        AzureCliAuth(runner=missing).headers()


class _Resp:
    def __init__(self, status, data):
        self.status_code, self._data = status, data

    def json(self):
        return self._data


class _Session:
    def __init__(self, resp=None, exc=None):
        self.resp, self.exc, self.params = resp, exc, None

    def get(self, url, params=None, headers=None, timeout=None):
        self.params = params
        if self.exc:
            raise self.exc
        return self.resp


def test_managed_identity():
    import requests

    session = _Session(_Resp(200, {"access_token": "mi", "expires_on": "99999999999"}))
    assert ManagedIdentityAuth(client_id="cid", session=session).headers()["Authorization"] == "Bearer mi"
    assert session.params["client_id"] == "cid"
    with pytest.raises(AuthError, match="HTTP 400"):
        ManagedIdentityAuth(session=_Session(_Resp(400, {}))).headers()
    with pytest.raises(AuthError, match="unreachable"):
        ManagedIdentityAuth(session=_Session(exc=requests.ConnectionError("x"))).headers()


def test_build_auth():
    assert isinstance(build_auth("pat", "x"), PatAuth)
    assert isinstance(build_auth("azcli"), AzureCliAuth)
    assert isinstance(build_auth("managed_identity"), ManagedIdentityAuth)
    with pytest.raises(AuthError):
        build_auth("nope")
