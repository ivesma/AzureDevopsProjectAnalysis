"""Thin, resilient Azure DevOps REST client.

The official ``azure-devops`` Python SDK does not cover several of the APIs this tool needs
(environments, checks, pipeline permissions, audit, entitlements, wiki page batches) and lags
behind the REST surface, so the platform talks to the REST API directly. The client handles:

* the different service hosts (``dev.azure.com``, ``vsrm``, ``vssps``, ``feeds`` ...)
* continuation-token and ``$top/$skip`` paging
* retries with ``Retry-After`` support for throttling (HTTP 429) and transient 5xx errors
* typed errors so collectors can record missing permissions instead of failing the run
"""

from __future__ import annotations

import logging
import time
from typing import Any, Callable, Iterator
from urllib.parse import quote, urlparse

import requests

from azdo_analyser.core.auth import AuthProvider
from azdo_analyser.core.config import org_name_from_url

log = logging.getLogger(__name__)

RETRY_STATUS = {429, 500, 502, 503, 504}


class AzDoError(RuntimeError):
    def __init__(self, message: str, status: int | None = None, url: str | None = None):
        super().__init__(message)
        self.status = status
        self.url = url


class PermissionDenied(AzDoError):
    pass


class NotFound(AzDoError):
    pass


def seg(value: str) -> str:
    """URL-encode a single path segment (project/team names may contain spaces)."""
    return quote(str(value), safe="")


class AzureDevOpsClient:
    def __init__(
        self,
        organisation_url: str,
        auth: AuthProvider,
        api_version: str = "7.1",
        timeout: int = 60,
        max_retries: int = 5,
        verify_ssl: bool = True,
        session: Any | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self.organisation_url = organisation_url.rstrip("/")
        self.organisation = org_name_from_url(self.organisation_url)
        self.auth = auth
        self.api_version = api_version
        self.timeout = timeout
        self.max_retries = max_retries
        self.verify_ssl = verify_ssl
        self.session = session or requests.Session()
        self._sleep = sleep
        self.request_count = 0

    # ------------------------------------------------------------------ hosts
    def host(self, service: str | None = None) -> str:
        """Return the base URL for a given service (None = core)."""
        if not service:
            return self.organisation_url
        parsed = urlparse(self.organisation_url)
        netloc = parsed.netloc.lower()
        if netloc == "dev.azure.com":
            return f"{parsed.scheme}://{service}.dev.azure.com/{self.organisation}"
        if netloc.endswith(".visualstudio.com"):
            return f"{parsed.scheme}://{self.organisation}.{service}.visualstudio.com"
        return self.organisation_url  # Azure DevOps Server: single host

    def url(self, path: str, service: str | None = None) -> str:
        if path.startswith("http://") or path.startswith("https://"):
            return path
        return f"{self.host(service)}/{path.lstrip('/')}"

    # --------------------------------------------------------------- requests
    def request(
        self,
        method: str,
        path: str,
        params: dict[str, Any] | None = None,
        json: Any = None,
        service: str | None = None,
        api_version: str | None = None,
        headers: dict[str, str] | None = None,
    ) -> Any:
        url = self.url(path, service)
        query = dict(params or {})
        query.setdefault("api-version", api_version or self.api_version)
        hdrs = {"Accept": "application/json", **self.auth.headers(), **(headers or {})}
        if json is not None:
            hdrs.setdefault("Content-Type", "application/json")

        attempt = 0
        while True:
            attempt += 1
            self.request_count += 1
            try:
                resp = self.session.request(
                    method, url, params=query, json=json, headers=hdrs,
                    timeout=self.timeout, verify=self.verify_ssl,
                )
            except requests.RequestException as exc:
                if attempt > self.max_retries:
                    raise AzDoError(f"{method} {url} failed: {exc}", url=url) from exc
                self._sleep(min(2 ** attempt, 30))
                continue

            status = resp.status_code
            if status in RETRY_STATUS and attempt <= self.max_retries:
                retry_after = resp.headers.get("Retry-After")
                delay = float(retry_after) if retry_after and retry_after.isdigit() else min(2 ** attempt, 30)
                log.debug("Retrying %s %s after %ss (HTTP %s)", method, url, delay, status)
                self._sleep(delay)
                continue
            if status in (401, 403):
                raise PermissionDenied(f"HTTP {status}: access denied for {url}", status, url)
            if status == 404:
                raise NotFound(f"HTTP 404: {url}", status, url)
            if status == 203 or (status < 400 and "text/html" in resp.headers.get("Content-Type", "")):
                # Azure DevOps answers invalid credentials with a sign-in page (HTTP 203).
                raise PermissionDenied(f"Authentication failed (sign-in page returned) for {url}", status, url)
            if status >= 400:
                raise AzDoError(f"HTTP {status} for {method} {url}: {resp.text[:300]}", status, url)
            return resp

    def get(self, path: str, params: dict[str, Any] | None = None, **kw) -> Any:
        resp = self.request("GET", path, params=params, **kw)
        return _json(resp)

    def post(self, path: str, json: Any, params: dict[str, Any] | None = None, **kw) -> Any:
        resp = self.request("POST", path, params=params, json=json, **kw)
        return _json(resp)

    def put(self, path: str, json: Any, params: dict[str, Any] | None = None, **kw) -> Any:
        return self.request("PUT", path, params=params, json=json, **kw)

    def get_text(self, path: str, params: dict[str, Any] | None = None, **kw) -> str:
        resp = self.request("GET", path, params=params, headers={"Accept": "text/plain"}, **kw)
        return resp.text

    # ----------------------------------------------------------------- paging
    def get_all(
        self, path: str, params: dict[str, Any] | None = None, limit: int | None = None, **kw
    ) -> list[Any]:
        """Follow ``x-ms-continuationtoken`` paging and return the concatenated ``value`` list."""
        return list(self.iter_all(path, params, limit=limit, **kw))

    def iter_all(
        self, path: str, params: dict[str, Any] | None = None, limit: int | None = None, **kw
    ) -> Iterator[Any]:
        query = dict(params or {})
        produced = 0
        while True:
            resp = self.request("GET", path, params=query, **kw)
            data = _json(resp)
            batch = data.get("value", []) if isinstance(data, dict) else (data or [])
            for item in batch:
                yield item
                produced += 1
                if limit and produced >= limit:
                    return
            token = resp.headers.get("x-ms-continuationtoken")
            if not token or not batch:
                return
            query["continuationToken"] = token

    def get_skip_paged(
        self,
        path: str,
        params: dict[str, Any] | None = None,
        page_size: int = 100,
        limit: int | None = None,
        top_param: str = "$top",
        skip_param: str = "$skip",
        **kw,
    ) -> list[Any]:
        """Page APIs that use ``$top``/``$skip`` (commits, pull requests ...)."""
        items: list[Any] = []
        skip = 0
        while True:
            size = page_size if not limit else min(page_size, limit - len(items))
            query = {**(params or {}), top_param: size, skip_param: skip}
            data = self.get(path, params=query, **kw)
            batch = data.get("value", []) if isinstance(data, dict) else (data or [])
            items.extend(batch)
            skip += len(batch)
            if len(batch) < size or (limit and len(items) >= limit):
                return items


def _json(resp: Any) -> Any:
    if not resp.content:
        return {}
    try:
        return resp.json()
    except ValueError:
        return {"text": resp.text}
