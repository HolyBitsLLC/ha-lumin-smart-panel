"""Tests for Lumin token-data parsing in ``custom_components.lumin.token_input``.

The module is imported by file path, like ``test_token_manager`` does for
``api.py``, so the CI unit-test job (pytest + aiohttp only) does not need Home
Assistant installed.
"""

from __future__ import annotations

import base64
import importlib.util
import json
import pathlib
import sys

import pytest

_ROOT = pathlib.Path(__file__).resolve().parents[1]
_SPEC = importlib.util.spec_from_file_location(
    "lumin_token_input", _ROOT / "custom_components" / "lumin" / "token_input.py"
)
assert _SPEC is not None and _SPEC.loader is not None
token_input = importlib.util.module_from_spec(_SPEC)
# `dataclasses` resolves the defining module through sys.modules, so the module
# has to be registered before it is executed (the file-path import does not).
sys.modules[_SPEC.name] = token_input
_SPEC.loader.exec_module(token_input)


def _b64(obj: dict) -> str:
    raw = json.dumps(obj).encode()
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


ACCESS = f"{_b64({'alg': 'RS256', 'typ': 'JWT'})}.{_b64({'exp': 1700000000})}.c2ln"
REFRESH = "v1.MRq8DummyRotatingRefreshTokenValue"
OPAQUE = "opaqueAccessTokenValue0123456789"
BLOB = json.dumps({"body": {"access_token": ACCESS, "refresh_token": REFRESH}})


def test_full_local_storage_blob_keeps_both_tokens() -> None:
    parsed = token_input.parse_token_input(BLOB)

    assert parsed.error is None
    assert (parsed.access, parsed.refresh) == (ACCESS, REFRESH)
    assert parsed.refreshable is True


def test_inner_body_object_is_accepted() -> None:
    parsed = token_input.parse_token_input(
        json.dumps({"access_token": ACCESS, "refresh_token": REFRESH})
    )

    assert (parsed.access, parsed.refresh) == (ACCESS, REFRESH)


def test_double_encoded_body_string_is_accepted() -> None:
    """The Auth0 SPA SDK stores `body` as a JSON-encoded string."""
    blob = json.dumps(
        {"body": json.dumps({"access_token": ACCESS, "refresh_token": REFRESH})}
    )

    parsed = token_input.parse_token_input(blob)

    assert (parsed.access, parsed.refresh) == (ACCESS, REFRESH)


def test_camel_case_keys_are_accepted() -> None:
    parsed = token_input.parse_token_input(
        json.dumps({"accessToken": ACCESS, "refreshToken": REFRESH})
    )

    assert (parsed.access, parsed.refresh) == (ACCESS, REFRESH)


def test_bare_jwt_is_accepted_without_a_refresh_token() -> None:
    parsed = token_input.parse_token_input(ACCESS)

    assert parsed.error is None
    assert parsed.access == ACCESS
    assert parsed.refresh == ""
    assert parsed.refreshable is False


@pytest.mark.parametrize(
    "raw",
    [
        ACCESS + "\n",
        "\n" + ACCESS,
        "  \n\t" + ACCESS + "\t\r\n  ",
        f'"{ACCESS}"',
        f"'{ACCESS}'",
        f"Bearer {ACCESS}",
        f"bearer {ACCESS}",
        f'"Bearer {ACCESS}"',
    ],
    ids=[
        "trailing-newline",
        "leading-newline",
        "surrounding-whitespace",
        "double-quoted",
        "single-quoted",
        "bearer-prefixed",
        "lowercase-bearer",
        "quoted-bearer",
    ],
)
def test_bare_jwt_survives_paste_noise(raw: str) -> None:
    parsed = token_input.parse_token_input(raw)

    assert parsed.error is None, raw
    assert parsed.access == ACCESS


def test_refresh_token_alone_is_reported_specifically() -> None:
    parsed = token_input.parse_token_input(REFRESH)

    assert parsed.access == ""
    assert parsed.error == token_input.ERROR_REFRESH_TOKEN_ONLY


def test_quoted_refresh_token_alone_is_reported_specifically() -> None:
    parsed = token_input.parse_token_input(f'"{REFRESH}"')

    assert parsed.error == token_input.ERROR_REFRESH_TOKEN_ONLY


def test_otpauth_uri_is_reported_as_a_refresh_token() -> None:
    parsed = token_input.parse_token_input("otpauth://totp/Lumin:user?secret=ABC")

    assert parsed.error == token_input.ERROR_REFRESH_TOKEN_ONLY


def test_truncated_json_recovers_both_tokens() -> None:
    """A large Local Storage value is often cut off mid-copy."""
    truncated = (
        '{"body":{"access_token":"'
        + ACCESS
        + '","refresh_token":"'
        + REFRESH
        + '","scope":"openid profile'
    )

    parsed = token_input.parse_token_input(truncated)

    assert (parsed.access, parsed.refresh) == (ACCESS, REFRESH)


def test_truncated_json_without_refresh_still_recovers_the_access_token() -> None:
    truncated = '{"body":{"access_token":"' + ACCESS + '","scope":"openid'

    parsed = token_input.parse_token_input(truncated)

    assert parsed.access == ACCESS
    assert parsed.refresh == ""


def test_opaque_access_token_is_accepted_as_access_only() -> None:
    parsed = token_input.parse_token_input(OPAQUE)

    assert parsed.error is None
    assert parsed.access == OPAQUE
    assert parsed.refreshable is False


@pytest.mark.parametrize("raw", ["", "   ", "\n\t", None, 12345])
def test_unusable_input_is_invalid(raw: object) -> None:
    parsed = token_input.parse_token_input(raw)

    assert parsed.error == token_input.ERROR_INVALID


def test_prose_is_not_mistaken_for_a_token() -> None:
    parsed = token_input.parse_token_input("I pasted the key here")

    assert parsed.error == token_input.ERROR_INVALID


def test_error_keys_are_the_documented_flow_keys() -> None:
    """These strings are used verbatim as ``errors["base"]`` keys."""
    assert token_input.ERROR_INVALID == "invalid_token_format"
    assert token_input.ERROR_REFRESH_TOKEN_ONLY == "refresh_token_only"


def test_error_keys_exist_in_both_translation_files() -> None:
    """Home Assistant requires strings.json and translations/en.json to agree."""
    root = _ROOT / "custom_components" / "lumin"
    strings = json.loads((root / "strings.json").read_text())
    english = json.loads((root / "translations" / "en.json").read_text())

    assert strings == english
    errors = strings["config"]["error"]
    assert token_input.ERROR_INVALID in errors
    assert token_input.ERROR_REFRESH_TOKEN_ONLY in errors
    assert "access_token_only" in strings["config"]["step"]
