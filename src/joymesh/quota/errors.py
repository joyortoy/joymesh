"""Quota-layer errors (non-fatal for execution)."""

from __future__ import annotations


class QuotaError(Exception):
    """Base quota error. Providers should catch and return UNKNOWN instead of raising."""


class QuotaProviderUnsupported(QuotaError):
    """Harness does not expose a quota provider."""


class QuotaProbeError(QuotaError):
    """A provider probe failed; callers should degrade to UNKNOWN/OBSERVED."""
