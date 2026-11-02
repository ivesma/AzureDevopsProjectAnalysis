"""Authentication providers: PAT, Azure CLI token and Managed Identity (preview)."""

from __future__ import annotations

import base64
import json
import shutil
import subprocess
import time
from typing import Callable, Protocol

import requests

# Well-known Azure DevOps resource (application) id used for Entra ID tokens.
AZDO_RESOURCE_ID = "499b84ac-1321-427f-aa17-267ca6975798"
IMDS_URL = "http://169.254.169.254/metadata/identity/oauth2/token"


class AuthError(RuntimeError):
    pass


class AuthProvider(Protocol):
    def headers(self) -> dict[str, str]: ...


class PatAuth:
    def __init__(self, pat: str):
        if not pat:
            raise AuthError("Empty personal access token")
        token = base64.b64encode(f":{pat}".encode()).decode()
        self._header = {"Authorization": f"Basic {token}"}

    def headers(self) -> dict[str, str]:
        return dict(self._header)


class _CachedBearer:
    """Caches a bearer token and refreshes it five minutes before expiry."""

    def __init__(self, clock: Callable[[], float] = time.time):
        self._token: str | None = None
        self._expires_at = 0.0
        self._clock = clock

    def _fetch(self) -> tuple[str, float]:  # pragma: no cover - abstract
        raise NotImplementedError

    def headers(self) -> dict[str, str]:
        if not self._token or self._clock() > self._expires_at - 300:
            self._token, self._expires_at = self._fetch()
        return {"Authorization": f"Bearer {self._token}"}


class AzureCliAuth(_CachedBearer):
    """Uses `az account get-access-token` for the Azure DevOps resource."""

    def __init__(self, runner: Callable[..., subprocess.CompletedProcess] | None = None, **kw):
        super().__init__(**kw)
        self._runner = runner or subprocess.run

    def _fetch(self) -> tuple[str, float]:
        az = shutil.which("az") or "az"
        try:
            proc = self._runner(
                [az, "account", "get-access-token", "--resource", AZDO_RESOURCE_ID, "--output", "json"],
                capture_output=True,
                text=True,
                check=False,
            )
        except FileNotFoundError as exc:
            raise AuthError("Azure CLI ('az') not found on PATH") from exc
        if proc.returncode != 0:
            raise AuthError(f"az account get-access-token failed: {proc.stderr.strip()}")
        data = json.loads(proc.stdout)
        expires = float(data.get("expires_on") or (self._clock() + 3000))
        return data["accessToken"], expires


class ManagedIdentityAuth(_CachedBearer):
    """Managed Identity via the Azure Instance Metadata Service (preview support)."""

    def __init__(self, client_id: str | None = None, session: requests.Session | None = None, **kw):
        super().__init__(**kw)
        self._client_id = client_id
        self._session = session or requests.Session()

    def _fetch(self) -> tuple[str, float]:
        params = {"api-version": "2018-02-01", "resource": AZDO_RESOURCE_ID}
        if self._client_id:
            params["client_id"] = self._client_id
        try:
            resp = self._session.get(IMDS_URL, params=params, headers={"Metadata": "true"}, timeout=10)
        except requests.RequestException as exc:
            raise AuthError(f"Managed identity endpoint unreachable: {exc}") from exc
        if resp.status_code != 200:
            raise AuthError(f"Managed identity token request failed: HTTP {resp.status_code}")
        data = resp.json()
        return data["access_token"], float(data.get("expires_on", self._clock() + 3000))


def build_auth(method: str, pat: str | None = None) -> AuthProvider:
    if method == "pat":
        return PatAuth(pat or "")
    if method == "azcli":
        return AzureCliAuth()
    if method == "managed_identity":
        return ManagedIdentityAuth()
    raise AuthError(f"Unknown auth method: {method}")
