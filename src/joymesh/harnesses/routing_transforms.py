"""Provider and external-router transforms beneath harness adapters."""

from __future__ import annotations

from urllib.parse import urlparse

from joymesh.harnesses.contracts import (
    BillingMode,
    Confidence,
    FundingKind,
    FundingSource,
    RouteTransform,
    RouteTransformKind,
)


def fireworks_transform(
    resource_name: str,
    *,
    billing_mode: BillingMode = BillingMode.PAID_API,
) -> RouteTransform:
    if not resource_name.startswith("accounts/") or "/routers/" not in resource_name:
        raise ValueError("Fireworks router must be an accounts/.../routers/... resource")
    return RouteTransform(
        id=f"fireworks:{resource_name}",
        kind=RouteTransformKind.FIREWORKS_ROUTER,
        provider="fireworks",
        model=resource_name,
        endpoint="https://api.fireworks.ai/inference/v1",
        funding=FundingSource(
            kind=FundingKind.API,
            provider="fireworks",
            billing_mode=billing_mode,
            confidence=Confidence.CONFIRMED,
        ),
        requires_approval=billing_mode is BillingMode.PAID_API,
    )


def compatible_router_transform(
    *,
    transform_id: str,
    endpoint: str,
    provider: str,
    model: str,
    billing_mode: BillingMode = BillingMode.UNKNOWN,
) -> RouteTransform:
    parsed = urlparse(endpoint)
    if parsed.scheme != "https" or not parsed.netloc:
        raise ValueError("external router endpoint must be an absolute HTTPS URL")
    return RouteTransform(
        id=transform_id,
        kind=RouteTransformKind.OPENAI_COMPATIBLE,
        provider=provider,
        model=model,
        endpoint=endpoint.rstrip("/"),
        funding=FundingSource(
            kind=FundingKind.UNKNOWN,
            provider=provider,
            billing_mode=billing_mode,
            confidence=Confidence.UNKNOWN,
        ),
        requires_approval=billing_mode is not BillingMode.INCLUDED_ALLOWANCE,
    )
