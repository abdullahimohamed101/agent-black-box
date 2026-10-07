"""Key format, hashing and verification (pure; no database)."""

import hashlib
import hmac
from unittest import mock

import pytest

from abb_api.auth.keys import GeneratedKey, generate_key, hash_secret, parse_key, verify_secret


def test_generated_key_has_the_documented_shape() -> None:
    key = generate_key()
    assert key.token.startswith("abb_live_")
    assert (
        len(key.key_id) == 12 and key.key_id.isalnum() and key.key_id.islower()
    ) or key.key_id.isdigit()
    assert len(key.secret) == 43
    assert key.token == f"abb_live_{key.key_id}.{key.secret}"


def test_parse_round_trips_and_exposes_both_parts() -> None:
    key = generate_key()
    parsed = parse_key(key.token)
    assert parsed is not None
    assert (parsed.key_id, parsed.secret) == (key.key_id, key.secret)


def test_only_a_sha256_of_the_secret_is_kept() -> None:
    key = generate_key()
    assert key.secret_hash == hashlib.sha256(key.secret.encode()).digest()
    assert key.secret.encode() not in key.secret_hash
    assert hash_secret(key.secret) == key.secret_hash


def test_keys_are_unique_and_secrets_high_entropy() -> None:
    generated = [generate_key() for _ in range(500)]
    assert len({k.key_id for k in generated}) == 500
    assert len({k.secret for k in generated}) == 500


@pytest.mark.parametrize(
    "bad",
    [
        "",
        "abb_live_",
        "Bearer abb_live_aaaaaaaaaaaa." + "x" * 43,
        "abb_test_aaaaaaaaaaaa." + "x" * 43,
        "abb_live_AAAAAAAAAAAA." + "x" * 43,  # key ids are lowercase
        "abb_live_aaaaaaaaaaa." + "x" * 43,  # short id
        "abb_live_aaaaaaaaaaaaa." + "x" * 43,  # long id
        "abb_live_aaaaaaaaaaaa." + "x" * 42,
        "abb_live_aaaaaaaaaaaa." + "x" * 44,
        "abb_live_aaaaaaaaaaaa." + "x" * 42 + "+",  # not url-safe base64
        "abb_live_aaaaaaaaaaaa." + "x" * 43 + "\n",  # trailing newline must not slip through
        " abb_live_aaaaaaaaaaaa." + "x" * 43,
    ],
)
def test_malformed_tokens_do_not_parse(bad: str) -> None:
    assert parse_key(bad) is None


def test_verification_accepts_the_right_secret_only() -> None:
    key = generate_key()
    other = generate_key()
    assert verify_secret(key.secret, key.secret_hash)
    assert not verify_secret(other.secret, key.secret_hash)
    assert not verify_secret("", key.secret_hash)


def test_unknown_key_still_does_a_constant_time_comparison_and_fails() -> None:
    real = mock.Mock(wraps=hmac.compare_digest)
    with mock.patch.object(hmac, "compare_digest", real):
        assert not verify_secret(generate_key().secret, None)
    assert real.call_count == 1  # same work as a wrong-secret attempt


def test_the_dummy_hash_never_validates_a_real_secret() -> None:
    generated: GeneratedKey = generate_key()
    assert not verify_secret(generated.secret, None)
