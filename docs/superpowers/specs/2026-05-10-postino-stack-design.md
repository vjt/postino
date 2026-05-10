# postino-stack — infra design spec

**Status:** draft, brainstormed 2026-05-10
**Topic:** parallel classic mail stack on athena, evaluated alongside the existing stalwart deployment
**Scope:** infrastructure bring-up only — containers, networks, volumes, certbot relocation, base configs. **No migration, no decommission, no postinod install in this spec.**

Sister spec: [`2026-05-10-postinod-design.md`](2026-05-10-postinod-design.md) — the IdP-driven provisioning daemon that will eventually run inside this stack. postinod install is out of scope here.

---

## 1. Purpose

Stand up a parallel classic-Unix mail stack (postfix + dovecot + mariadb + rspamd + mlmmj + public-inbox + snappymail) on athena, in containers, isolated from the running stalwart deployment. The stack will host a future evaluation phase comparing classic vs stalwart on operational simplicity, Zitadel integration depth, migration cost, and feature/perf — but **this spec covers the bring-up only**, not the evaluation, the migration, or the cutover decision.

stalwart is **not** decommissioned. Legacy mail flow is **not** modified. No mailboxes, aliases, or list memberships are migrated. The deliverable of this spec is an empty, healthy, evaluable stack.

## 2. Goals and non-goals

**Goals:**
- New compose stack at `/opt/postino/` on athena, running independently of `/opt/stalwart/`.
- Single shared cert source at `/opt/certbot/` consumed read-only by both stacks.
- Cutover script for stalwart relocated out of `/opt/stalwart/` to a neutral host path, decoupled from cert issuance.
- All new containers green on health checks, smoke-tested for SMTP/IMAP/HTTP basics with synthetic data.
- Zero changes to legacy postfix, dovecot, mailman, MySQL, or DNS.
- Zero changes to running stalwart compose, beyond a future cert-path edit (executed only when the cert dir relocation lands).

**Non-goals:**
- Provisioning real users, aliases, or domains into the new stack.
- Wiring shadow-BCC tee from legacy to the new postfix.
- Importing pipermail mboxes into public-inbox.
- Porting sieves from stalwart.
- Installing postinod (Zitadel/SCIM provisioning daemon) — its own spec, separate session.
- Issuing the LE cert (gated on rate-limit reset 2026-05-11 18:42 CEST).
- Decommissioning stalwart, legacy, solr, or any other production component.

## 3. Architecture

```
                  athena (Debian, 7.8G RAM, 4 cores, 58G free)
  ┌──────────────────────────────────────────────────────────────┐
  │  EXISTING (untouched by this spec)                           │
  │   stalwart-mail        :25 :443 :465 :993 :4190              │
  │   stalwart-postgres / grafana / prometheus / diun / node-exp │
  │   path: /opt/stalwart/                                       │
  └──────────────────────────────────────────────────────────────┘
  ┌──────────────────────────────────────────────────────────────┐
  │  NEW: /opt/certbot/docker-compose.yml                        │
  │   certbot              (host network, port 80 standalone)    │
  │   data: /opt/certbot/data/letsencrypt/  (single source)      │
  └──────────────────────────────────────────────────────────────┘
  ┌──────────────────────────────────────────────────────────────┐
  │  NEW: /opt/postino/docker-compose.yml                        │
  │  network: postino_net (bridge, isolated)                     │
  │                                                              │
  │   web (nginx + s6 + php-fpm + public-inbox-httpd) :8443      │
  │     ├── /webmail   → snappymail (PHP-FPM, internal socket)  │
  │     ├── /archives  → public-inbox-httpd (internal port)     │
  │     └── /admin     → reserved for future postinod (404 now) │
  │                                                              │
  │   mta (postfix + mlmmj, s6-supervised)  :2525 :4651 :5587   │
  │   dovecot                               :1143 :9931 :14190  │
  │   rspamd                                (internal 11334)    │
  │   redis                                 (internal 6379)     │
  │   mariadb                               (internal 3306)     │
  │   clamav                                (profile=clamav)    │
  │                                                              │
  │  state: /srv/postino/data/                                  │
  │    maildirs/  mysql/  sieves/  lists/  archives/  redis/   │
  │    rspamd/   logs/                                          │
  │  config: /srv/postino/config/                               │
  │    postfix/  dovecot/  rspamd/  mlmmj/  public-inbox/      │
  │    nginx/    snappymail/                                    │
  └──────────────────────────────────────────────────────────────┘
  ┌──────────────────────────────────────────────────────────────┐
  │  NEW: /opt/cutover/  (relocated from /opt/stalwart/staged/) │
  │   deploy-cutover.sh — stalwart compose-swap (cert-free)     │
  │   staged/{docker-compose.yml.new, stalwart-config.toml.new, │
  │           nginx/nginx.conf}                                 │
  └──────────────────────────────────────────────────────────────┘
```

