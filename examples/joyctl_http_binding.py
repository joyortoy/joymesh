"""Authenticated HTTP seam for existing JoyCtl routes; no credential issuer.

An existing trusted authentication integration must supply a verified session.
This adapter cannot establish a login, grant authority, enroll dots or bypass
JoyCtl's bearer verification/RBAC/tenant checks. No automatic POST retries.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from sam_dot_bridge import TransportContext


@dataclass(frozen=True)
class VerifiedSession:
    context: TransportContext
    bearer: str = field(repr=False)


class NoRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class JoyCtlHTTPBinding:
    def __init__(self, endpoint: str,
                 session_for: Callable[[TransportContext], VerifiedSession], *,
                 allow_loopback_http: bool = False, opener=None):
        url = urlsplit(endpoint)
        local_http = (allow_loopback_http and url.scheme == "http"
                      and url.hostname in {"127.0.0.1", "::1", "localhost"})
        if (url.scheme != "https" and not local_http) or not url.hostname or (
            url.username or url.password or url.query or url.fragment
            or url.path not in {"", "/"}
        ):
            raise ValueError("fixed HTTPS JoyCtl origin required")
        self.endpoint = endpoint.rstrip("/")
        self.session_for = session_for
        self.opener = opener if opener is not None else build_opener(NoRedirects())

    def request(self, ctx, method, path, body=None):
        allowed = (
            (method == "POST" and path in {"/api/missions", "/api/executions"})
            or (method == "GET" and re.fullmatch(
                r"/api/(?:missions|executions)/[A-Za-z0-9_.%~-]+"
                r"(?:/(?:graph|evidence|verification))?", path))
        )
        if not allowed or (method == "GET" and body is not None):
            raise ValueError("unsupported JoyCtl route")
        if method == "POST":
            fields = ({"workspace_id", "project_id", "title"} if path == "/api/missions"
                      else {"mission_id", "step_id", "authorization_id", "prompt_id"})
            if (not isinstance(body, dict) or set(body) != fields or not all(
                isinstance(value, str) and value.strip() for value in body.values()
            )):
                raise ValueError("supported canonical intake fields required")
        session = self.session_for(ctx)  # existing verified authentication only
        if session.context != ctx or not ctx.subject or not ctx.organisation_id:
            raise PermissionError("verified session context mismatch")
        if not session.bearer or any(c.isspace() for c in session.bearer):
            raise PermissionError("valid existing bearer required")
        data = None if body is None else json.dumps(body, allow_nan=False).encode()
        req = Request(self.endpoint + path, data=data, method=method, headers={
            "Authorization": "Bearer " + session.bearer,
            "Content-Type": "application/json", "Accept": "application/json",
        })
        try:
            with self.opener.open(req, timeout=10) as response:
                content = response.read(1024 * 1024 + 1)
                if len(content) > 1024 * 1024:
                    raise ValueError("JoyCtl response exceeds limit")
                value = json.loads(content)
        except HTTPError as error:
            if error.code in {401, 403}:
                raise PermissionError("JoyCtl denied request") from None
            raise RuntimeError(f"JoyCtl HTTP status {error.code}; reconcile before retry") from None
        except URLError:
            raise RuntimeError("JoyCtl unavailable; reconcile before retry") from None
        if not isinstance(value, dict):
            raise ValueError("JoyCtl object response required")
        return value

    def assignment(self, ctx, offer_id):
        raise PermissionError("supported authoritative assignment binding required")

    def record_result(self, ctx, offer, result):
        raise PermissionError("supported canonical result binding required")
