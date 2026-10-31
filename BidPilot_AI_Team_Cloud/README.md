# BidPilot AI — reviewed project

**v4.4.1 existing-account recovery:** [check the actual database/password and recover the same account](V4_4_1_ACCOUNT_RECOVERY_ROMAN_URDU.md). A local operator can diagnose401 with hidden password input or reset an existing enabled account's password, preserving its paid plan and dates. No new public reset endpoint or email service is added. V4.4's limiter fixes remain.

**v4.4 sign-in fix:** [diagnosis, upgrade and recovery steps](V4_4_LOGIN_FIX_ROMAN_URDU.md). Successful/shared-IP sign-ins no longer consume the failed-password allowance, and registration no longer issues a redundant login. Persistent account/network failure limits and a separate request cap remain. The UI shows the server's retry countdown. Admins can reset a selected member's sign-in failure lock, with a local operator recovery command available; neither changes passwords or subscriptions. Pro upgrades and renewals preserve the original password.

**v4.3 payments and account security:** [payment details, monthly lifecycle and update steps](V4_3_BILLING_SECURITY_ROMAN_URDU.md). Admins can configure the supplied Easypaisa/JazzCash/NayaPay/Meezan receiving accounts. Customers submit payment references using their account identity; passwords are never requested for payment or returned to administrators. Actual settlement remains manually verified. Approved new grants record activation and next-calendar-month expiry; automatic expiry blocks paid access and pauses automation. Existing expiry dates are preserved on upgrade. The Security panel supports current-password-protected password changes and own-account session revocation.

**v4.2 auto-bidding setup:** [enable/disable commands and WSL steps](V4_2_AUTOBID_ROMAN_URDU.md). `configure_autobid.py enable|disable|status` manages the existing private server automation flag with a private backup and atomic replacement. Restart both web and worker after changes, then explicitly enable and save Auto-Pilot for the intended connected account. The helper never enables every user's preference or changes the database. Settings now explains which gate is blocking activation. All existing filters, quotas, bid spacing and provider pauses remain enforced.

**v4.1 AI Chat update — 31 October 2026:** [usage and update steps in Roman Urdu](V4_1_CHATBOT_ROMAN_URDU.md). The sidebar AI Chat now answers general questions, coding, writing and translation using server-side Gemini/OpenAI. A separate App help mode retains BidPilot guidance. Live replies show the actual provider/model; general-chat unavailability is explicit. Mode changes, Clear and account changes clear the in-memory conversation. General chat receives no account-status snapshot, has no action tools and does not persist conversation in the database or browser storage. Active subscription, configured valid provider credentials and provider access are required for live AI. Existing worker, billing and automation behavior remain.

**v4 update — 31 October 2026:** [new features and WSL update steps in Roman Urdu](V4_FEATURES_ROMAN_URDU.md). The Overview now separates worker health from actual successful Freelancer project polls, with next-scan timing, pending project checks and bid pacing. A bounded scheduler checks due scans every second, processes up to four accounts concurrently and records a heartbeat every ten seconds independently of API/AI work. Background project refresh targets at least 60 seconds between attempts (`SCAN_SECONDS`, minimum 60). Fresh results immediately enter the existing auto-bidding flow when enabled; provider latency, filters, working hours, quotas and minimum bid gaps can delay submission beyond a minute. Scan leases prevent overlapping refreshes, and Settings saves preserve edits made while a request is pending. The dark/purple design, existing [v3 features](V3_FEATURES_ROMAN_URDU.md), credentials and subscriptions are preserved. Install the updated requirements and run the worker separately from the web server.

BidPilot is a FastAPI service with a web dashboard, PostgreSQL/SQLite storage and a separate scanner worker. It connects to each user's Freelancer account and can generate proposal drafts using Gemini/OpenAI or the local template generator. Cloud credentials are encrypted at rest; account passwords are hashed. Security depends on the deployment, secret protection and ongoing maintenance. No review can guarantee that software is free of every bug.