**Invariants:**
- stalwart and postino compose stacks share **no docker volumes and no docker network**. The only crossing is the host-path bind mount of `/opt/certbot/data/letsencrypt/` (read-only).
- All published host ports for postino are non-standard, so stalwart keeps real-port traffic.
- All new state lives under `/srv/postino/data/` (a single directory tree, easy to back up or wipe).
- Image sources prefer official upstream (`mariadb`, `dovecot/dovecot`, `rspamd/rspamd`, `redis`, `clamav/clamav`, `nginx`, `certbot/certbot`). Custom Dockerfiles only for `mta` (postfix+mlmmj+s6) and `web` (nginx+php-fpm+public-inbox+s6).

## 4. Components

### 4.1 Container inventory

| Container | Base image | Bundles | Estimated RAM | Host ports |
|---|---|---|---|---|
| `postino-mta` | `debian:bookworm-slim` + s6-overlay (custom Dockerfile) | postfix, postfix-mysql, mlmmj | ~80 MB | 2525, 4651, 5587 |
| `postino-dovecot` | `dovecot/dovecot:2.3` | dovecot core, mysql passdb plugin, fts-flatcurve plugin | ~80 MB | 1143, 9931, 14190 |
| `postino-mariadb` | `mariadb:11` | mariadb | ~200 MB | (internal only) |
| `postino-rspamd` | `rspamd/rspamd:stable` | rspamd | ~250 MB | (internal only) |
| `postino-redis` | `redis:7-alpine` | redis with AOF persistence | ~30 MB | (internal only) |
| `postino-web` | `debian:bookworm-slim` + s6-overlay (custom Dockerfile) | nginx, php-fpm, snappymail, public-inbox-httpd, public-inbox tools | ~150 MB | 8443 |
| `postino-clamav` | `clamav/clamav:stable` | clamd, freshclam | ~800 MB | (internal only) — **profile `clamav`, off by default** |
| `certbot` | `certbot/certbot:latest` | certbot | ~30 MB | port 80 host-net (only during issuance/renewal) |

**Total estimated steady-state RAM (clamav off): ~820 MB.** With stalwart at 1.6 GB and grafana stack ~400 MB, the box has ~5 GB headroom. clamav can be flipped on for full-fidelity tests with comfortable margin.

### 4.2 Image-build policy

- `postino-agent` (postinod) is **out of scope for this spec** — see [`2026-05-10-postinod-design.md`](2026-05-10-postinod-design.md). When that spec lands, the agreed install policy is: install from PyPI (`pip install il-postino==<pinned>`). No source bind-mount, no editable install. Pre-release iteration via local-built wheel + `twine upload`. One install path, one source of truth. Recorded here only so the future Dockerfile is unambiguous; nothing in this spec installs the agent.
- `mta` and `web` — Dockerfiles checked into `/srv/postino/docker/{mta,web}/Dockerfile`. Built on athena via `docker compose build`. No registry push for now; rebuild from source as needed.

### 4.3 Full-text search

dovecot uses **fts-flatcurve** (Lucene in-process, Open-Xchange official plugin, in dovecot 2.3.20+). Index files live alongside maildir under `/srv/postino/data/maildirs/<dom>/<user>/.fts.*`. No solr, no JVM, no extra container.

The legacy custom-compiled dovecot + custom solr schema is **not** replicated. Modern dovecot ships fts-solr in-tree if we ever need it, but flatcurve is the chosen path for this stack.

**Image source caveat:** the upstream `dovecot/dovecot` image does not always include flatcurve in its default build. If the pinned image tag lacks the plugin, fall back to a custom Dockerfile (`debian:bookworm-slim` + dovecot from Debian backports + `dovecot-fts-flatcurve` package, or compile from source with `--with-stemmer --with-icu`). Decision lands at first build attempt — not a blocker, just a build-time branch.

### 4.4 Anti-spam / classifier

rspamd is the **only** classifier — SpamAssassin is dropped from the legacy carryover. Modern rspamd config baseline:

- Bayes (redis backend)
- Fuzzy hashes (redis backend)
- DKIM signing (outbound)
- ARC signing (outbound)
- Greylisting (redis backend, off by default during eval to avoid noise)
- Ratelimit module (redis backend)
- RBL: Spamhaus ZEN, SURBL multi
- Neural module (off by default; opt-in once corpus exists)
- ClamAV antivirus (only when `COMPOSE_PROFILES=clamav` set)

