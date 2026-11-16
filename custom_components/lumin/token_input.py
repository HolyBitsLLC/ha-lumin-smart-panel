"""Parsing for the token data pasted into the Lumin config flow.

Deliberately free of Home Assistant imports so the unit-test job can import it
directly (that job installs only ``pytest`` and ``aiohttp``).

The Lumin portal keeps its Auth0 session in Local Storage. The value of the auth
entry is JSON shaped like::

    {"body": {"access_token": "eyJ...", "refresh_token": "v1...", ...}}

``body`` can also be the same object as a JSON-encoded *string* (the Auth0 SPA
SDK double-encodes it), or the object can be pasted on its own. Users also paste
just the access token, usually with the quotes or the ``Bearer `` prefix that
the DevTools value viewer leaves around it -- and sometimes they paste the
refresh token on its own, because it is the "durable key" in the docs.

Each of those has a distinct, actionable outcome instead of one generic parse
failure. Token material is never logged here.
"""

from __future__ import annotations

import base64
import binascii
import json
import re
from dataclasses import dataclass
from typing import Any

# Error keys. These are used verbatim as ``errors["base"]`` values, so every one
# of them must exist in both strings.json and translations/en.json.
ERROR_INVALID = "invalid_token_format"
ERROR_REFRESH_TOKEN_ONLY = "refresh_token_only"

_ACCESS_KEYS = ("access_token", "accessToken")
_REFRESH_KEYS = ("refresh_token", "refreshToken")

# Token literals inside a JSON blob that was truncated mid-copy. Only used when
# the blob does not parse, so escaped double-encoded bodies never reach this.
_ACCESS_RE = re.compile(r'"(?:access_token|accessToken)"\s*:\s*"([^"]+)"')
_REFRESH_RE = re.compile(r'"(?:refresh_token|refreshToken)"\s*:\s*"([^"]+)"')

_QUOTES = "\"'"
_BEARER_RE = re.compile(r"^bearer\s+", re.IGNORECASE)
_JWT_SEGMENT_RE = re.compile(r"^[A-Za-z0-9_-]+$")
_OPAQUE_RE = re.compile(r"^[A-Za-z0-9._~+/=-]+$")

_MIN_OPAQUE_LENGTH = 20


@dataclass(frozen=True)
class ParsedTokenInput:
    """The usable credentials found in one paste, or the reason there are none."""

    access: str = ""
    refresh: str = ""
    error: str | None = None

    @property
    def ok(self) -> bool:
        return bool(self.access)

    @property
    def refreshable(self) -> bool:
        """True when a refresh token was captured alongside the access token."""
        return bool(self.access and self.refresh)


def parse_token_input(raw: Any) -> ParsedTokenInput:
    """Parse one pasted value into an access token and (optionally) a refresh token.

    Accepts, in order:
      1. the full Local Storage JSON value (``body`` as an object or a JSON string)
      2. the inner body object on its own
      3. a bare access token JWT, with surrounding whitespace, quotes or ``Bearer``
      4. a truncated JSON blob, by recovering the token literals with a regex

    A refresh token pasted on its own is reported as ``ERROR_REFRESH_TOKEN_ONLY``
    rather than being mistaken for an unusable access token.
    """
    if not isinstance(raw, str):
        return ParsedTokenInput(error=ERROR_INVALID)

    original = raw.strip()
    if not original:
        return ParsedTokenInput(error=ERROR_INVALID)

    stripped = _strip_wrappers(original)

    # `original` first so a double-encoded ("{...}") body still parses as JSON.
    for candidate in (original, stripped):
        access, refresh = _from_json(candidate)
        if access:
            return ParsedTokenInput(access=access, refresh=refresh)

    access_match = _ACCESS_RE.search(stripped)
    if access_match:
        refresh_match = _REFRESH_RE.search(stripped)
        return ParsedTokenInput(
            access=access_match.group(1),
            refresh=refresh_match.group(1) if refresh_match else "",
        )

    if _looks_like_refresh_token(stripped):
        return ParsedTokenInput(error=ERROR_REFRESH_TOKEN_ONLY)

    if _looks_like_jwt(stripped):
        return ParsedTokenInput(access=stripped)

    # An opaque access token is still usable until it expires; it just carries no
    # `exp` claim for us to read and no refresh grant.
    if _looks_like_opaque_token(stripped):
        return ParsedTokenInput(access=stripped)

    return ParsedTokenInput(error=ERROR_INVALID)


def _strip_wrappers(raw: str) -> str:
    """Drop whitespace, one pair of wrapping quotes, and a ``Bearer`` prefix."""
    text = raw.strip()
    # A paste from the DevTools JSON viewer often keeps the surrounding quotes.
    while len(text) >= 2 and text[0] in _QUOTES and text[-1] == text[0]:
        text = text[1:-1].strip()
    text = _BEARER_RE.sub("", text).strip()
    # Covers `"Bearer eyJ..."`: the quote comes off after the prefix, or before.
    while len(text) >= 2 and text[0] in _QUOTES and text[-1] == text[0]:
        text = text[1:-1].strip()
    return text


def _from_json(text: str) -> tuple[str, str]:
    """Return ``(access_token, refresh_token)`` from a JSON paste, or empty strings."""
    if not text:
        return ("", "")

    try:
        data = json.loads(text)
    except (ValueError, TypeError):
        return ("", "")

    # A JSON *string* whose own contents are the blob (double-encoded body).
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except (ValueError, TypeError):
            return ("", "")

    if not isinstance(data, dict):
        return ("", "")

    for container in _candidate_dicts(data):
        access = _first_str(container, _ACCESS_KEYS)
        if access:
            return (access, _first_str(container, _REFRESH_KEYS))
    return ("", "")


def _candidate_dicts(data: dict) -> list[dict]:
    """The pasted object plus any `body` it wraps, before or after decoding."""
    candidates = [data]
    body = data.get("body")
    if isinstance(body, dict):
        candidates.append(body)
    elif isinstance(body, str):
        try:
            decoded = json.loads(body)
        except (ValueError, TypeError):
            decoded = None
        if isinstance(decoded, dict):
            candidates.append(decoded)
    return candidates


def _first_str(data: dict, keys: tuple[str, ...]) -> str:
    for key in keys:
        value = data.get(key)
        if isinstance(value, str) and value:
            return value
    return ""


def _looks_like_refresh_token(text: str) -> bool:
    """True for the refresh-token shapes users paste thinking it is "the key"."""
    return text.startswith("v1.") or text.lower().startswith("otpauth://")


def _looks_like_jwt(text: str) -> bool:
    parts = text.split(".")
    if len(parts) != 3 or not all(parts):
        return False
    if not all(_JWT_SEGMENT_RE.match(part) for part in parts):
        return False
    # The header of a real JWT is a base64url JSON object, which always starts
    # with "ey" (the encoding of `{"`).
    return parts[0].startswith("ey") or _decodes_to_json_object(parts[0])


def _decodes_to_json_object(segment: str) -> bool:
    padded = segment + "=" * (-len(segment) % 4)
    try:
        decoded = base64.urlsafe_b64decode(padded)
        return isinstance(json.loads(decoded), dict)
    except (ValueError, TypeError, binascii.Error):
        return False


def _looks_like_opaque_token(text: str) -> bool:
    """A single long token-ish atom -- an access token without JWT structure."""
    if len(text) < _MIN_OPAQUE_LENGTH or any(char.isspace() for char in text):
        return False
    if any(char in text for char in '{}[]":'):
        return False
    return bool(_OPAQUE_RE.match(text))
