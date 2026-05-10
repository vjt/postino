"""HmacVerifier — sign/verify roundtrip, tamper detection, constant-time, rotation."""

from __future__ import annotations

import hashlib
import hmac
import inspect
import re

import pytest

from postinod.auth.hmac_guard import HmacVerifier

SECRET = b"shared-secret-from-zitadel-target"
SECRET_OLD = b"the-previous-rotation-secret-foo"
SECRET_NEW = b"the-newly-issued-rotation-secret"


def _sign(secret: bytes, body: bytes) -> str:
    return hmac.new(secret, body, hashlib.sha256).hexdigest()


def test_valid_signature_passes() -> None:
    body = b'{"event_type":"user.human.added"}'
    assert HmacVerifier(secrets=(SECRET,)).verify(body, _sign(SECRET, body)) is True


def test_tampered_body_rejected() -> None:
    body = b'{"event_type":"user.human.added"}'
    sig = _sign(SECRET, body)
    tampered = b'{"event_type":"user.human.removed"}'
    assert HmacVerifier(secrets=(SECRET,)).verify(tampered, sig) is False


def test_wrong_secret_rejected() -> None:
    body = b'{"x":1}'
    bad_sig = hmac.new(b"wrong-secret", body, hashlib.sha256).hexdigest()
    assert HmacVerifier(secrets=(SECRET,)).verify(body, bad_sig) is False


def test_empty_signature_rejected() -> None:
    body = b'{"x":1}'
    assert HmacVerifier(secrets=(SECRET,)).verify(body, "") is False


def test_default_header_name_is_zitadel_signature() -> None:
    assert HmacVerifier(secrets=(SECRET,)).header_name == "ZITADEL-Signature"


def test_custom_header_name() -> None:
    v = HmacVerifier(secrets=(SECRET,), header_name="X-Foo-Sig")
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
    assert not re.search(r"\bexpected\b\s*==", src), "verify() must not use == on the digest"
    assert not re.search(r"==\s*\bexpected\b", src), "verify() must not use == on the digest"


def test_repr_does_not_leak_secret() -> None:
    v = HmacVerifier(secrets=(b"super-secret-value",))
    assert "super-secret" not in repr(v)
    assert "****" in repr(v)


def test_rotation_accepts_either_secret() -> None:
    body = b'{"x":1}'
    v = HmacVerifier(secrets=(SECRET_OLD, SECRET_NEW))
    assert v.verify(body, _sign(SECRET_OLD, body)) is True
    assert v.verify(body, _sign(SECRET_NEW, body)) is True


def test_rotation_rejects_unconfigured_secret() -> None:
    body = b'{"x":1}'
    v = HmacVerifier(secrets=(SECRET_OLD, SECRET_NEW))
    rogue = _sign(b"some-other-secret-entirely-foo", body)
    assert v.verify(body, rogue) is False


def test_empty_secrets_tuple_rejected() -> None:
    with pytest.raises(ValueError):
        HmacVerifier(secrets=())
