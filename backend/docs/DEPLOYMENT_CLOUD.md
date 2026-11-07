# Cloud Deployment

The `docker-compose.yml` in the repository root is the reference topology:

```
            ┌────────────┐
   :8000 ──►│    api     │────┐
            └────────────┘    │
            ┌────────────┐    │      ┌──────────┐
            │   worker   │────┼─────►│ postgres │
            └────────────┘    │      └──────────┘
            ┌────────────┐    │      ┌──────────┐
   one-shot │  migrate   │────┘─────►│  redis   │ (events, rate limits)
            └────────────┘           └──────────┘
            ┌────────────┐   (optional profile: storage)
            │   minio    │  S3-compatible object storage
            └────────────┘
```

## Steps

```bash
git clone <repo> && cd agentic-platform-backend
cp .env.example .env
```

Minimum required values in `.env`:

```ini
AGP_SECRET_KEY=<python -c "import secrets; print(secrets.token_urlsafe(48))">
POSTGRES_PASSWORD=<strong password>
AGP_CORS_ORIGINS=["https://your-frontend.example.com"]
AGP_SALUKI_ENDPOINT=http://your-saluki-runtime:8000/v1   # when verified
```

Then:

```bash
docker compose up -d --build
docker compose ps                  # migrate exits 0, api/worker healthy
curl -s localhost:8000/api/v1/health/ready | jq
```

Scale workers: `AGP_WORKER_REPLICAS=3 docker compose up -d worker`.

## Hardening checklist

* **Terminat­e TLS at a reverse proxy** (nginx/traefik/ALB) and forward to
  the api service. Enable HSTS at the proxy (the app also sets it when
  `AGP_ENVIRONMENT=production`).
* **Never expose** postgres/redis/minio ports publicly — the compose file
  deliberately publishes only the API port.
* Restrict `AGP_CORS_ORIGINS` to the real frontend origin.
* Provide object storage: either the `storage` compose profile (MinIO) or
  a managed bucket (`AGP_STORAGE_BACKEND=s3` + endpoint/keys). Mount
  buckets with least-privilege keys.
* Set `AGP_TRUST_PROXY_HEADERS=true` only behind a trusted proxy so
  rate-limit identities use the real client IP.
* Ship `AGP_LOG_JSON=true` output to your log platform; every line carries
  `request_id`/`user_id` correlation fields.
* Enable optional OpenTelemetry tracing
  (`pip install -r requirements-cloud.txt` inside the image, then
  `AGP_OTEL_ENABLED=true` + `AGP_OTEL_ENDPOINT`).

## Isolated tool execution in the cloud

Two supported sandbox backends (`AGP_SANDBOX_BACKEND`):

* `local` (default) — hardened local subprocesses: jailed workspace,
  scrubbed environment (no app secrets), POSIX rlimits, process-group
  kill. Fine for single-tenant trusted deployments.
* `docker` — each tool runs in a container: `--network none`,
  `--cap-drop ALL`, `no-new-privileges`, memory/CPU caps, non-root user,
  per-task volume, `timeout -k`. Requires the Docker CLI on the worker and
  appropriate socket access. **Recommended for multi-tenant or
  untrusted-plan deployments.**

The sandbox driver is chosen per deployment; tools and the executor are
unchanged.

## Kubernetes sketch

The images are plain processes — run them as a Deployment (`api`, 2+
replicas behind a Service/Ingress) and a separate Deployment (`worker`),
both pointing at the same `AGP_DATABASE_URL` (managed Postgres) and
`AGP_REDIS_URL`. Run `alembic upgrade head` as an initContainer or a
Kubernetes Job before rolling the API. Liveness/readiness probes map to
`/api/v1/health/live` and `/api/v1/health/ready`. Resource hints: api
512Mi–1Gi, worker 1–2Gi (sandbox memory limits are separate).

## Upgrade / rollback procedure

```bash
# upgrade
git pull
docker compose build
docker compose run --rm migrate        # alembic upgrade head
docker compose up -d api worker        # rolling restart

# rollback application
git checkout <previous-tag>
docker compose build && docker compose up -d api worker

# rollback database (only if the new migration must be undone):
docker compose run --rm worker alembic downgrade -1
```

Take a database snapshot (`pg_dump`) before every upgrade — see
docs/DATABASE.md.
