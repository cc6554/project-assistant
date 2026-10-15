"""Scoped GitHub token access for deterministic GitHub tools.

The token is deliberately kept out of environment variables and Git's global
credential helpers.  The integrations layer loads a workspace-bound encrypted
record and installs it only for the duration of one explicit operation.
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from contextvars import ContextVar, Token
from typing import Iterator


GITHUB_TOKEN_ENV = "AGENT_COACH_GITHUB_TOKEN"
_MAX_TOKEN_BYTES = 8192
_UNSET = object()
_GITHUB_TOKEN: ContextVar[object] = ContextVar("agent_coach_github_token", default=_UNSET)


def _clean_token(value: object) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    clean = value.strip()
    if any(ord(char) < 0x20 or ord(char) == 0x7F for char in clean):
        return None
    if len(clean.encode("utf-8")) > _MAX_TOKEN_BYTES:
        return None
    return clean


def environment_github_token() -> str | None:
    """Read the explicitly named CLI compatibility variable only."""

    return _clean_token(os.environ.get(GITHUB_TOKEN_ENV))


def github_token() -> str | None:
    """Return a scoped token, or the explicit CLI environment token by default.

    A scope containing ``None`` deliberately suppresses the environment
    fallback.  This lets a workspace-level clear action remain clear even when
    an old CLI environment variable is still present.
    """

    value = _GITHUB_TOKEN.get()
    if value is not _UNSET:
        return value if isinstance(value, str) else None
    return environment_github_token()


@contextmanager
def github_token_scope(token: str | None) -> Iterator[None]:
    """Install a token for the current context and restore the prior value."""

    clean = _clean_token(token)
    marker: Token[object] = _GITHUB_TOKEN.set(clean)
    try:
        yield
    finally:
        _GITHUB_TOKEN.reset(marker)


__all__ = ["GITHUB_TOKEN_ENV", "environment_github_token", "github_token", "github_token_scope"]
