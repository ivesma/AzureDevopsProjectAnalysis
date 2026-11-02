import json

import pytest
import requests

from azdo_analyser.core.auth import PatAuth
from azdo_analyser.core.client import AzDoError, AzureDevOpsClient, NotFound, PermissionDenied, seg


class Resp:
    def __init__(self, status=200, data=None, headers=None, content_type="application/json"):
        self.status_code = status
        self._data = data
        self.headers = {"Content-Type": content_type, **(headers or {})}
        self.content = b"" if data is None else json.dumps(data).encode()
        self.text = self.content.decode() if data is not None else ""

    def json(self):
        if self._data is None:
            raise ValueError("no json")
        return self._data


class Session:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def request(self, method, url, **kw):
        self.calls.append((method, url, kw))
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def client(responses, **kw):
    sleeps = []
    c = AzureDevOpsClient("https://dev.azure.com/org", PatAuth("x"), session=Session(responses),
                          sleep=sleeps.append, **kw)
    return c, sleeps


def test_hosts():
    c, _ = client([])
    assert c.host() == "https://dev.azure.com/org"
    assert c.host("vsrm") == "https://vsrm.dev.azure.com/org"
    assert c.url("https://x/y") == "https://x/y"
    legacy = AzureDevOpsClient("https://org.visualstudio.com", PatAuth("x"))
    assert legacy.host("feeds") == "https://org.feeds.visualstudio.com"
    server = AzureDevOpsClient("https://tfs.local/tfs/Coll", PatAuth("x"))
    assert server.host("vsrm") == "https://tfs.local/tfs/Coll"
    assert seg("My Project") == "My%20Project"


def test_get_adds_api_version_and_auth():
    c, _ = client([Resp(data={"ok": 1})])
    assert c.get("p/_apis/x", {"a": 1}) == {"ok": 1}
    method, url, kw = c.session.calls[0]
    assert url == "https://dev.azure.com/org/p/_apis/x"
    assert kw["params"] == {"a": 1, "api-version": "7.1"}
    assert kw["headers"]["Authorization"].startswith("Basic ")


def test_retry_on_throttle_and_server_errors():
    c, sleeps = client([Resp(429, {}, {"Retry-After": "3"}), Resp(503, {}), Resp(data={"v": 1})])
    assert c.get("x") == {"v": 1}
    assert sleeps == [3.0, 4]


def test_retry_connection_error_then_give_up():
    c, sleeps = client([requests.ConnectionError("boom")] * 3, max_retries=2)
    with pytest.raises(AzDoError, match="boom"):
        c.get("x")
    assert len(sleeps) == 2


@pytest.mark.parametrize("status,exc", [(401, PermissionDenied), (403, PermissionDenied), (404, NotFound),
                                        (400, AzDoError), (500, AzDoError)])
def test_error_mapping(status, exc):
    c, _ = client([Resp(status, {"message": "m"})], max_retries=0)
    with pytest.raises(exc):
        c.get("x")


def test_sign_in_page_is_permission_denied():
    c, _ = client([Resp(203, {"x": 1}, content_type="text/html")])
    with pytest.raises(PermissionDenied, match="sign-in"):
        c.get("x")


def test_continuation_paging_and_limit():
    pages = [Resp(data={"value": [1, 2]}, headers={"x-ms-continuationtoken": "t1"}),
             Resp(data={"value": [3]}, headers={})]
    c, _ = client(pages)
    assert c.get_all("x") == [1, 2, 3]
    assert c.session.calls[1][2]["params"]["continuationToken"] == "t1"
    c2, _ = client([Resp(data={"value": [1, 2, 3]}, headers={"x-ms-continuationtoken": "t"})])
    assert c2.get_all("x", limit=2) == [1, 2]
    c3, _ = client([Resp(data=[4, 5])])
    assert c3.get_all("x") == [4, 5]


def test_skip_paging():
    c, _ = client([Resp(data={"value": [1, 2]}), Resp(data={"value": [3]})])
    assert c.get_skip_paged("x", page_size=2) == [1, 2, 3]
    assert c.session.calls[1][2]["params"]["$skip"] == 2
    c2, _ = client([Resp(data={"value": [1, 2]}), Resp(data={"value": [3, 4]})])
    assert c2.get_skip_paged("x", page_size=2, limit=3) == [1, 2, 3, 4][:4]


def test_post_put_text_and_empty_body():
    c, _ = client([Resp(data={"id": 1}), Resp(data=None), Resp(data={"t": 1})])
    assert c.post("x", {"a": 1}) == {"id": 1}
    assert c.session.calls[0][2]["headers"]["Content-Type"] == "application/json"
    assert c.put("x", {"b": 2}).status_code == 200
    assert c.get_text("x") == '{"t": 1}'