Strengthening over legacy is principally the addition of ARC signing and modern DKIM-via-rspamd (replacing opendkim), plus the choice of redis-backed bayes/fuzzy with persistence.

### 4.5 Mailing lists

mlmmj as a binary inside the `mta` container, invoked via postfix transport pipe. Per-list spool under `/srv/postino/data/lists/<listname>/`. No daemon, no HTTP UI — administered via CLI / file edits, with a future postino subcommand wrapping the common ops.

public-inbox provides the web archive UI: one git inbox per list under `/srv/postino/data/archives/<listname>.git/`, served by `public-inbox-httpd` inside the `web` container. Atom feeds and full-text search included. Pipermail mbox import is **out of scope** for this spec; designed for, not yet executed.

### 4.6 Webmail

SnappyMail, no OIDC, plain IMAP login via internal `dovecot:143` STARTTLS. Served at `https://<host>:8443/webmail/` through nginx in the `web` container.

## 5. Network and ports

```
postino_net (bridge, isolated, no overlap with stalwart's network)

published to host:
  2525   postfix smtp
  4651   postfix smtps (implicit TLS)
  5587   postfix submission (STARTTLS)
  1143   dovecot imap (STARTTLS)
  9931   dovecot imaps (implicit TLS)
  14190  dovecot managesieve (implicit TLS)
  8443   web nginx (TLS)

internal-only:
  postino-mariadb:3306
  postino-rspamd:11334
  postino-redis:6379
  postino-clamav:3310
  postino-mta milter port to rspamd
  postino-web → snappymail php-fpm socket
  postino-web → public-inbox-httpd port

inter-container traffic: plaintext on the bridge.
```

stalwart's published ports (25/443/465/993/4190) remain untouched. Postino's ports are deliberately non-overlapping so a port collision is impossible.

## 6. TLS / certificates

**Single source of truth:** `/opt/certbot/data/letsencrypt/`. Owned by `/opt/certbot/docker-compose.yml`'s certbot container. Both stacks consume read-only.

**External surface gets TLS, internal surface does not.**

External daemons that need certs:
- `postino-web` nginx (8443) — for webmail/archives/future admin UI
- `postino-mta` postfix (4651 SMTPS implicit, 5587 STARTTLS)
- `postino-dovecot` (9931 IMAPS implicit, 14190 managesieve implicit)

Internal traffic on `postino_net` (postfix↔rspamd milter, postfix↔dovecot LMTP, web↔snappymail, web↔public-inbox-httpd, all daemons↔mariadb/redis) is plaintext. Docker bridge is the trust boundary.

**Certbot deploy hook** at `/opt/certbot/data/letsencrypt/renewal-hooks/deploy/reload-services.sh`:

```sh
#!/bin/sh
# stalwart side — guards against stack absence
docker exec stalwart-mail   sh -c 'kill -HUP 1' 2>/dev/null || true
docker exec stalwart-nginx  nginx -s reload     2>/dev/null || true
# postino side
docker exec postino-mta     postfix reload      2>/dev/null || true
docker exec postino-dovecot doveadm reload      2>/dev/null || true
docker exec postino-web     nginx -s reload     2>/dev/null || true
# notify
/opt/certbot/scripts/telegram-notify "cert renewed, reloaded both stacks"
```

Runs from inside the certbot container at the end of a successful renewal. `|| true` on every line so a stopped container does not fail the hook for the others. Container-level `docker exec` requires `/var/run/docker.sock` mounted into certbot — accepted first-step trade-off (root-equivalent on host); future hardening path documented as an open item (host-level systemd path-watch).

**Migration of existing LE account:**
```sh
sudo mkdir -p /opt/certbot/data
sudo mv /opt/stalwart/data/letsencrypt /opt/certbot/data/letsencrypt
sudo ln -s /opt/certbot/data/letsencrypt /opt/stalwart/data/letsencrypt
```
Symlink keeps any code that still references the old path working, until the staged stalwart cutover script is updated to read from `/opt/certbot/...` directly.

