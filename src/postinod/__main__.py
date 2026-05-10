"""`python -m postinod` — uvicorn-driven daemon entrypoint."""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

import uvicorn

from postinod.app import build_app
from postinod.config import load_postinod_settings


def main(argv: list[str] | None = None) -> int:
    config_path = Path(os.environ.get("POSTINO_CONFIG", "/usr/local/etc/postino/postino.toml"))
    if not config_path.is_file():
        print(f"postinod: config not found at {config_path}", file=sys.stderr)
        return 2

    settings = load_postinod_settings(config_path)
    logging.basicConfig(level=settings.log_level)

    host, _, port_s = settings.listen.partition(":")
    if not port_s:
        print(f"postinod: invalid listen address {settings.listen!r}", file=sys.stderr)
        return 2

    app = build_app(toml_path=config_path)
    uvicorn.run(app, host=host, port=int(port_s), log_config=None)
    return 0


if __name__ == "__main__":
    sys.exit(main())
