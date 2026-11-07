# Security

## Threat model (abridged)

| Adversary | Vector | Primary controls |
| --- | --- | --- |
| anonymous attacker | credential stuffing, enumeration | rate limits, uniform errors, argon2id |
| authenticated user | access to other users' data | service-layer ownership checks, uniform 404s, per-topic WS auth |
| malicious upload | MIME spoofing, zip bombs, executables | content sniffing + extension cross-check, size caps, type allowlist |
| prompt injection via tool output / files | instructions smuggled into context | untrusted-data delimiters, provenance labelling, memory never auto-verified, approval gates |
| attacker via autonomous task | path traversal, command injection, SSRF, resource exhaustion | workspace jail, argv-only allowlisted execution, DNS/IP validation, rlimits + quotas + budgets |
| stolen refresh token | replay | rotation + reuse detection (revokes all sessions), hashed storage |
| database compromise | password theft | argon2id hashes only, no plaintext secrets |

## Controls in force

**Authentication & sessions**
Argon2id password hashing (library defaults, rehash-on-login upgrades);
short-lived HS256 JWT access tokens carrying `sub/sid/typ`; every request
re-validates the session row (instant logout/rotation/global revocation);
refresh tokens are 384-bit random, stored as SHA-256 digests, rotated on
every refresh; replaying a rotated token revokes ALL of the user's
sessions and writes a CRITICAL audit event; password reset tokens are
single-use, hashed, 1h TTL.

**Authorization**
Role model (`admin`, `user`); every resource lookup is
`user_id == principal.user_id` or 404 — cross-user probing cannot
distinguish "exists but foreign" from "does not exist". Admin endpoints
require the admin role. WebSocket subscriptions are ownership-checked per
topic.

**Input/output validation**
Pydantic v2 schemas on every endpoint; tool inputs validated against
per-tool models; tool outputs validated against per-tool output models;
request bodies size-capped (413); error envelopes scrubbed of secrets and
stack traces (verified by test).

**File security**
Filenames sanitised to basenames; storage keys are opaque UUIDs; MIME
detected from magic bytes (ZIP containers classified by internal
structure, robust to truncation) and cross-checked against the claimed
extension — mismatches rejected; size enforced while streaming (never
fully buffered); downloads stream with `X-Content-Type-Options: nosniff`;
`scan_status` + `quarantine_file()` provide the malware-scan integration
point (see below).

**Tool & sandbox security**
`python.execute` / `terminal.execute` run in a sandbox: jailed working
directory (symlink-aware), scrubbed environment (application secrets never
inherited), POSIX rlimits (address space, CPU, file size, process count),
no shell anywhere (argv lists only), process-group SIGKILL on timeout or
cancellation. `terminal.execute` additionally restricts binaries to the
deployment allowlist. `http.request` validates every URL: scheme
allowlist, no embedded credentials, DNS resolution with
loopback/private/link-local/reserved/multicast blocking (cloud metadata
included), manual redirect re-validation, response size caps.
Approvals: plans (client- or model-generated) and sensitive steps require
explicit user decisions before execution.

**Prompt-injection risk controls** (layered, honestly bounded)
Tool outputs enter the context wrapped in `<tool-output untrusted-data>`
blocks with a system instruction to treat them as data; memory is split
into VERIFIED facts vs UNVERIFIED model notes; model-generated statements
are never promoted to `verified` without explicit evidence; sensitive
actions still require human approval. **These are mitigations, not a
complete defense** — a capable model can still be manipulated by crafted
content; the hard boundaries (filesystem jail, network policy, allowlists,
approvals, quotas) are what limit the blast radius.

**Transport & headers**
CORS restricted to configured origins (no wildcards in production, no
credentials mode — tokens go in Authorization headers, which also removes
the classic CSRF cookie surface); `X-Content-Type-Options`, `X-Frame-Options`,
`Referrer-Policy`, CSP `default-src 'none'`, `Cache-Control: no-store`,
HSTS in production; correlation via validated `X-Request-ID`.

**Secrets**
Configuration via environment only; nothing hardcoded; logging pipeline
redacts secret-bearing keys (including nested); model API keys masked in
all API responses; upstream error bodies never propagated to clients.

**Audit trail** (append-only, queryable via `/admin/audit`)
`auth.register/login/login_failed/refresh/refresh_reuse_detected/
logout/password_reset_*/password_changed`, `user.updated`,
`conversation.*`, `file.uploaded/downloaded/deleted/quarantined`,
`task.created/transition/paused/resumed/cancelled`, `approval.decided`,
`tool.executed/denied`, `access.denied`, `memory.verified/deleted`.

## Malware scanning integration point

Set `AGP_FILE_MALWARE_SCAN_ENABLED=true` and wire your scanner into
`app/services/files.py` (the `scan_status` column and
`quarantine_file()` already exist). Until wired, uploads are marked
`scan_status=skipped` and **should be treated as untrusted content**
(downloads carry `nosniff`; extraction is text-only; images are never
executed).

## Known limitations (see also the engineering report)

* The local sandbox shares the host kernel (use `AGP_SANDBOX_BACKEND=docker`
  for stronger isolation of untrusted plans).
* Password-reset delivery is a structured-log integration point (no SMTP
  provider wired).
* Rate limiting is per-process unless Redis is configured.
* The WebSocket query-token pattern requires short token TTLs and
  URL-hygiene (treat links as sensitive).
