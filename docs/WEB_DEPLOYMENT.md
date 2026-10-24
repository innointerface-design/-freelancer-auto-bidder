# Web application: configuration and deployment

The FastAPI dashboard extends the existing Python engine. The original CLI, job scoring, official API integration, durable duplicate ledger and tests remain available. The supplied hosting files are deployment templates; they do not represent a deployed or tested cloud instance.

## Local launch

Use Python 3.11 or newer on Linux or macOS, from the repository root. The single-worker process lock uses POSIX file locking; on Windows, use WSL2 or the Linux container option.

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[web,dev]'
cp config/example.toml config/local.toml
freelancer-web hash-password
```

Choose a strong, unique password at the private prompt. Copy the generated hash into your local secret configuration. A hash contains literal `$` characters: quote the complete value with single quotes in a shell, and do not paste a real password or OAuth token into source files or chat.

```bash
export FREELANCER_ADMIN_USERNAME=admin
export FREELANCER_ADMIN_PASSWORD_HASH='<generated scrypt hash>'
export FREELANCER_CONFIG_FILE=config/local.toml
export FREELANCER_WEB_DATABASE=data/web.sqlite3
export FREELANCER_WEB_ALLOWED_HOSTS=localhost,127.0.0.1
export FREELANCER_WEB_COOKIE_SECURE=false
export FREELANCER_DRY_RUN=true
export FREELANCER_ENABLE_AUTO_BIDDING=false
freelancer-web serve --host 127.0.0.1 --port 8000
```

Open `http://127.0.0.1:8000` and sign in. Disabling secure cookies is appropriate for this loopback-only HTTP session. Production requires HTTPS and `FREELANCER_WEB_COOKIE_SECURE=true`.

The equivalent direct ASGI command is:

```bash
uvicorn freelancer_auto_bidder.web.app:create_app --factory \
  --host 127.0.0.1 --port 8000 --workers 1
```

Keep one web worker. The application has a process lock to avoid multiple web schedulers using the same data. Do not also run the CLI's continuous bidder, another container replica, or another bidding tool against the same account and projects. The scheduler is part of the web process, so it stops when that process stops.

## Connection and secrets

`.env` files are not automatically loaded by the application. Use exported variables, a service manager or your cloud secret manager.

| Variable | Purpose |
| --- | --- |
| `FREELANCER_ADMIN_USERNAME` | Admin username; defaults to `admin` |
| `FREELANCER_ADMIN_PASSWORD_HASH` | Required generated scrypt password hash; no default password |
| `FREELANCER_WEB_COOKIE_SECURE` | Defaults to `true`; keep enabled behind production HTTPS |
| `FREELANCER_WEB_ALLOWED_HOSTS` | Comma-separated exact hostnames accepted by the web application |
| `FREELANCER_WEB_ORIGIN` | Optional explicit origin such as `https://bidder.your-domain.example`; recommended behind a proxy |
| `FREELANCER_WEB_DATABASE` | Web settings, sessions, drafts, activity and analytics database; default `data/web.sqlite3` |
| `FREELANCER_WEB_SESSION_TTL` | Fixed admin session lifetime in seconds; default `28800` |
| `FREELANCER_CONFIG_FILE` | Optional path to the existing engine TOML configuration |
| `FREELANCER_WEB_DEMO_MODE` | Explicit synthetic-data mode; defaults to `false` |
| `FREELANCER_OAUTH_TOKEN` | Approved Freelancer OAuth access token; never sent back to the browser |
| `FREELANCER_ENVIRONMENT` | `production` or `sandbox`; use the matching account and token |
| `OPENAI_API_KEY` | Optional server-side key for AI drafts |
| `OPENAI_MODEL` | Default AI model, `gpt-4o-mini`; admin prompt settings can select a supported model |
| `FORWARDED_ALLOW_IPS` | Trusted reverse-proxy IPs for Uvicorn forwarded headers |

The original four live-mode gates remain environment-only: `FREELANCER_DRY_RUN=false`, `FREELANCER_ENABLE_AUTO_BIDDING=true`, `FREELANCER_AUTO_BID_PERMISSION_VERIFIED=true`, and a nonempty `FREELANCER_PERMISSION_REFERENCE`. Leave dry-run enabled and the other acknowledgements disabled until you have verified that Freelancer permits unattended automatic submission for your exact account and application. Possessing a token or successfully searching projects does not prove this permission.

