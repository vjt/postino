"""SCIM e2e: bring up Compose stack, generate JWT, run httpx-driven tests."""

from __future__ import annotations

import base64
import json
import subprocess
import time
from collections.abc import Generator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPrivateKey, generate_private_key

ROOT = Path(__file__).parent
COMPOSE_FILE = ROOT / "docker-compose.yml"
JWKS_DIR = ROOT / "jwks"
KID = "e2e-kid"


def _b64uint(i: int) -> str:
    b = i.to_bytes((i.bit_length() + 7) // 8, "big")
    return base64.urlsafe_b64encode(b).decode().rstrip("=")


@pytest.fixture(scope="session")
def keypair() -> Generator[RSAPrivateKey, None, None]:
    key = generate_private_key(public_exponent=65537, key_size=2048)
    pub = key.public_key().public_numbers()
    JWKS_DIR.mkdir(exist_ok=True)
    (JWKS_DIR / "jwks.json").write_text(
        json.dumps(
            {
                "keys": [
                    {
                        "kty": "RSA",
                        "kid": KID,
                        "use": "sig",
                        "alg": "RS256",
                        "n": _b64uint(pub.n),
                        "e": _b64uint(pub.e),
                    }
                ]
            }
        )
    )
    yield key
    (JWKS_DIR / "jwks.json").unlink(missing_ok=True)


@pytest.fixture(scope="session")
def stack(keypair: RSAPrivateKey) -> Generator[None, None, None]:
    subprocess.check_call(["docker", "compose", "-f", str(COMPOSE_FILE), "up", "-d", "--build"])
    # Wait for postinod /healthz
    deadline = time.time() + 120
    while time.time() < deadline:
        try:
            r = httpx.get("http://localhost:18443/healthz", timeout=2)
            if r.status_code == 200:
                break
        except httpx.HTTPError:
            time.sleep(1)
    else:
        subprocess.call(["docker", "compose", "-f", str(COMPOSE_FILE), "logs"])
        raise RuntimeError("postinod did not become healthy within 120s")
    yield
    subprocess.check_call(["docker", "compose", "-f", str(COMPOSE_FILE), "down", "-v"])


@pytest.fixture
def bearer_token(keypair: RSAPrivateKey) -> str:
    pem = keypair.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    return jwt.encode(
        {
            "iss": "http://jwks-stub",
            "aud": "postinod",
            "sub": "e2e",
            "exp": datetime.now(UTC) + timedelta(hours=1),
        },
        pem,
        algorithm="RS256",
        headers={"kid": KID},
    )