The 30 October update fixes races when saving and testing AI credentials and uses `gemini-3.5-flash-lite` for new configurations. Google [limits Gemini 2.5 access to existing users](https://ai.google.dev/gemini-api/docs/deprecations); a 404 on a catalog-listed 2.5 model may reflect this policy. When the catalog reports the recommended model, the failed test offers an explicit model-selection action. Save and test after selecting; existing users' model choices and operator environment settings are preserved. Gemini requests use model sampling defaults for compatibility with current models. OpenAI `401 invalid_api_key` still requires a valid full provider secret; an active key record ID cannot substitute for that secret.

## Subscription behavior

| Plan ID | Price | App bid quota |
| --- | --- | --- |
| `paid_starter` | US$10 per calendar month for new grants | 150 submitted, sending or uncertain attempts in the billing period |
| `paid_pro` | US$40 per calendar month for new grants | No monthly app quota; user's hourly/daily limits and Freelancer restrictions still apply |
| `free_team` | Free | Two founder accounts maximum, including the owner |

The supplied receiving accounts are available in admin payment settings, with phone and IBAN validation. Existing non-empty operator environment destinations remain a legacy fallback until database settings are saved. A transaction ID submitted in the dashboard is a **request for manual review**, not proof of payment. The admin must independently verify settlement in the payment provider before approval. New paid approval records activation and next-calendar-month expiry in Asia/Karachi. Same-tier renewal extends access without resetting the current quota window; month-end dates follow the original billing day. Existing stored expiries and legacy quota anchors are preserved on upgrade. This project does not implement merchant settlement API verification, automatic recurring charges, refunds or card-data collection. A Stripe payment-link setting is only a link.

The setup utility creates the owner and optionally a second founder. Public signup cannot assign admin or free-founder privileges. Keep founder passwords private; there is no automatic email ownership verification or forgotten-password email delivery. Bootstrap password variables only create a new account; changing them later does not reset an existing account password. Plan an operator-supported identity-verification/recovery process before inviting customers.

## Ubuntu: local testing

The commands below target Ubuntu 24.04 LTS, whose default `python3` is Python 3.12. The dependency set was tested on Python 3.12. For another Ubuntu release, select a Python 3.12 interpreter explicitly or use the Docker deployment. Start on a new test installation:

```bash
sudo apt update
sudo apt install -y python3 python3-venv
cd BidPilot_AI_Team_Cloud
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements-dev.txt
python setup_local.py
python -m pytest -q
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Open `http://127.0.0.1:8000`. In another terminal in the project, activate `.venv` and run `python -m app.worker` when testing scanning. The web server alone does not run the background worker. Keep the development HTTP server bound to loopback; use HTTPS deployment for public traffic.

`setup_local.py` preserves an existing `.env`, generates independent random session/encryption/database secrets and creates the file with mode `0600` on Unix. It does not include any credentials supplied in a chat. Generated bootstrap passwords use `ADMIN_PASSWORD_B64`/`SECOND_FOUNDER_PASSWORD_B64` to preserve special characters across dotenv and Compose parsers; base64 is encoding, not encryption, and must be protected as the password itself. Existing plaintext `ADMIN_PASSWORD`/`SECOND_FOUNDER_PASSWORD` environment variables remain supported. Update `.env` using a private editor and keep it out of Git, screenshots and support bundles. On Windows, use `start_windows.bat` for local testing and protect `.env` with your account's file permissions.

## Ubuntu: HTTPS production deployment

A domain with DNS A/AAAA records pointing at the Ubuntu server, reachable ports 80/443 and Docker Engine with the Compose plugin are needed. Install Docker using the official instructions for your Ubuntu release. Keep SSH restricted to your access path. Do not expose PostgreSQL or port 8000 publicly.

For a fresh production installation (with Python 3.12 available):

```bash
sudo apt update
sudo apt install -y python3 python3-venv
cd BidPilot_AI_Team_Cloud
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.txt
python setup_local.py --production
chmod 600 .env
# Privately configure actual payment destinations and optional shared AI credentials.
docker compose up --build -d
docker compose ps
curl --fail https://your-domain.example/health
```

The Compose file creates a private PostgreSQL service, runs database initialization before starting web/worker, and places Caddy in front of the web service. Caddy obtains and renews TLS certificates. Only Caddy publishes ports. Application containers run as UID 10001 with a read-only root filesystem. Database and Caddy state persist in named Docker volumes. If `172.30.0.0/24` conflicts with your server networks, change both the Compose subnet/Caddy IP and `FORWARDED_ALLOW_IPS` consistently. The files are deployment configuration; the target Ubuntu host/domain still needs its own end-to-end verification.

If reusing a development `.env`, the setup command leaves it unchanged. Edit `APP_ENV=production`, `PUBLIC_URL=https://your-domain`, `SITE_DOMAIN=your-domain`, and `SECURE_COOKIES=true` privately before deploying. Compose overrides `DATABASE_URL` to its PostgreSQL instance. Use a URL-safe generated `POSTGRES_PASSWORD`; changing it in `.env` after PostgreSQL initialization does **not** change the existing database password. Never remove/recreate the database volume merely to change credentials.

Environment changes need recreation: `docker compose up -d --force-recreate web worker`. Do not run `docker compose config` in a shared terminal or paste its output; it expands secrets. Render users may use `render.yaml` as an alternative: configure shared variables once in its environment group, use the same Fernet key for web and worker, and set `PUBLIC_URL` to the actual HTTPS web URL. Hosting plans and provider prices should be checked with the host before deployment.

## API setup and live validation

1. Rotate credentials that were pasted into a chat. Use fresh keys from the intended provider and do not infer validity from a key prefix. In Settings, save personal Gemini/OpenAI credentials and run **Test AI Keys**. Server `.env` keys are a shared fallback; admin personal database keys are not shared with other users. A local template fallback can draft proposals without proving cloud API connectivity.
2. `OPENAI_BASE_URL` defaults to `https://api.openai.com/v1`. If a key belongs to an OpenAI-compatible third party, set that provider's documented HTTPS endpoint on the **server**; its privacy, billing and auth behavior need separate verification. Never send a provider key to a guessed endpoint. Set a model supported by your account.
3. Create a Freelancer sandbox token first. Connect it under **Settings → Sandbox**, confirm the imported account identity and scan projects. Token scopes, account bid balance and project eligibility remain subject to Freelancer's API.
4. Test a manual bid only on a sandbox project you control. Check it appears exactly once in Freelancer and the dashboard. A timeout creates an `uncertain` result to avoid automatic duplicate retry; reconcile against Freelancer manually.
5. To enable unattended bids, set `PLATFORM_AUTOBID_APPROVED=true`, restart web/worker, then explicitly enable Auto-Pilot in the user's Settings. Review the displayed budget, currency, project-type, daily/hourly and package limits first. Auto-chat is a separate opt-in setting. Production connections use the real Freelancer account; actual bids/messages change that account and may incur charges.
6. Before accepting a customer's payment, verify signup/login/logout, cross-user access isolation, actual provider settlement and plan expiry on your HTTPS deployment. Keep automation off for the first deployment until these checks pass.

The test suite uses controlled mocks for external APIs and does not charge cards, validate a real supplied key, or place live bids. Network/API availability on the target host, actual credentials, payment settlement and TLS/DNS must be checked there. Logs should be restricted to operators; they can include project/customer activity. Do not publish database files or operational logs.

## Backups, upgrades and recovery

Run `bash deploy/backup.sh` to create a private PostgreSQL dump. Keep encrypted off-server backups of both the dump and `.env` through a separate secure process. The dump alone cannot recover encrypted Freelancer/AI tokens without the original `FERNET_KEY`. Protect backups as customer data and define your own retention and deletion policy.

For upgrades, take a backup, test the new version and migrations against a restored copy, stop the old worker, then rebuild/redeploy. Never run two different app versions against the same live database during migration. Dependencies are pinned to versions resolved in the review environment; review advisories and rerun tests before upgrades. Docker image tags should be pinned to organization-approved image digests for controlled production releases.

Restore drill (destroys/replaces data in the selected database; first use an isolated test deployment): stop `web` and `worker`, restore a chosen dump with `docker compose exec -T db pg_restore -U bidpilot -d bidpilot --clean --if-exists < path/to/backup.dump`, restore the matching private configuration, then restart. Check login, token decryption and project history before enabling the worker.

Do not replace `FERNET_KEY` to rotate API credentials: revoke/regenerate the provider key and save the new key via Settings. Fernet key rotation requires decrypting/re-encrypting every protected field with the old/new keys and testing recovery; this project does not ship an automatic Fernet-key rotation command. Changing `SESSION_SECRET` requires a coordinated restart of all app processes and should be treated separately from revoking existing sessions. A lost encryption key means saved provider tokens must be reconnected.

## Data sent to integrations

Proposal and Freelancer chat-greeting AI requests include the profile text, name, skills, portfolio and the relevant project brief or message. General AI Chat sends the redacted question and recent conversation, without an account-status snapshot; App help also includes a limited own-account snapshot. Chat has no action tools or persistent conversation storage. Known saved credentials and common credential formats are redacted from chat prompts/replies; keep secrets and sensitive customer data out of chat. Use of Gemini/OpenAI is subject to those providers’ privacy and billing settings. Provider secrets authenticate requests through server-side headers. Saved personal keys are encrypted; server keys remain private runtime configuration. Plain transaction references, emails and job history still require restricted database and backup access.

## Login address and saved-key diagnostics patch

Local development supports exact `localhost`, `127.0.0.1` and `::1` browser addresses on the configured scheme and port. This allowance requires a same-origin request and is disabled in production. Prefer `http://127.0.0.1:8000` locally; use the configured HTTPS public origin in production. Origin denials now return readable JSON with the normal security headers.

AI test results refer to saved configuration. Save edits before testing; unsaved changes are explicitly blocked. A configured key is not a verified connection. OpenAI 401 requires a valid provider-issued secret credential. Gemini 404 diagnostics attempt read-only discovery of generation-supported models, with bounded pagination and explicit partial/failure reporting. Discovery never selects or generates with another model automatically. Save a supported model ID and test again. No supplied credentials were used during this patch's tests.

## Optional founder conflicts during restart

Bootstrap settings create accounts, not reset or promotion rules. If SECOND_FOUNDER_EMAIL already belongs to an existing paid member, startup preserves that member's credentials, tokens, role, plan and expiry and logs a generic warning. It no longer aborts the web server or worker. Assign founder access explicitly through the authenticated Admin screen if a free seat is available. Invalid optional founder settings and full founder slots also skip provisioning; the first mandatory administrator still requires valid bootstrap configuration. Existing administrators do not require old bootstrap passwords to be parsed again.

Keep your current database and matching private .env. No reset or deletion is required for this fix. Explicit APP_ENV=test ignores local dotenv files so test fixtures do not import real bootstrap/API credentials.

## Provider codes when model changes do not resolve an API error

Diagnostics expose only fixed classifications and allowlisted provider status/reason codes. Non-success response bodies are inspected up to64KiB; raw messages, credentials, URLs, headers and prompts are not returned. `body: non_json`/`unreadable` can help identify a nonstandard response without exposing its content. HTTP404 by itself does not establish that a model is retired or a key is valid. If the selected model is listed, the UI stops suggesting model changes and explains that catalog access does not prove generation access. The actual credential/project/API permission or endpoint cause still needs provider-side verification. OpenAI401 remains a credential rejection; repeated testing of the same rejected saved credential cannot establish a working connection.

## Independently verify an OpenAI credential

An active API-key record has an identifier separate from its secret value. OpenAI documents a record ID such as `key_abc` and a separate redacted secret, along with active project-access metadata: https://developers.openai.com/api/reference/resources/admin/subresources/organization/subresources/projects/subresources/api_keys/methods/retrieve . Use the complete secret from key creation, not a record ID or a masked value. Format alone is not validation: the app continues to accept opaque credentials, and only displays the possible-identifier hint after an explicit invalid-key401 from the official OpenAI endpoint.

Run `.venv/bin/python check_openai_key.py` from this application folder. Paste the complete secret at the hidden prompt; it is not printed or stored. The standalone standard-library checker imports no app/database/dotenv configuration and requests only `https://api.openai.com/v1/models`, with TLS verification and redirects disabled. It returns safe JSON: `model_read_access` confirms only model-list access, not generation/billing; `credential_rejected` with401 is an independent authentication failure;403 can mean access restrictions. No raw provider bodies or credentials are displayed. Do not paste a secret into a command-line argument or a screenshot.
