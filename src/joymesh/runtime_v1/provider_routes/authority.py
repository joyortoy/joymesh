"""Coordinator-scoped authority for provider-route mutations.

Defence in depth: raw manager mutation primitives refuse to run unless the
current asyncio Task holds a matching ``MutationAuthority`` ContextVar set by
``ProviderRouteMutationCoordinator`` (or recovery). ContextVars do not leak
across unrelated tasks that were created without the authority.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass
from typing import Literal
from uuid import uuid4

MutationPurpose = Literal["lifecycle", "serialised", "recovery", "test"]


@dataclass(frozen=True)
class MutationAuthority:
    """Unforgeable-per-task proof that a coordinator authorised a mutation."""

    token: str
    manager_id: str
    connector_id: str
    purpose: MutationPurpose
    lease_id: str | None = None


class MutationAuthorityError(PermissionError):
    """Raised when a raw mutation is attempted without coordinator authority."""

    def __init__(
        self,
        message: str = "provider-route mutation requires coordinator authority",
    ) -> None:
        super().__init__(message)
        self.reason_code = "mutation_authority_required"
        self.message = message


_AUTHORITY: ContextVar[MutationAuthority | None] = ContextVar(
    "joymesh_provider_route_mutation_authority",
    default=None,
)


def current_mutation_authority() -> MutationAuthority | None:
    return _AUTHORITY.get()


def require_mutation_authority(*, manager_id: str, connector_id: str) -> MutationAuthority:
    authority = _AUTHORITY.get()
    if authority is None:
        raise MutationAuthorityError(
            "provider-route mutation requires an active coordinator lease context"
        )
    if authority.manager_id != manager_id or authority.connector_id != connector_id:
        raise MutationAuthorityError(
            "provider-route mutation authority does not match manager/connector"
        )
    return authority


@contextmanager
def mutation_authority(
    *,
    manager_id: str,
    connector_id: str,
    purpose: MutationPurpose,
    lease_id: str | None = None,
    token: str | None = None,
) -> Iterator[MutationAuthority]:
    """Install mutation authority for the current task until the block exits."""

    authority = MutationAuthority(
        token=token or uuid4().hex,
        manager_id=manager_id,
        connector_id=connector_id,
        purpose=purpose,
        lease_id=lease_id,
    )
    reset_token: Token[MutationAuthority | None] = _AUTHORITY.set(authority)
    try:
        yield authority
    finally:
        _AUTHORITY.reset(reset_token)


@contextmanager
def mutation_authority_for_tests(
    *,
    manager_id: str,
    connector_id: str,
) -> Iterator[MutationAuthority]:
    """Explicit test-only authority for unit-testing raw manager primitives."""

    with mutation_authority(
        manager_id=manager_id,
        connector_id=connector_id,
        purpose="test",
    ) as authority:
        yield authority