**Issuance is out of scope for this spec.** Real issuance is gated on the LE rate-limit reset (2026-05-11 18:42 CEST after today's failed attempt). When it lands, run `/opt/certbot/scripts/issue.sh` — staging dry-run first, production second.

**Self-signed bootstrap (pre-issuance):** `postino-web`, `postino-mta`, and `postino-dovecot` each have a writable per-container TLS path at `/srv/postino/data/tls/<service>/`, bind-mounted read-write. At container start, an entrypoint script:

1. If `/etc/letsencrypt/live/athena.olografix.org/fullchain.pem` exists (the read-only mount from `/opt/certbot/...`), copy/symlink it into `/srv/postino/data/tls/<service>/{fullchain.pem,privkey.pem}`.
2. Else generate self-signed via `openssl req -x509 -newkey rsa:2048 -nodes -days 30 -subj "/CN=athena.olografix.org"` and write to the same path.

The daemon (postfix/dovecot/nginx) configuration always points at `/srv/postino/data/tls/<service>/...`, never at `/etc/letsencrypt/...` directly. The deploy hook re-runs the entrypoint copy step + reloads. This keeps the LE volume read-only and avoids per-daemon special-casing for the missing-cert path.

## 7. Cutover script relocation

`/opt/stalwart/staged/deploy-cutover.sh` and adjacent staged files are moved out of stalwart's tree to `/opt/cutover/`. The script is renamed and refactored:

- New unit name: `cutover-stalwart-compose.service` / `.timer` (was `cutover-stalwart-cert.*`). The cert step is removed; the script now does only the stalwart compose + config swap.
- `certbot --standalone` step **removed**. Cert issuance is independent and runs from `/opt/certbot/`.
- Precondition added: `test -f /opt/certbot/data/letsencrypt/live/athena.olografix.org/fullchain.pem` — exit early if no cert.
- Compose `docker-compose.yml.new` updated to bind-mount `/opt/certbot/data/letsencrypt:ro` instead of `./data/letsencrypt:ro`.

The previous failed transient timer (`cutover-stalwart-cert.*`) is cancelled. A new timer is **not** armed in this spec — re-arming the cutover is a separate decision that depends on LE rate-limit window opening.

Source-of-truth on dev box (`/srv/olografix/scripts/deploy-cutover.sh`) is updated in lockstep. Deployed copy on athena lives at `/opt/cutover/deploy-cutover.sh`.

## 8. State and configuration layout

```
/srv/postino/                                   bind-mount roots, on host
  docker-compose.yml                            single compose file
  .env                                          mariadb root pw, rspamd controller pw, etc — mode 600
  docker/
    mta/Dockerfile                              postfix + mlmmj + s6
    web/Dockerfile                              nginx + php-fpm + snappymail + public-inbox + s6
  config/
    postfix/{main.cf,master.cf,mysql-virtual_*.cf,transport,...}
    dovecot/{dovecot.conf,conf.d/*.conf,dovecot-sql.conf.ext}
    rspamd/{local.d/*,override.d/*}
    mlmmj/                                      list-template defaults
    public-inbox/config
    nginx/{nginx.conf,sites/*}
    snappymail/include.php
  data/
    maildirs/<dom>/<user>/                      vmail uid/gid
    mysql/                                      mariadb datadir
    redis/                                      AOF + RDB
    rspamd/                                     dkim keys, neural snapshots
    sieves/<user>.sieve
    lists/<listname>/                           mlmmj per-list state
    archives/<listname>.git/                    public-inbox repos
    logs/                                       per-container log dirs
  scripts/
    smoke-test.sh                               post-up validator
```

Configs are bind-mounted read-only into containers. State is bind-mounted read-write. Compose-down does not lose state.

## 9. Smoke tests (acceptance criteria for this spec)

The deliverable is "stack is up, healthy, and minimally exercisable." Acceptance script `/srv/postino/scripts/smoke-test.sh` runs the following and exits 0 on full pass.

**Fixture (setup before checks, teardown after):**
- One synthetic domain `stack.local` inserted into mariadb postfixadmin schema (NOT a real domain — never exits the host).
- One synthetic mailbox `smoke@stack.local` with a known password (BLF-CRYPT hash).
- One synthetic mailing list `smoketest@stack.local` with one subscriber (`smoke@stack.local`).
- All three are deleted at teardown so the stack returns to empty state.

**Checks:**

1. **mariadb** — connect with admin user, list databases, expect empty postfixadmin schema present + the fixture rows from setup.
2. **mta** — `swaks --to smoke@stack.local --server localhost:2525 --ehlo eval-test.local` returns 250.
3. **dovecot** — IMAPS login as `smoke@stack.local` to `imaps://localhost:9931/` (via `imap-cli`, modern `curl`, or equivalent client) succeeds and lists the message delivered in #2. `doveadm fts rescan -u smoke@stack.local` succeeds (flatcurve sanity).
4. **rspamd** — `docker exec postino-rspamd rspamc status` returns OK; the message from #2 has an rspamd `X-Spam-*` header set.
5. **redis** — `docker exec postino-redis redis-cli PING` returns PONG.
6. **mlmmj** — post one message to `smoketest@stack.local`, observe (a) delivery to `smoke@stack.local`'s maildir, (b) one new commit in `/srv/postino/data/archives/smoketest.git/`.
7. **web/snappymail** — `curl -k https://localhost:8443/webmail/` returns 200; loginable as `smoke@stack.local` (interactive check, scripted via simple HTTP POST).
8. **web/public-inbox** — `curl -k https://localhost:8443/archives/` returns 200, lists `smoketest`.
9. **certbot** — container running, `certbot certificates` lists either zero certs (pre-issuance) or the eventual `athena.olografix.org` entry.
10. **stalwart unaffected** — port 25/443/465/993/4190 still answer, `docker exec stalwart-mail stalwart-cli server status` OK.

Pass = all 10 green. Failure of any single check fails the spec acceptance.

## 10. Risks and mitigations

| Risk | Likelihood | Mitigation |
|---|---|---|
| Cert dir relocation (`/opt/stalwart/data/letsencrypt` → `/opt/certbot/data/letsencrypt`) breaks staged stalwart cutover | high if uncoordinated | Relocate first, symlink for back-compat, update staged compose path in same edit, re-arm timer only after smoke-test of stalwart bring-up against new path. **In this spec we do the relocation but do not re-arm the timer.** |
| RAM pressure on 7.8 G box | medium | clamav profile-gated, monitored via existing prometheus/grafana, rspamd/mariadb sized in compose limits |
| Existing legacy/stalwart traffic hits `:80` during certbot standalone runs | low | certbot binds 80 only during issuance/renewal windows. Today nothing on athena binds 80 (verified). Future: if anything binds 80, switch to `--webroot` via web container. |
| docker.sock exposed to certbot container = host-root | accepted as first-step trade-off | Documented; future hardening = host systemd path-watch firing reload script outside any container |
| s6-overlay multi-process container failure mode opacity | low | s6 emits structured logs to `/var/log/s6/`; bind-mount to `/srv/postino/data/logs/web/` and `/srv/postino/data/logs/mta/` for inspection |
| Self-signed cert at first boot annoys clients | low | Only matters during eval; switches to LE cert automatically once `/opt/certbot/...` populates and reload-hook fires |
| mariadb root password leak via `.env` if mode wrong | low | `.env` set to mode 600 by bring-up script; check in smoke-test |
| Image rebuild reproducibility drift | medium | Pin all base image tags by digest after first successful build; commit Dockerfiles |

## 11. Out-of-scope / deferred items

These are intentionally not addressed by this spec, in order to keep the bring-up surgical:

- **postinod install** — separate spec, separate session.
- **User/alias/domain provisioning** — neither manual seed nor migration from legacy. Stack stays empty post-bring-up except for the smoke-test fixture (which is torn down).
- **Shadow-BCC tee from legacy postfix to athena:2525** — happens later, when the eval phase formally starts.
- **Sieve port from stalwart** — later.
- **Mailman2 → mlmmj membership migration** — later.
- **Pipermail mbox → public-inbox import** — later.
- **Eval FQDN registration (e.g. `mail2.olografix.org`)** — defer until cutover decision moment; until then, eval users tolerate self-signed or use `athena.olografix.org` cert via SNI on 8443.
- **Solr decommission on legacy** — never planned in this spec; legacy stays as-is.
- **Stalwart cutover re-arming** — depends on LE rate-limit window (2026-05-11 18:42 CEST) and is independent of this spec's deliverable.
- **Decision on stalwart vs postino as the production stack** — explicitly deferred until evaluation runs.

## 12. Implementation order (preview, full plan in writing-plans)

Rough sequencing for a follow-up implementation plan:

1. Create `/srv/postino/` skeleton + Dockerfiles + base configs.
2. Create `/opt/certbot/` compose, migrate LE account dir from `/opt/stalwart/data/letsencrypt`, install symlink.
3. Move staged cutover artifacts from `/opt/stalwart/staged/` to `/opt/cutover/`, refactor script, cancel old transient timer.
4. Bring up postino-mariadb, init schema, smoke-test #1.
5. Bring up postino-redis, postino-rspamd, smoke-test #4 + #5.
6. Bring up postino-mta + postino-dovecot, smoke-test #2 + #3.
7. Bring up postino-web (nginx + snappymail + public-inbox), smoke-test #7 + #8.
8. Bring up mlmmj test list, smoke-test #6.
9. Run full smoke-test #1-#10. Spec accepted on green.

---
