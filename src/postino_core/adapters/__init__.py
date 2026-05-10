"""External-tool adapters. One module per tool family.

Adapters wrap subprocess invocations behind a typed shape so the
service layer never assembles argv directly. Today: mlmmj. Tomorrow:
postfix's `postmap`, `postqueue` (when needed).
"""
