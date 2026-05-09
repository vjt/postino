# postino

CLI tool to administer a Postfix + Dovecot mail server with the
PostfixAdmin SQL schema as user/alias/domain backend.

Pluggable identity backend (local password column or external IdP via
Zitadel/SCIM). Built for FreeBSD mail hosts but portable.

## Status

Pre-implementation. Design spec: [`docs/superpowers/specs/2026-05-09-postino-design.md`](docs/superpowers/specs/2026-05-09-postino-design.md).

## Install

Not yet released. Once the MVP lands:

```sh
pipx install il-postino
```

Import name remains `postino`. The PyPI distribution is published as
`il-postino` because the bare `postino` name is squatted by an unrelated
2017 package.

## License

MIT — see [LICENSE](LICENSE).
