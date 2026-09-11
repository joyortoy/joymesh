"""Official CLI / documented-signal quota providers for supported harnesses.

Providers never scrape websites and never store secrets. They only record
factual observations derived from PATH detection, documented CLI status
output, environment configuration presence, and execution results.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from joymesh.models import FailureKind
from joymesh.quota.contracts import (
    HarnessAvailability,
    QuotaSnapshot,
    QuotaSource,
    QuotaState,
    QuotaVisibility,
)
from joymesh.security import filter_environment


def utc_now() -> datetime:
    return datetime.now(UTC)


def _unknown(harness_id: str, *, metadata: Mapping[str, Any] | None = None) -> QuotaSnapshot:
    return QuotaSnapshot(
        harness_id=harness_id,
        availability=HarnessAvailability.UNKNOWN,
        quota_visibility=QuotaVisibility.UNKNOWN,
        state=QuotaState.UNKNOWN,
        authenticated=False,
        configured=False,
        credits_remaining=None,
        requests_remaining=None,
        tokens_remaining=None,
        reset_at=None,
        observed_at=utc_now(),
        source=QuotaSource.NONE,
        raw_metadata=dict(metadata or {}),
    )


def _run_cli(
    argv: tuple[str, ...],
    *,
    timeout: float = 8.0,
    extra_env_keys: frozenset[str] | None = None,
) -> tuple[int, str]:
    env = filter_environment(extra_keys=extra_env_keys or frozenset())
    try:
        completed = subprocess.run(
            list(argv),
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
            stdin=subprocess.DEVNULL,
            check=False,
        )
    except FileNotFoundError:
        return 127, "executable_not_found"
    except subprocess.TimeoutExpired as exc:
        out_bytes = (exc.stdout or b"") + (exc.stderr or b"")
        if isinstance(out_bytes, bytes):
            out = out_bytes.decode(errors="replace")
        else:
            out = str(out_bytes)
        return 124, out or "probe_timeout"
    output = (completed.stdout or "") + (completed.stderr or "")
    return completed.returncode, output


class BaseQuotaProvider:
    harness_id: str

    def quota_snapshot(self) -> QuotaSnapshot:
        raise NotImplementedError

    def apply_observation(
        self,
        snapshot: QuotaSnapshot,
        *,
        success: bool,
        failure_kind: FailureKind | None,
        detail: str | None = None,
        usage_tokens: int | None = None,
    ) -> QuotaSnapshot:
        """Merge an execution observation into a prior snapshot."""
        meta = dict(snapshot.raw_metadata)
        meta["last_observation"] = {
            "success": success,
            "failure_kind": failure_kind.value if failure_kind else None,
            "detail": (detail or "")[:240] or None,
            "usage_tokens": usage_tokens,
        }
        if success:
            return QuotaSnapshot(
                harness_id=snapshot.harness_id,
                availability=HarnessAvailability.READY,
                quota_visibility=(
                    snapshot.quota_visibility
                    if snapshot.quota_visibility is not QuotaVisibility.UNKNOWN
                    else QuotaVisibility.OBSERVED
                ),
                state=QuotaState.AVAILABLE,
                authenticated=True,
                configured=True,
                credits_remaining=snapshot.credits_remaining,
                requests_remaining=snapshot.requests_remaining,
                tokens_remaining=snapshot.tokens_remaining,
                reset_at=snapshot.reset_at,
                observed_at=utc_now(),
                source=QuotaSource.EXECUTION_RESULT,
                raw_metadata=meta,
            )
        detail_lower = (detail or "").lower()
        config_missing = (
            ("api key" in detail_lower or "apikey" in detail_lower)
            and (
                "missing" in detail_lower
                or "required" in detail_lower
                or "not set" in detail_lower
                or "invalid" in detail_lower
            )
        ) or "configuration required" in detail_lower
        if config_missing or (
            failure_kind is FailureKind.INVALID_REQUEST and "api key" in detail_lower
        ):
            return QuotaSnapshot(
                harness_id=snapshot.harness_id,
                availability=HarnessAvailability.CONFIGURATION_REQUIRED,
                quota_visibility=QuotaVisibility.OBSERVED,
                state=QuotaState.BLOCKED,
                authenticated=False,
                configured=False,
                credits_remaining=snapshot.credits_remaining,
                requests_remaining=snapshot.requests_remaining,
                tokens_remaining=snapshot.tokens_remaining,
                reset_at=snapshot.reset_at,
                observed_at=utc_now(),
                source=QuotaSource.EXECUTION_RESULT,
                raw_metadata=meta,
            )
        if failure_kind is FailureKind.AUTHENTICATION:
            return QuotaSnapshot(
                harness_id=snapshot.harness_id,
                availability=HarnessAvailability.AUTHENTICATION_REQUIRED,
                quota_visibility=QuotaVisibility.OBSERVED,
                state=QuotaState.BLOCKED,
                authenticated=False,
                configured=snapshot.configured,
                credits_remaining=snapshot.credits_remaining,
                requests_remaining=snapshot.requests_remaining,
                tokens_remaining=snapshot.tokens_remaining,
                reset_at=snapshot.reset_at,
                observed_at=utc_now(),
                source=QuotaSource.EXECUTION_RESULT,
                raw_metadata=meta,
            )
        if failure_kind is FailureKind.QUOTA_EXHAUSTED:
            return QuotaSnapshot(
                harness_id=snapshot.harness_id,
                availability=HarnessAvailability.QUOTA_EXHAUSTED,
                quota_visibility=QuotaVisibility.OBSERVED,
                state=QuotaState.EXHAUSTED,
                authenticated=snapshot.authenticated,
                configured=snapshot.configured,
                credits_remaining=0.0,
                requests_remaining=0,
                tokens_remaining=snapshot.tokens_remaining,
                reset_at=snapshot.reset_at,
                observed_at=utc_now(),
                source=QuotaSource.EXECUTION_RESULT,
                raw_metadata=meta,
            )
        if failure_kind is FailureKind.RATE_LIMIT:
            return QuotaSnapshot(
                harness_id=snapshot.harness_id,
                availability=HarnessAvailability.RATE_LIMITED,
                quota_visibility=QuotaVisibility.OBSERVED,
                state=QuotaState.BLOCKED,
                authenticated=snapshot.authenticated,
                configured=snapshot.configured,
                credits_remaining=snapshot.credits_remaining,
                requests_remaining=snapshot.requests_remaining,
                tokens_remaining=snapshot.tokens_remaining,
                reset_at=snapshot.reset_at,
                observed_at=utc_now(),
                source=QuotaSource.EXECUTION_RESULT,
                raw_metadata=meta,
            )
        # Generic process/provider failure — keep prior auth/config facts.
        availability = snapshot.availability
        if "provider unavailable" in (detail or "").lower():
            availability = HarnessAvailability.PROVIDER_UNAVAILABLE
        return QuotaSnapshot(
            harness_id=snapshot.harness_id,
            availability=availability,
            quota_visibility=QuotaVisibility.OBSERVED,
            state=snapshot.state,
            authenticated=snapshot.authenticated,
            configured=snapshot.configured,
            credits_remaining=snapshot.credits_remaining,
            requests_remaining=snapshot.requests_remaining,
            tokens_remaining=snapshot.tokens_remaining,
            reset_at=snapshot.reset_at,
            observed_at=utc_now(),
            source=QuotaSource.EXECUTION_RESULT,
            raw_metadata=meta,
        )


class CodexQuotaProvider(BaseQuotaProvider):
    harness_id = "codex"

    def quota_snapshot(self) -> QuotaSnapshot:
        executable = shutil.which("codex")
        if executable is None:
            return QuotaSnapshot(
                harness_id=self.harness_id,
                availability=HarnessAvailability.OFFLINE,
                quota_visibility=QuotaVisibility.OBSERVED,
                state=QuotaState.BLOCKED,
                authenticated=False,
                configured=False,
                credits_remaining=None,
                requests_remaining=None,
                tokens_remaining=None,
                reset_at=None,
                observed_at=utc_now(),
                source=QuotaSource.OFFICIAL_CLI,
                raw_metadata={"detail": "executable_not_found"},
            )
        code, output = _run_cli((executable, "login", "status"))
        lowered = output.lower()
        authenticated = code == 0 and (
            "logged in" in lowered or "chatgpt" in lowered or "api key" in lowered
        )
        if "out of credits" in lowered or "credits exhausted" in lowered:
            return QuotaSnapshot(
                harness_id=self.harness_id,
                availability=HarnessAvailability.QUOTA_EXHAUSTED,
                quota_visibility=QuotaVisibility.OBSERVED,
                state=QuotaState.EXHAUSTED,
                authenticated=authenticated or "logged in" in lowered,
                configured=True,
                credits_remaining=0.0,
                requests_remaining=0,
                tokens_remaining=None,
                reset_at=None,
                observed_at=utc_now(),
                source=QuotaSource.OFFICIAL_CLI,
                raw_metadata={"login_status_code": code},
            )
        if not authenticated:
            return QuotaSnapshot(
                harness_id=self.harness_id,
                availability=HarnessAvailability.AUTHENTICATION_REQUIRED,
                quota_visibility=QuotaVisibility.OBSERVED,
                state=QuotaState.BLOCKED,
                authenticated=False,
                configured=True,
                credits_remaining=None,
                requests_remaining=None,
                tokens_remaining=None,
                reset_at=None,
                observed_at=utc_now(),
                source=QuotaSource.OFFICIAL_CLI,
                raw_metadata={"login_status_code": code},
            )
        # Official remaining-credit amounts are not exposed by login status.
        return QuotaSnapshot(
            harness_id=self.harness_id,
            availability=HarnessAvailability.READY,
            quota_visibility=QuotaVisibility.OBSERVED,
            state=QuotaState.AVAILABLE,
            authenticated=True,
            configured=True,
            credits_remaining=None,
            requests_remaining=None,
            tokens_remaining=None,
            reset_at=None,
            observed_at=utc_now(),
            source=QuotaSource.OFFICIAL_CLI,
            raw_metadata={"login_status_code": code},
        )


class ClaudeQuotaProvider(BaseQuotaProvider):
    harness_id = "claude-code"

    def quota_snapshot(self) -> QuotaSnapshot:
        executable = shutil.which("claude")
        if executable is None:
            return QuotaSnapshot(
                harness_id=self.harness_id,
                availability=HarnessAvailability.OFFLINE,
                quota_visibility=QuotaVisibility.OBSERVED,
                state=QuotaState.BLOCKED,
                authenticated=False,
                configured=False,
                credits_remaining=None,
                requests_remaining=None,
                tokens_remaining=None,
                reset_at=None,
                observed_at=utc_now(),
                source=QuotaSource.OFFICIAL_CLI,
                raw_metadata={"detail": "executable_not_found"},
            )
        code, output = _run_cli((executable, "auth", "status"))
        logged_in = False
        try:
            payload = json.loads(output.strip().splitlines()[-1] if output.strip() else "{}")
            if isinstance(payload, dict):
                logged_in = bool(payload.get("loggedIn"))
        except json.JSONDecodeError:
            logged_in = "logged in" in output.lower() and "false" not in output.lower()
        if code != 0 and not logged_in:
            # Fall back to textual cues from documented auth status.
            logged_in = '"loggedIn": true' in output.replace(" ", "")
        if not logged_in:
            return QuotaSnapshot(
                harness_id=self.harness_id,
                availability=HarnessAvailability.AUTHENTICATION_REQUIRED,
                quota_visibility=QuotaVisibility.OBSERVED,
                state=QuotaState.BLOCKED,
                authenticated=False,
                configured=True,
                credits_remaining=None,
                requests_remaining=None,
                tokens_remaining=None,
                reset_at=None,
                observed_at=utc_now(),
                source=QuotaSource.OFFICIAL_CLI,
                raw_metadata={"auth_status_code": code},
            )
        return QuotaSnapshot(
            harness_id=self.harness_id,
            availability=HarnessAvailability.READY,
            quota_visibility=QuotaVisibility.OBSERVED,
            state=QuotaState.AVAILABLE,
            authenticated=True,
            configured=True,
            credits_remaining=None,
            requests_remaining=None,
            tokens_remaining=None,
            reset_at=None,
            observed_at=utc_now(),
            source=QuotaSource.OFFICIAL_CLI,
            raw_metadata={"auth_status_code": code},
        )


class GeminiQuotaProvider(BaseQuotaProvider):
    harness_id = "gemini-cli"

    def quota_snapshot(self) -> QuotaSnapshot:
        executable = shutil.which("gemini")
        if executable is None:
            return QuotaSnapshot(
                harness_id=self.harness_id,
                availability=HarnessAvailability.OFFLINE,
                quota_visibility=QuotaVisibility.OBSERVED,
                state=QuotaState.BLOCKED,
                authenticated=False,
                configured=False,
                credits_remaining=None,
                requests_remaining=None,
                tokens_remaining=None,
                reset_at=None,
                observed_at=utc_now(),
                source=QuotaSource.OFFICIAL_CLI,
                raw_metadata={"detail": "executable_not_found"},
            )
        api_key = bool(
            os.environ.get("GEMINI_API_KEY")
            or os.environ.get("GOOGLE_API_KEY")
            or os.environ.get("GOOGLE_GENAI_API_KEY")
        )
        vertex = bool(os.environ.get("GOOGLE_GENAI_USE_VERTEXAI"))
        gca = bool(os.environ.get("GOOGLE_GENAI_USE_GCA"))
        settings_path = Path.home() / ".gemini" / "settings.json"
        settings_configured = False
        if settings_path.is_file():
            try:
                settings = json.loads(settings_path.read_text(encoding="utf-8"))
                auth = (
                    settings.get("security", {}).get("auth", {})
                    if isinstance(settings, dict)
                    else {}
                )
                selected = auth.get("selectedType") if isinstance(auth, dict) else None
                settings_configured = bool(selected)
            except (OSError, json.JSONDecodeError):
                settings_configured = False
        configured = api_key or vertex or gca or settings_configured
        if not configured:
            return QuotaSnapshot(
                harness_id=self.harness_id,
                availability=HarnessAvailability.CONFIGURATION_REQUIRED,
                quota_visibility=QuotaVisibility.OBSERVED,
                state=QuotaState.BLOCKED,
                authenticated=False,
                configured=False,
                credits_remaining=None,
                requests_remaining=None,
                tokens_remaining=None,
                reset_at=None,
                observed_at=utc_now(),
                source=QuotaSource.OFFICIAL_CLI,
                raw_metadata={
                    "detail": "GEMINI_API_KEY_or_auth_method_required",
                    "api_key_env_present": api_key,
                },
            )
        return QuotaSnapshot(
            harness_id=self.harness_id,
            availability=HarnessAvailability.READY,
            quota_visibility=QuotaVisibility.OBSERVED,
            state=QuotaState.AVAILABLE,
            authenticated=True,
            configured=True,
            credits_remaining=None,
            requests_remaining=None,
            tokens_remaining=None,
            reset_at=None,
            observed_at=utc_now(),
            source=QuotaSource.OFFICIAL_CLI,
            raw_metadata={
                "api_key_env_present": api_key,
                "vertex": vertex,
                "gca": gca,
                "settings_configured": settings_configured,
            },
        )


class OpenCodeQuotaProvider(BaseQuotaProvider):
    harness_id = "opencode"

    def quota_snapshot(self) -> QuotaSnapshot:
        executable = shutil.which("opencode")
        if executable is None:
            return QuotaSnapshot(
                harness_id=self.harness_id,
                availability=HarnessAvailability.OFFLINE,
                quota_visibility=QuotaVisibility.OBSERVED,
                state=QuotaState.BLOCKED,
                authenticated=False,
                configured=False,
                credits_remaining=None,
                requests_remaining=None,
                tokens_remaining=None,
                reset_at=None,
                observed_at=utc_now(),
                source=QuotaSource.OFFICIAL_CLI,
                raw_metadata={"detail": "executable_not_found"},
            )
        code, output = _run_cli((executable, "--version"))
        if code != 0:
            return QuotaSnapshot(
                harness_id=self.harness_id,
                availability=HarnessAvailability.PROVIDER_UNAVAILABLE,
                quota_visibility=QuotaVisibility.OBSERVED,
                state=QuotaState.BLOCKED,
                authenticated=False,
                configured=True,
                credits_remaining=None,
                requests_remaining=None,
                tokens_remaining=None,
                reset_at=None,
                observed_at=utc_now(),
                source=QuotaSource.OFFICIAL_CLI,
                raw_metadata={"version_code": code, "detail": output[:200]},
            )
        # OpenCode does not expose an official remaining-quota endpoint.
        return QuotaSnapshot(
            harness_id=self.harness_id,
            availability=HarnessAvailability.READY,
            quota_visibility=QuotaVisibility.OBSERVED,
            state=QuotaState.AVAILABLE,
            authenticated=True,
            configured=True,
            credits_remaining=None,
            requests_remaining=None,
            tokens_remaining=None,
            reset_at=None,
            observed_at=utc_now(),
            source=QuotaSource.OFFICIAL_CLI,
            raw_metadata={"version": output.strip().splitlines()[0] if output.strip() else None},
        )


class GrokQuotaProvider(BaseQuotaProvider):
    harness_id = "grok"

    def quota_snapshot(self) -> QuotaSnapshot:
        executable = shutil.which("grok")
        if executable is None:
            return QuotaSnapshot(
                harness_id=self.harness_id,
                availability=HarnessAvailability.OFFLINE,
                quota_visibility=QuotaVisibility.OBSERVED,
                state=QuotaState.BLOCKED,
                authenticated=False,
                configured=False,
                credits_remaining=None,
                requests_remaining=None,
                tokens_remaining=None,
                reset_at=None,
                observed_at=utc_now(),
                source=QuotaSource.OFFICIAL_CLI,
                raw_metadata={"detail": "executable_not_found"},
            )
        code, output = _run_cli((executable, "models"), timeout=12.0)
        lowered = output.lower()
        authenticated = code == 0 and (
            "logged in" in lowered or "available models" in lowered or "grok-" in lowered
        )
        if "not authenticated" in lowered or "not signed in" in lowered:
            authenticated = False
        if not authenticated:
            return QuotaSnapshot(
                harness_id=self.harness_id,
                availability=HarnessAvailability.AUTHENTICATION_REQUIRED,
                quota_visibility=QuotaVisibility.OBSERVED,
                state=QuotaState.BLOCKED,
                authenticated=False,
                configured=True,
                credits_remaining=None,
                requests_remaining=None,
                tokens_remaining=None,
                reset_at=None,
                observed_at=utc_now(),
                source=QuotaSource.OFFICIAL_CLI,
                raw_metadata={"models_code": code},
            )
        return QuotaSnapshot(
            harness_id=self.harness_id,
            availability=HarnessAvailability.READY,
            quota_visibility=QuotaVisibility.OBSERVED,
            state=QuotaState.AVAILABLE,
            authenticated=True,
            configured=True,
            credits_remaining=None,
            requests_remaining=None,
            tokens_remaining=None,
            reset_at=None,
            observed_at=utc_now(),
            source=QuotaSource.OFFICIAL_CLI,
            raw_metadata={"models_code": code},
        )


class UnknownQuotaProvider(BaseQuotaProvider):
    def __init__(self, harness_id: str) -> None:
        self.harness_id = harness_id

    def quota_snapshot(self) -> QuotaSnapshot:
        return _unknown(self.harness_id, metadata={"detail": "no_quota_provider"})


def builtin_quota_providers() -> dict[str, BaseQuotaProvider]:
    providers: list[BaseQuotaProvider] = [
        CodexQuotaProvider(),
        ClaudeQuotaProvider(),
        GeminiQuotaProvider(),
        OpenCodeQuotaProvider(),
        GrokQuotaProvider(),
    ]
    return {provider.harness_id: provider for provider in providers}


async def async_quota_snapshot(provider: BaseQuotaProvider) -> QuotaSnapshot:
    """Run a potentially blocking provider probe off the event loop."""
    return await asyncio.to_thread(provider.quota_snapshot)
