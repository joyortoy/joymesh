import sys
from io import BytesIO
from pathlib import Path
from urllib.error import HTTPError

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "examples"))

from joyctl_http_binding import JoyCtlHTTPBinding, NoRedirects, VerifiedSession
from sam_dot_bridge import Bridge, TransportContext

CTX = TransportContext("org-one", "subject-one")


class Opener:
    def __init__(self, content=b'{"mission_id":"mission-one","state":"draft"}'):
        self.content = content
        self.calls = []

    def open(self, req, timeout):
        self.calls.append((req, timeout))
        if isinstance(self.content, Exception):
            raise self.content
        return BytesIO(self.content)


def binding(opener, session=None):
    return JoyCtlHTTPBinding(
        "https://joyctl.example",
        lambda ctx: session or VerifiedSession(CTX, "existing-test-session"),
        opener=opener,
    )


def test_draft_uses_existing_bearer_and_existing_endpoint_once():
    opener = Opener()
    result = Bridge(binding(opener)).call(
        CTX, "intent_submit", {"workspace_id": "ws", "project_id": "project", "title": "Safe draft"}
    )
    assert result["state"] == "draft"
    req, timeout = opener.calls[0]
    assert req.full_url == "https://joyctl.example/api/missions"
    assert req.get_header("Authorization") == "Bearer existing-test-session"
    assert b"policy_grant" not in req.data
    assert len(opener.calls) == 1 and timeout == 10


@pytest.mark.parametrize(
    "url",
    [
        "http://joyctl.example",
        "https://user:pass@host",
        "https://host/base",
        "https://host?token=secret",
    ],
)
def test_unsafe_origin_rejected(url):
    with pytest.raises(ValueError):
        JoyCtlHTTPBinding(url, lambda ctx: VerifiedSession(CTX, "test"))


def test_context_mismatch_never_sends_request():
    opener = Opener()
    adapter = binding(opener, VerifiedSession(TransportContext("other", "other"), "test"))
    with pytest.raises(PermissionError):
        adapter.request(CTX, "GET", "/api/missions/one")
    assert opener.calls == []


def test_denied_post_not_retried_and_token_not_in_errors_or_repr():
    opener = Opener(HTTPError("https://joyctl.example", 403, "secret", {}, None))
    with pytest.raises(PermissionError, match="JoyCtl denied") as error:
        binding(opener).request(
            CTX,
            "POST",
            "/api/missions",
            {"workspace_id": "ws", "project_id": "p", "title": "draft"},
        )
    assert "secret" not in str(error.value)
    assert "test-secret" not in repr(VerifiedSession(CTX, "test-secret"))
    assert len(opener.calls) == 1


def test_redirects_and_unsupported_authority_operations_fail_closed():
    assert NoRedirects().redirect_request(None, None, 302, "", {}, "https://other") is None
    adapter = binding(Opener())
    with pytest.raises(ValueError):
        adapter.request(CTX, "POST", "/api/grants", {})
    with pytest.raises(ValueError):
        adapter.request(
            CTX,
            "POST",
            "/api/missions",
            {
                "workspace_id": "ws",
                "project_id": "p",
                "title": "draft",
                "policy_grant": "untrusted",
            },
        )
    with pytest.raises(PermissionError):
        adapter.assignment(CTX, "offer")
    with pytest.raises(PermissionError):
        adapter.record_result(CTX, None, None)


def test_response_limit():
    with pytest.raises(ValueError, match="exceeds limit"):
        binding(Opener(b" " * (1024 * 1024 + 1))).request(CTX, "GET", "/api/missions/one")