Use the [official authentication and permission guidance in the README](../README.md#api-authentication-and-permissions). Tokens expire or may be revoked; renew them through the approved application. This application does not implement an automatic OAuth refresh flow.

Admin passwords are verified against a salted scrypt hash. Opaque session tokens are stored as hashes in SQLite and issued in HttpOnly, SameSite cookies. State-changing authenticated requests require the session CSRF token. Login is rate limited, and logout invalidates the server-side session. Keep the private data directory and credential files restricted to administrators and the application account. Rotate credentials through the deployment's secret manager, then restart the application.

## Dashboard behavior

- Live search uses the official Freelancer API through the existing engine. Search is a dry-run operation: it scores projects and creates proposals, and never places bids. Filters narrow stored search results by title/description query, relevant skill, client country, currency, budget and recommendation.
- Country comes from the project's owner record, never currency or the project's work location. The existing score and eligibility rules still apply. This version supports active fixed-price jobs; hourly jobs remain skipped.
- The AI editor uses OpenAI only when its server-side key is configured. Without a key, it clearly reports template generation. AI prompts and tone are separate from prices. Review generated text and save edits before using it elsewhere. Interactive drafts are not automatically submitted; scheduled bidding continues to use the engine's existing proposal generator.
- Monitoring runs at five-minute intervals while enabled, subject to API cooldowns and request limits. The Auto Bidder switch also requires monitoring. Turning a control on never changes the environment's dry-run or permission gates. The Auto Bidder resets to off when the application restarts.
- Configure the automatic bidding window separately from monitoring. Schedule days use Monday `0` through Sunday `6`, with a named timezone and start/end times; overnight windows are supported. The default timezone is `Asia/Karachi`. Daily attempt limits still reset by UTC, as in the existing engine.
- History records actual local bid attempts and their statuses. Analytics describe local assessments, recommendations and submissions; they are not claims about awarded projects or earnings. With no activity, counts are zero. Demo jobs are labelled synthetic and cannot submit real bids.
- Demo mode uses separate `.demo` engine, report and web database files derived from the configured paths. Switching the flag off returns to the real workspace without exposing demo jobs, sessions, drafts or analytics there. Demo mode also keeps the optional AI provider offline.
- The duplicate ledger reserves an intent before submission and retains interrupted, uncertain or rejected attempts. An uncertain response stops further automated submission until reviewed. Inspect actual account bids and the ledger before deciding how to resolve a hold; do not delete the database to bypass it.

## Persistent data and single-worker operation

Persist the entire `data/` directory. The web database contains sessions, settings, drafts and run activity; the engine database, normally `data/bidder.sqlite3`, contains assessments and duplicate prevention records. Its location remains configurable in engine TOML. Back up both databases and the configuration using an SQLite-consistent backup or a stopped application; account for WAL files when relevant.

Do not use ephemeral cloud instance storage for these files. Keep one application process with one persistent volume, even if a hosting platform normally scales replicas automatically. Multiple independent database copies cannot coordinate duplicate protection. Restoring an older backup may omit recent attempts, so reconcile the actual Freelancer account before resuming submission after a restore.

## Secure Linux VM with Caddy and systemd

Use a Linux VM from your chosen provider, a real DNS hostname pointing to it, and a dedicated non-root application account. Restrict SSH to your administrative access path. Expose only HTTP/HTTPS through your firewall; keep Uvicorn on loopback. Caddy obtains and renews TLS certificates when your DNS and ports are ready. You may additionally place the admin application behind your organization's VPN or access gateway.

1. Install the source and virtual environment at `/opt/freelancer-auto-bidder`. Install `.[web]`; keep source and virtual environment owned by an administrator. Create `/opt/freelancer-auto-bidder/data` with mode `0700`, owned by the `freelancer-bidder` service account.
2. Review and install engine configuration as `/etc/freelancer-web.toml`, readable by the service account (for example, root-owned mode `0644`; it contains policy and prices, not credentials). Its database and report paths should remain beneath the writable `data/` directory. Customize the illustrative pricing before considering live bids.
3. Create `/etc/freelancer-web.env` using an administrator editor or secret manager, owned by root with mode `0600`. Systemd's `EnvironmentFile` reads literal dollar signs; values here do not need shell `export` prefixes. Start with these values, supplying private credentials through that file:

```dotenv
FREELANCER_ADMIN_USERNAME=admin
FREELANCER_ADMIN_PASSWORD_HASH=<generated scrypt hash, literal dollar signs>
FREELANCER_CONFIG_FILE=/etc/freelancer-web.toml
FREELANCER_WEB_DATABASE=data/web.sqlite3
FREELANCER_WEB_ALLOWED_HOSTS=bidder.your-real-domain.example
FREELANCER_WEB_ORIGIN=https://bidder.your-real-domain.example
FREELANCER_WEB_COOKIE_SECURE=true
FREELANCER_WEB_DEMO_MODE=false
FREELANCER_OAUTH_TOKEN=<your approved token supplied privately>
FREELANCER_ENVIRONMENT=production
FREELANCER_DRY_RUN=true
FREELANCER_ENABLE_AUTO_BIDDING=false
FREELANCER_AUTO_BID_PERMISSION_VERIFIED=false
FREELANCER_PERMISSION_REFERENCE=
FORWARDED_ALLOW_IPS=127.0.0.1
```

4. Install [freelancer-web.service](../deploy/freelancer-web.service) into `/etc/systemd/system/`. Review its paths and service account. It runs one loopback Uvicorn process with writes restricted to `data/`.
5. Install Caddy and adapt [Caddyfile](../deploy/Caddyfile) to your actual hostname. The `.invalid` fallback is deliberately unusable and must be replaced. Set Caddy's `FREELANCER_WEB_DOMAIN` to your real hostname or replace the placeholder in its config; keep its upstream `127.0.0.1:8000`. Preserve the public Host header and HTTPS forwarding.
6. Validate the adapted configuration, then start the services:

```bash
sudo caddy validate --config /etc/caddy/Caddyfile
sudo systemctl daemon-reload
sudo systemctl enable --now freelancer-web
sudo systemctl reload caddy
sudo systemctl status freelancer-web
sudo journalctl -u freelancer-web --since today
```

The web service uses `Restart=on-failure` with a cooldown. Configuration errors require correction rather than a rapid restart loop. Authentication or uncertain-submission failures are surfaced in the dashboard and disable automatic work until reviewed. Do not run the CLI bidder systemd unit alongside this service.

## Container deployment option

The supplied [Dockerfile](../deploy/Dockerfile.web) packages the same Python app; it does not replace the existing engine. [Compose](../deploy/compose.web.yml) runs the non-root app behind Caddy. It uses a named persistent data volume and does not publish Uvicorn's port. The only public ports belong to Caddy. This is a single-host setup, not a multi-replica orchestrator configuration.

The Compose file requires Docker Compose 2.30 or newer for `env_file.format: raw`. Create `/etc/freelancer-web.env` with literal, unquoted values so `$` in the password hash is not interpolated or retained inside quote characters. Supply the same secure values as above, but the container overrides configuration and database paths to its mounted paths. Install your reviewed TOML as `/etc/freelancer-web.toml` before starting; keep its output paths relative to `/app` under `data/`.

Set the real public hostname as a non-secret shell variable, then run from the repository root:

```bash
export FREELANCER_WEB_DOMAIN=bidder.your-real-domain.example
docker compose -f deploy/compose.web.yml config --quiet
docker compose -f deploy/compose.web.yml up -d --build
docker compose -f deploy/compose.web.yml ps
docker compose -f deploy/compose.web.yml logs --tail 100 web
```

Use `config --quiet`; rendered Compose output may contain resolved secrets. The Compose app trusts forwarded headers from its proxy network because only Caddy can reach the unexposed app service. Do not publish the app port or connect untrusted containers to that network. For other topologies, restrict `FORWARDED_ALLOW_IPS` to your proxy's exact IPs and configure the hosting platform's trusted-proxy path.

The `.dockerignore` excludes credentials, runtime state, local configuration, Git metadata and test caches from the image context. Keep API keys in runtime secrets, never Docker build arguments or image layers. In a managed container platform, configure equivalent persistent storage, one replica, HTTPS ingress, hostname validation and private secrets.

## Verification after an actual deployment

Do these checks against the actual deployment before describing it as live or enabling monitoring:

```bash
curl --fail https://YOUR-REAL-HOST/healthz
```

Open the HTTPS site in a browser, sign in, and verify the secure HttpOnly session cookie, logout and login behavior. Confirm unauthenticated API requests are rejected and the correct public hostname works. Verify that demo mode is off, the environment is the intended one, the default badge reads dry-run, monitoring and Auto Bidder are off, and real submissions remain disabled.

Configure the approved Freelancer token, run a manual search and review its provenance, scores and proposals. Enable monitoring in dry-run, wait through a full monitoring interval and inspect activity and API cooldown handling. Test a stop/restart and confirm persistent history/drafts survive while Auto Bidder resets off. Verify backups, logs and TLS renewal. Only after separately confirming Freelancer's automated-submission permission should you follow the README's live-mode steps and configure actual prices and limits.

## Automated checks

```bash
python -m pip install -e '.[web,dev]'
pytest -q
ruff check .
python -m pip check
```

The original 139 tests remain the engine baseline. The complete local suite passed **340 tests**, with lint, dependency and JavaScript syntax checks passing. Added web tests cover authentication, CSRF, controls, settings, schedules, AI drafting and integration with the existing runner. Local Chromium checks exercised desktop and mobile workflows with no JavaScript errors or horizontal page overflow. Tests mock external APIs and cannot establish account permissions or prove a cloud deployment. Cloud credentials, real Freelancer/OpenAI requests, TLS/DNS and provider-specific deployment health require separate validation in your environment.
