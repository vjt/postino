"""HmacVerifier — sign/verify roundtrip, tamper detection, constant-time."""

from __future__ import annotations

import hashlib
import hmac
import inspect

from postinod.auth.hmac_guard import HmacVerifier

SECRET = b"shared-secret-from-zitadel-target"


def _sign(body: bytes) -> str:
    return hmac.new(SECRET, body, hashlib.sha256).hexdigest()


def test_valid_signature_passes() -> None:
    body = b'{"event_type":"user.human.added"}'
    assert HmacVerifier(secret=SECRET).verify(body, _sign(body)) is True


def test_tampered_body_rejected() -> None:
    body = b'{"event_type":"user.human.added"}'
    sig = _sign(body)
    tampered = b'{"event_type":"user.human.removed"}'
    assert HmacVerifier(secret=SECRET).verify(tampered, sig) is False


def test_wrong_secret_rejected() -> None:
    body = b'{"x":1}'
    bad_sig = hmac.new(b"wrong-secret", body, hashlib.sha256).hexdigest()
    assert HmacVerifier(secret=SECRET).verify(body, bad_sig) is False


def test_empty_signature_rejected() -> None:
    body = b'{"x":1}'
    assert HmacVerifier(secret=SECRET).verify(body, "") is False


def test_default_header_name_is_zitadel_signature() -> None:
    assert HmacVerifier(secret=SECRET).header_name == "ZITADEL-Signature"


def test_custom_header_name() -> None:
    v = HmacVerifier(secret=SECRET, header_name="X-Foo-Sig")
    assert v.header_name == "X-Foo-Sig"


def test_constant_time_comparison_is_used() -> None:
    """HmacVerifier.verify must use hmac.compare_digest, not == on digests.

    A timing-side-channel attack on naive == compare can leak the
    expected digest one byte at a time. compare_digest masks this.
    """
    src = inspect.getsource(HmacVerifier.verify)
    assert "compare_digest" in src, "verify() must use hmac.compare_digest"
    # Reject naive '==' on the computed digest. (Approximate check; we
    # accept that comparing other things with == is fine.)
    assert "expected ==" not in src.replace(" ", "")
    assert "==expected" not in src.replace(" ", "")
