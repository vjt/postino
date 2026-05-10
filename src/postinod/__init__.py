"""postinod — IdP-driven mailbox provisioning daemon.

HTTP edge for Zitadel Actions v2 webhooks (HMAC-SHA256 signed) and
SCIM 2.0 (JWT bearer). Routes both surfaces into postino_core's
service layer with the NoAuthProvider; Dovecot does its own OIDC
auth for IMAP/POP/submission and is not in postinod's path.

Spec: docs/superpowers/specs/2026-05-10-postinod-design.md
"""

from __future__ import annotations

__version__ = "0.2.0"
