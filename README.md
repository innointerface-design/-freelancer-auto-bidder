# Freelancer Auto Bidder

A Python 3.11+ bidding engine and CLI with a FastAPI web dashboard. It finds relevant Freelancer.com projects, explains a **BID** or **SKIP** recommendation, and drafts short English proposals. **Real bid submission is disabled by default.** A BID recommendation in dry-run mode does not place a bid.

The service profile covers short-form video editing; YouTube Shorts, Instagram Reels and TikTok; motion graphics and After Effects; AI video ads and animation; product and promotional videos; Premiere Pro and DaVinci Resolve. This initial version handles active, fixed-price projects. Hourly projects are skipped.

## BidPilot AI Updated v4.4.2 — source and ZIP download

The reviewed BidPilot SaaS application is available here:

- [Download the complete updated v4.4.2 ZIP](https://github.com/innointerface-design/-freelancer-auto-bidder/raw/refs/heads/main/downloads/BidPilot_AI_Updated_v4_4_2.zip)
- [Expire a paid plan manually while preserving account login](BidPilot_AI_Team_Cloud/V4_4_2_PLAN_EXPIRY_ROMAN_URDU.md)
- [Check and recover an existing account while preserving its paid plan](BidPilot_AI_Team_Cloud/V4_4_1_ACCOUNT_RECOVERY_ROMAN_URDU.md)
- [Pro sign-in fix and scoped lock recovery](BidPilot_AI_Team_Cloud/V4_4_LOGIN_FIX_ROMAN_URDU.md)
- [Payment accounts, monthly plans and account security](BidPilot_AI_Team_Cloud/V4_3_BILLING_SECURITY_ROMAN_URDU.md)
- [Enable auto-bidding and update instructions](BidPilot_AI_Team_Cloud/V4_2_AUTOBID_ROMAN_URDU.md)
- [New Gemini/OpenAI AI Chat and update instructions](BidPilot_AI_Team_Cloud/V4_1_CHATBOT_ROMAN_URDU.md)
- [New v4 features and update instructions](BidPilot_AI_Team_Cloud/V4_FEATURES_ROMAN_URDU.md)
- [Previous v3 features](BidPilot_AI_Team_Cloud/V3_FEATURES_ROMAN_URDU.md)
- [Application source](BidPilot_AI_Team_Cloud/)
- [Ubuntu setup in Roman Urdu](BidPilot_AI_Team_Cloud/README_ROMAN_URDU.md)
- [Second-review fixes in Roman Urdu](BidPilot_AI_Team_Cloud/SECOND_REVIEW_CHANGES_ROMAN_URDU.md)
- [Review and test results](BidPilot_AI_Team_Cloud/REVIEW_AND_TESTS.md)

BidPilot has $10 Starter and $40 Pro plans, two free founder slots, encrypted saved API tokens, and manual payment verification. V4.1 adds general AI Chat for questions, coding, writing and translation through server-side Gemini/OpenAI, alongside the existing app-help mode. Provider/model labels identify actual live answers; unavailable AI is reported honestly. Conversation is held only in tab memory, and mode/account changes clear it. The chat has no account-action tools. V4 live automation, independent worker heartbeat, bounded account scanning, filters, schedules, bid gaps, quotas and API cooldowns remain. The original dark/purple design and private configuration are preserved. Provider latency can delay a scan or bid beyond a minute; an online server and running worker are required. Verification details are recorded in the review report. Live AI credentials, Freelancer sandbox acceptance, actual payment settlement and public HTTPS still require validation on the target server. No real credentials, customer databases or private `.env` files are included.

For this application, change into `BidPilot_AI_Team_Cloud` and follow its own README and Python 3.12 requirements. Its source and tests are separate from the CLI/dashboard described below. The repository adds a local `pytest.ini` for test isolation when BidPilot is nested in this repository; the downloadable ZIP contains the current standalone application source. GitHub cannot preview ZIP contents: use the download link above, or choose **Download raw file** on the ZIP's GitHub page.

## Web dashboard quick start

The web application extends the existing engine and retains the CLI. It adds admin login, live API search, skill/country/currency/budget filters, proposal drafting and editing, scoring, bid history, analytics, monitoring controls and schedules. Both controls default to off; the Auto Bidder resets off at every restart, while a previously enabled monitoring preference is retained. The default bidding mode is dry-run.

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[web,dev]'
cp config/example.toml config/local.toml
freelancer-web hash-password
```

The last command prompts privately for an admin password and prints a scrypt hash. Store that hash in your local secret configuration. For an interactive shell, surround the entire hash with single quotes so its literal `$` characters are preserved:

```bash
export FREELANCER_ADMIN_PASSWORD_HASH='<paste the generated scrypt hash here>'
export FREELANCER_ADMIN_USERNAME=admin
export FREELANCER_CONFIG_FILE=config/local.toml
export FREELANCER_WEB_ALLOWED_HOSTS=localhost,127.0.0.1
# Local HTTP only; use true when serving through HTTPS in production.
export FREELANCER_WEB_COOKIE_SECURE=false
export FREELANCER_DRY_RUN=true
export FREELANCER_ENABLE_AUTO_BIDDING=false
freelancer-web serve --host 127.0.0.1 --port 8000
```

Open `http://127.0.0.1:8000` and sign in with the password you hashed. No admin password is supplied by the repository. Add an approved `FREELANCER_OAUTH_TOKEN` through your environment or secret manager to search actual projects. Without a token, the dashboard shows that the connection is unconfigured. Set `FREELANCER_WEB_DEMO_MODE=true` explicitly for labelled synthetic jobs; demo mode never calls Freelancer or submits bids. Demo mode derives separate `.demo` database and report files, preserving real account data when the flag is changed.

Set `OPENAI_API_KEY` to enable the optional AI drafting provider. Without it, the editor identifies generated text as a template draft. AI instructions and tone are saved separately from bid prices. Generated or edited drafts are saved for review; automatic runs continue to use the existing engine's deterministic proposal generator.

Use the dashboard to turn monitoring on, review dry-run results and configure schedules and prices. The web Auto Bidder control cannot grant API permission or override the environment's live-submission gates. Follow [the permission verification steps](#enable-real-bidding-only-after-verification) before considering any real submission.

See [web configuration and secure deployment](docs/WEB_DEPLOYMENT.md) for environment variables, HTTPS hosting, persistent data, tests and post-deployment checks. **Cloud deployment has not been performed by these instructions.**

## CLI quick start

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'
cp config/example.toml config/local.toml
```

Review `config/local.toml`. Every price and delivery period in the example is **illustrative**, not an estimate of your rates or a promise to clients. Set prices you can honor before enabling real bidding.

Acquire an OAuth access token through your approved Freelancer developer application. Keep it in a local secret manager or environment variable; never put tokens in source control, command-line arguments, logs, proposals, or chat. For an interactive Bash session, this avoids placing the value in shell history:

```bash
read -rsp 'Freelancer OAuth token: ' FREELANCER_OAUTH_TOKEN
echo
export FREELANCER_OAUTH_TOKEN
export FREELANCER_DRY_RUN=true
export FREELANCER_ENABLE_AUTO_BIDDING=false

freelancer-bidder --config config/local.toml check
freelancer-bidder --config config/local.toml run --once
```

`check` validates configuration without making network requests. `run --once` performs one search cycle. Inspect `data/recommendations.json` for scores, reasons, source-backed client information, proposed amounts and proposals. Run continuously with:

```bash
freelancer-bidder --config config/local.toml run
```

The process must remain running to poll; a chat session does not schedule the application. To return to dry-run at any time, set `FREELANCER_DRY_RUN=true` and restart the process.

## API authentication and permissions

Create/configure your application in the [Freelancer developer portal](https://developers.freelancer.com/), follow its current OAuth authorization process, and use a token authorized for the intended Freelancer account. The CLI sends that token using the official `Freelancer-OAuth-V1` header and keeps TLS verification enabled. It does not ask for your Freelancer password or implement browser automation. Manage token expiry, revocation and renewal through the developer application or your secret manager; there is no automatic OAuth refresh flow in this initial version.

Production uses `https://www.freelancer.com/api/`. For development, set `FREELANCER_ENVIRONMENT=sandbox` and use a separate sandbox account/token issued for `https://www.freelancer-sandbox.com/api/`. Sandbox authorization and results do not establish production permissions.

Official API contracts are reflected in the [Freelancer Python SDK](https://github.com/freelancer/freelancer-sdk-python) and [Android SDK](https://github.com/freelancer/freelancer-sdk-android). The application uses:

| Purpose | Official API route |
| --- | --- |
| Search active projects | `GET /api/projects/0.1/projects/active/` |
| Refresh project data | `GET /api/projects/0.1/projects/` |
| Read authenticated account | `GET /api/users/0.1/self/` |
| Check current account's existing bids | Project bids endpoint, filtered to the bidder |
| Submit a configured bid | `POST /api/projects/0.1/bids/` |

An OAuth token, a successful search, or access to the bid endpoint does **not** prove that your application is permitted to submit unattended automatic bids. There is no permission-discovery claim in this application. Verify the current API rules, account restrictions, app permissions, bid allowances and any required approval in your developer application and with Freelancer support before enabling real submissions. Do not infer permission from a guessed OAuth scope name.

### Enable real bidding only after verification

1. Confirm that the specific account and application have permission for **unattended automatic bid submission**. Retain the permission/approval reference and review the current Freelancer API terms and limits.
2. Review dry-run results and proposal wording. Configure your own currency prices, delivery period, milestone percentage and daily cap. Test against the sandbox where available.
3. Keep a persistent, private database and run one deployed worker. Confirm that the configured OAuth token belongs to the intended account and environment.
4. Only after those checks, configure all four environment values below and restart the worker:

```bash
export FREELANCER_DRY_RUN=false
export FREELANCER_ENABLE_AUTO_BIDDING=true
export FREELANCER_AUTO_BID_PERMISSION_VERIFIED=true
# Set this to your actual non-secret verification reference.
export FREELANCER_PERMISSION_REFERENCE='your actual app permission or support approval reference'
```

These flags record your acknowledgement; they do not obtain or independently verify permission from Freelancer. Missing acknowledgements cause live configuration to be rejected. They should remain unset or disabled until permission has been verified. This repository does not enable them for you.

## Configuration

The CLI loads TOML with `--config`; see [config/example.toml](config/example.toml). Paths are relative to the process working directory. Unknown keys and invalid values fail validation. `.env.example` documents environment names; **`.env` files are not loaded automatically**. Use your shell, service manager or secret manager to supply them.

| Setting | Default | Meaning |
| --- | --- | --- |
| `poll_interval_seconds` | `300` | At least five minutes between searches; server-directed cooldowns may delay cycles |
| `search_queries` | Video service queries | Search terms; example configuration includes the complete service profile |
| `page_size`, `max_pages_per_query` | `50`, `2` | Bounded result pagination |
| `max_requests_per_cycle` | `80` | Local request budget; includes read retries and submission-related calls |
| `min_request_interval_seconds` | `1.0` | Minimum spacing between API requests |
| `request_timeout_seconds`, `max_get_retries` | `30`, `2` | Bounded request timeout and retries for transient read failures |
| `max_project_age_hours` | `24` | Freshness window; missing or invalid submission dates are skipped |
| `score_threshold` | `70` | Minimum score for an otherwise eligible BID recommendation |
| `preferred_countries` | `US`, `GB`, `CA`, `AU`, `AE` | Priority for USA, UK, Canada, Australia and UAE |
| `require_known_country` | `true` | Skip projects without an established client country |
| `require_verified_payment` | `true` | Skip when payment verification is false or unavailable |
| `min_client_rating` | `3.5` | Reject a reviewed employer rating below this level |
| `bid_period_days`, `milestone_percentage` | `5`, `100` | Actual submission terms; customize before use |
| `max_bids_per_day` | `3` | Shared local daily attempt cap, counted in UTC |
| `database_path` | `data/bidder.sqlite3` | Durable recommendations and submission ledger |
| `report_path` | `data/recommendations.json` | Latest cycle's human-reviewable JSON report |

Environment-only settings are `FREELANCER_OAUTH_TOKEN`, `FREELANCER_ENVIRONMENT`, `FREELANCER_DRY_RUN`, `FREELANCER_ENABLE_AUTO_BIDDING`, `FREELANCER_AUTO_BID_PERMISSION_VERIFIED` and `FREELANCER_PERMISSION_REFERENCE`. Boolean values must be `true` or `false`.

### Client data, scoring and proposals

The project owner's ID joins to the API's user record. Client country comes from the owner's `location.country.code` or `location.country.name`; project work location and project currency never establish client country. This verifies what the API reports about the client profile, not the client's physical whereabouts. Missing or conflicting country data remains unknown.

The scorer considers the title, full description and required skills; project budget and currency; client employer rating and review count; `status.payment_verified`; client country; project status, type and age. Each recommendation includes reasons. Preferred countries receive priority rather than being an exclusive country allowlist. A high score cannot override eligibility checks such as unsupported currency, unavailable required verification, an inadequate budget or an inactive project.

| Signal | Points |
| --- | ---: |
| Relevant service match | 50 |
| Budget fits the configured currency rule | 15 |
| Client country is in the preferred list | 15 |
| Verified client payment | 10 |
| Usable employer reviews with rating at or above the minimum | 10 |

Missing review history earns no review points. Review count comes from employer reviews, not the number of completed projects. Missing data is reported as unknown rather than filled with a favorable assumption.

Each currency needs its own explicit budget rule with `min_budget`, `bid_amount` and `max_bid`. The bid must fit the project's stated budget and your configured cap. The app does not convert currencies or infer a client country from USD, GBP or any other currency. The example includes independent USD, GBP, CAD, AUD and AED rules; remove currencies you do not want to bid in.

Proposals use deterministic English templates with project-specific service and requirement matching. No LLM service or extra API key is needed. They avoid invented credentials, portfolio claims and guaranteed outcomes. Review dry-run drafts carefully: matching and requirement extraction are heuristics, not a full creative brief or a judgment of legal/contractual obligations.

### Polling and API limits

Polling defaults to five minutes, with a configurable minimum of 300 seconds. Requests are paced and have a per-cycle budget. Transient GET failures have bounded retries; rate-limit responses respect the server's retry delay. Authentication and permission failures stop polling until the configuration is corrected. Submission requests are never automatically retried. A stop signal is checked between API phases and after request pacing, before starting another HTTP request; an already in-flight request may finish.

Tune pacing, query count and page limits to the limits and permissions assigned to your application. A local limit is not a guarantee of compliance with a changing server quota. Search is bounded to recent active projects and a maximum number of pages per query; bursts or API search limitations can mean some projects are not returned. Overlapping search results are deduplicated by project ID.

### Duplicate protection and uncertain submissions

Dry-run recommendations do not reserve real bids. Live mode refreshes project data, re-evaluates eligibility, checks the authenticated account's existing bids through the API and atomically reserves a local submission attempt before POST. Reservations are unique by API environment, bidder ID and project ID. The daily cap is enforced in the same SQLite transaction, so workers sharing that database cannot race past it.

Keep the database across restarts and deployments, and back it up privately. An interrupted, in-flight or uncertain attempt remains held; the next poll does not automatically replay it. A timeout can mean Freelancer accepted the bid even though the response was lost. Inspect the account's actual bids and the local ledger before resolving any such case. The initial version intentionally has no automatic reset/retry command for held attempts.

The API read and subsequent POST are separate operations. Another tool or manual session bidding at the same moment can race with this worker; the application cannot promise server-level exactly-once behavior without a documented API idempotency mechanism. Prefer one worker, one persistent ledger, and no concurrent bidding tools for the same projects.

## Tests

```bash
python -m pip install -e '.[web,dev]'
pytest -q
ruff check .
```

Tests use local fixtures, fake clients and mocked HTTP responses. They cover safe defaults, API behavior, country provenance, scoring, proposals, duplicate protection and submission failures without sending real bids or requiring a token. GitHub Actions runs the checks on Python 3.11–3.13. Live authentication and account-specific Freelancer permissions require separate verification; automated tests cannot establish them.

Initial local validation on Python 3.12: **139 tests passed**, `ruff check .` passed, and `pip check` found no broken requirements. The end-to-end tests exercise the CLI and HTTP adapter against a mocked API, including dry-run and a single submission across repeated enabled runs. No live Freelancer API authentication or bid submission was performed.

Web extension validation on Python 3.12: **340 tests passed**, including all 139 original engine tests, with the original engine source and tests unchanged. Lint, JavaScript syntax and dependency checks passed. A local Chromium smoke test exercised admin login, search, proposal generation/editing/saving, analytics, settings and dry-run controls, including 390px and 768px mobile layouts without horizontal overflow or browser JavaScript errors. External Freelancer/OpenAI calls were mocked or used the offline demo; cloud hosting was not deployed.

## Deploy on Linux with systemd

Use a dedicated non-root account. Install this repository and its virtual environment under `/opt/freelancer-auto-bidder`, with source and environment owned by an administrator. Create a private writable `data/` directory owned by the `freelancer-bidder` service user. Preserve this directory during upgrades.

Install a reviewed copy of the configuration at `/etc/freelancer-bidder.toml`. Create `/etc/freelancer-bidder.env` with a secret-management tool or administrator editor, owned by root with mode `0600`:

```dotenv
FREELANCER_OAUTH_TOKEN=<token supplied privately>
FREELANCER_ENVIRONMENT=production
FREELANCER_DRY_RUN=true
FREELANCER_ENABLE_AUTO_BIDDING=false
FREELANCER_AUTO_BID_PERMISSION_VERIFIED=false
FREELANCER_PERMISSION_REFERENCE=
```

Replace the placeholder in that private file. Do not copy real values into the repository. Install [deploy/freelancer-bidder.service](deploy/freelancer-bidder.service) into `/etc/systemd/system/` and start the service after reviewing the unit and configuration:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now freelancer-bidder
sudo systemctl status freelancer-bidder
sudo journalctl -u freelancer-bidder --since today
```

The service sets a private file-creation mask and permits writes only to `data/`. Reports and the ledger contain project information and proposal drafts; restrict access and retention appropriately. The service emits operational logs to the journal. Deploy with network access to the selected official Freelancer host, valid CA certificates and the approved token. Use your application's assigned API limits and retain TLS verification.

The unit waits five minutes before restarting unexpected failures. Configuration errors (exit 2), authentication/permission failures (exit 3), uncertain submissions (exit 4) and local state failures (exit 6) prevent automatic restart and require operator review. A one-shot API/rate-limit failure returns exit 5; daemon mode observes the cooldown and continues. Server cooldown state is held in memory, so preserve the process during cooldown and do not rapidly restart it after a rate limit.

To stop all activity: `sudo systemctl stop freelancer-bidder`. After editing environment values, restart the service so it reads the new values. Start deployment in dry-run mode and inspect results before following the real-bidding verification steps above.
