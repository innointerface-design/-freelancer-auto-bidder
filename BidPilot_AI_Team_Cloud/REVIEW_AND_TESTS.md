# BidPilot AI — v4 features and verification

## v4 release — 31 October 2026

Current release verification: **402 application tests passed plus 26 subtests**, 55.80 seconds. The separate repository CLI/dashboard suite passed **340 tests**. Repository Ruff, JavaScript syntax, Python compilation, dependency consistency and diff checks passed. One upstream Starlette TestClient deprecation warning remains. Results under older release headings are historical.

The worker now checks scheduling every second, runs at most four accounts concurrently and records an independent heartbeat every ten seconds. A slow API/AI request for one account no longer serially blocks all other accounts. Background scan intervals are at least 60 seconds and are checked again under the database lock. Pending saved matches are checked between scans without repolling project/chat APIs. Successful scans feed the existing automatic proposal/bid flow immediately; all permission gates, project filters, schedules, bid gaps, quotas and provider waits remain enforced. These intervals are targets, not guarantees of a bid reaching a client within a minute.

Persistent scan claims prevent overlapping account refreshes, expire after a crashed process and reject stale results after connection replacement. Actual successful-poll timestamps are separate from scan-start timestamps. Ctrl+C/SIGTERM stops new scheduled work and signals running automatic paths to stop before further API/AI work or mutation; already-dispatched remote requests can still finish. Tests cover minute-boundary cadence, bounded/fair scheduling, heartbeat during a slow account, cancellation after a read-only fetch and after AI generation, replacement claims, failures and restart recovery. An actual pre-v4 SQLite schema is reconstructed and upgraded twice, preserving password hashes, profile data and encrypted AI/Freelancer credentials.

The authenticated status endpoint performs no remote calls and exposes only the current account's automation metrics. Cross-tab account mismatch is rejected before loading status. Pending counts exclude demo, previous-account/environment, closed, low-score and already-drafted/sent projects; remaining candidates still need final eligibility checks. A live worker heartbeat does not prove a healthy Freelancer poll. A failed latest refresh retains the previous success timestamp while showing the failure separately.

Settings regression fixes preserve edits typed while Save is pending, leave unsaved AI fields intact during basic saves and prevent Save/Restore overlap from re-enabling automation. The original dark/purple theme remains, with responsive live-status cards and clearer narrow-screen status rows. No new general-purpose chatbot was added; the committed v3 app-help feature remains unchanged.

Actual Chromium passed **13 v4 flows** using real local endpoints and synthetic data: status lifecycle, honest scan timestamps, next-refresh countdown, scan leases, API cooldown, failed latest poll, Overview-only non-overlapping polling, settings draft preservation, heartbeat expiry and stale-response/logout privacy. Overview and Settings fit 1440px, 390px and 360px viewports. No JavaScript/CSP errors, external requests or provider calls occurred; expected anonymous `/api/me` 401 responses were the only HTTP errors.

An isolated **PostgreSQL 17 upgrade smoke passed**: the committed v3 schema was initialized, current v4 source added the three connection columns and heartbeat table, and the original encrypted token remained unchanged and decryptable. The worker persisted its heartbeat and scan lease during a controlled scan, ingested a project and reported the correct account status/pending count. A fresh process initialized twice and preserved the token, successful-scan timestamp and heartbeat; persisted API cooldown appeared correctly. The test used cached containers with current source, synthetic credentials and controlled external APIs. Temporary containers, network, database and generated configuration were cleaned up; a new production image was not built.

External API protocols are tested with controlled providers. No supplied live AI key was validated, no real bid/payment was sent and no production website was deployed. Live OAuth permissions, account/bid balance, provider secrets/models and public HTTPS require target-server acceptance. Account restriction cannot be ruled out by app code or request intervals; the permission and platform-limit guidance below still applies. Other Docker results under earlier headings describe those earlier revisions.

## v3 release — 30 October 2026

Current release verification: **370 tests passed plus 26 subtests**, 52.81 seconds; JavaScript syntax, diff checks and the existing repository Ruff check pass. One upstream Starlette TestClient deprecation warning remains. The results below under older patch headings describe earlier revisions.

The v3 feature release adds validated project/client/currency filters, selected search fields, manual-review keywords, keyword/default templates, timezone-aware working hours, FIFO/score ordering, persisted dispatch spacing and credential-free configuration backup/restore. New-account defaults are 10 bids/day and 3/hour; existing explicit limits remain. SQLite/PostgreSQL receive additive user/connection columns, preserving accounts, passwords and encrypted credentials. Portable timezone data is pinned, with a regression that disables system timezone files and verifies Karachi working hours using the packaged data.

API safety regressions cover concurrency, actual dispatch timing, a queue behind 105 consumed projects, worker draining between scans, `401/403` pauses, unknown mutation results and `429` seconds/HTTP-date waits. Provider waits persist after restart, disconnect, first-connection failure and token replacement. No uncertain mutation is automatically retried. Manual bidding must still meet explicit filters and the configured profile-skill match; working hours and manual-review keywords constrain automation.

Preference restore is tenant-scoped, CSRF-protected, size-bounded and atomic. It excludes credentials, roles, subscriptions and billing; auto-bidding and auto-chat are always OFF afterwards. SQL work runs outside the ASGI event loop. Malformed/extra fields do not echo file contents or inputs, and import cannot erase API pauses or race an in-flight bid.

Help Chat uses the existing provider transports with a trusted support system role. It transmits only the redacted question/recent history and a limited own-account snapshot. It has no action tools and stores no conversation in the app database. Live answers identify the actual Gemini/OpenAI provider; inactive accounts or provider failures receive explicit local guidance. Known credentials/common token formats are redacted before cloud I/O and in replies. Per-account quota controls apply; cross-tab expected-account checks reject stale chat state before any provider request. Tests use controlled providers, not the user's keys.

Actual Chromium passed **9 advanced-settings flows** and **6 Help Chat/layout flows**: real API save/reload/template drafts, local backup download, explicit restore and key preservation, template priority changes, API pause controls, real local help fallback, controlled Gemini metadata, inert XSS text, pending-response/clear/logout privacy and 390px layout. There were no JavaScript/CSP errors or external requests; only expected anonymous `/api/me` 401 checks. The original dark/purple theme is retained; mobile body/document widths match the viewport exactly.

The [public FABB feature listing](https://www.freelancerautobiddingbot.com/) informed workflow selection; its code/assets and safety/earnings claims were not adopted. [Freelancer User Agreement section 33](https://www.freelancer.com/about/terms) addresses express written permission for automated access, including API use; [API Terms section 4.9](https://www.freelancer.com/about/apiterms) prohibits bypassing limits. App intervals are not platform-approved limits or a no-restriction guarantee. Actual marketplace bid/wallet balance, OAuth permissions and live sandbox bid acceptance still require target-account verification. Docker/PostgreSQL deployment checks described later were performed on the earlier v2 revision, not rebuilt for v3; no new production rollout is claimed.

Second review and subsequent login/API-diagnostic patches completed 29–30 October 2026. Supersedes the earlier reviewed ZIP. This release repairs the uploaded project; the accompanying pasted Gemini response was reference material, not code automatically trusted or installed. The subsequently uploaded 16 MB `BidPilot_AI_All_Freelancer_Bids_Pro_FIXED.zip` contains only a Python venv and requirements.txt, with no application source; fixes therefore use the complete reviewed source already in this repository. This is a tested revision, not a guarantee that every possible defect or attack has been eliminated.

## Changes made

- Fixed the missing live-bids import and Freelancer bid response ID handling. Corrected own-bid API filtering, numeric account IDs, message encoding, malformed response handling and safe API diagnostics.
- Gemini credentials travel in headers, not URL query strings. OpenAI/Gemini model configuration is explicit. Provider HTTP errors are distinguished from local template fallback. An unavailable API is not reported as successful cloud AI.
- User keys remain encrypted. Another administrator's personal keys are never borrowed. Explicitly configured shared server keys use server-selected models. Failed-provider circuit breakers and shared per-account AI request limits reduce repeated failures and uncontrolled usage.
- Login now uses revocable server-side sessions, hashed session/CSRF tokens, persistent throttling and consistent CSRF/origin checks. Logout revokes the session. Disabling an account revokes its sessions. Password hashes use salted PBKDF2, with legacy compatibility. Validation errors no longer echo secret inputs.
- Signup creates an unpaid member, never an administrator or free founder. There are two free founder slots, including the owner; setup can provision the second account privately.
- Starter costs $10 per 30 days with 150 attempted/reserved bids; Pro costs $40 per 30 days without an app monthly quota. Both retain hourly/daily and marketplace restrictions. Subscription expiry and limits apply to automatic and manual live bids. Same-tier renewal extends expiry without resetting the current quota window.
- Payment references are requests for manual review. Fixed package prices, database reference uniqueness and atomic one-time approval prevent common activation/replay bugs. Unconfigured payment destinations are rejected. There are no fake payment account defaults.
- Bid reservations serialize per account across API/worker processes. Duplicate/uncertain sends stay blocked; dispatch claims cannot be reused. Credential snapshots and connection-change checks prevent a reserved bid being redirected to a different account/environment. One Freelancer account per environment is enforced in the database.
- Price suggestions respect project bounds and user caps. Risk terms, excluded skills, closed/unknown status and invalid budgets stop automation. Currency caps use the listing currency; no exchange conversion is assumed.
- Scan claims prevent overlapping rapid scans; stale results are discarded after account changes. Chat threads are reserved before sending; uncertain sends are not automatically repeated. Automation and chat are opt-in.
- Frontend remote data is escaped, payment URLs validated, mobile layout repaired and local proposal templates labeled. Added signup and accurate billing controls.
- Database initialization is serialized across processes. Additive migrations fail visibly. PostgreSQL URLs select the installed psycopg2 driver, fixing a production-only startup failure found during real-container testing.
- Added a non-root Docker image, persistent PostgreSQL/Caddy Compose configuration, private configuration generator, backup instructions and pinned Python 3.12 dependencies.

## Additional defects repaired in the second review

- Shared-browser logout and account switching now clear previous user/admin data, reset navigation and discard stale network responses. Cross-tab cookie account changes and admin role loss trigger a clean session reload. These prevent old admin lists from remaining visible in another account's interface.
- Concurrent proposal requests no longer overwrite manual edits. Bid preparation cannot mix two projects across asynchronous requests. Pending or uncertain server results disable immediate retries, and approved subscriptions refresh without requiring logout.
- Successful API diagnostics clear an old failure cooldown; subsequent proposals can actually use the recovered provider. Configuration changes invalidate relevant failure state. Diagnostics do not echo arbitrary remote text.
- Truncated or safety-filtered provider completions are rejected instead of treated as finished proposals. Malformed scan/bid envelopes and invalid remote account/project IDs produce explicit errors.
- Remote missing currency/type stays unknown, with live bidding disabled until a rescan supplies known units. Own-project bidding is blocked. Untagged legacy projects must be rescanned before dispatch.
- Slow AI generation no longer holds a database write lock. Account/subscription, automation preferences, filters, bid caps and connection identity are checked again before a bid is dispatched. Pausing automation during generation stops the bid; known cancellations are distinguished from uncertain external results.
- Chat checks current opt-in/account state after generation and records actual delivery state. Failed/uncertain deliveries are no longer counted as successful greetings. Legacy chat rows migrate to unknown status.
- Skill matching supports Go, R, C# and C++; whole-term matching avoids false matches such as React in reaction. Risk phrase matching handles spacing/newlines/zero-width text. Nonfinite and boolean numeric inputs are rejected by scoring. Exact decimal cent bounds keep suggested prices within valid ranges.
- SQLite database and journal files are private (0600 on Unix), including when the process has a permissive umask. Symlink targets are rejected.
- The health endpoint now checks database availability. Docker healthchecks honor the configured PORT. Backup filenames are unique, setup email lengths match database constraints, and Caddy configuration was validated in an isolated container.

## Verification completed

| Check | Result |
| --- | --- |
| Full automated suite | **222 passed plus 19 subtests**, 29.59 seconds on the 30 October patch run |
| External API protocol/security cases | Controlled mocks; no real credentials or bids |
| Chromium desktop/mobile smoke | 13 flows passed (including same-browser owner→member privacy and post-approval plan refresh): owner login, demo proposal/draft, settings, billing, member creation, logout, mobile signup, unpaid local demo, $40 receipt submission, owner approval |
| Browser errors | No JavaScript page errors, CSP violations, unexpected API failures or external requests; only expected anonymous `/api/me` 401 responses |
| Docker build | Previous reviewed v2 passed with pinned dependencies and temporary build-only proxy CA mount; not repeated for the current provider/default/frontend patch |
| Actual PostgreSQL 17 + production app | Passed startup, login, Secure/HttpOnly cookies, CSRF rejection, revoked logout, registration, member/admin isolation, fixed prices and refusal of unconfigured payment methods |
| Actual PostgreSQL concurrency/migrations | Distinct-project hourly reservation cap, one-time payment approval, renewal/anchor preservation, legacy chat-status migration and duplicate-account constraint passed |
| Worker container | Startup passed; automatic outbound actions remained off in this deployment smoke |
| Container health | Custom PORT=18000 healthcheck passed; database-outage health endpoint returns 503; Caddy configuration validates without issuing certificates |
| Image isolation | Previous reviewed v2 image: UID 10001, source hashes matched that revision, no `.env` or session CA persisted |
| Other checks | Compose configuration, JavaScript/Python syntax, backup shell syntax, `pip check` passed |
| Shared-credential scan | Supplied credentials absent from release source |

The suite emits one upstream TestClient deprecation warning; tests pass. Docker smoke containers/network were removed. No domain was published and no customer data was used.

## Latest local-login and API-diagnostic patch

The user's real browser exposed an address mismatch: setup configured PUBLIC_URL as 127.0.0.1 while instructions opened localhost. A regression reproduced the plaintext403 before the fix. The patch supports exact same-origin loopback aliases only in development/test with matching scheme/port, and keeps strict production origin rules. It rejects spoofed hostnames, userinfo, paths/query/fragment, malformed ports/controls, duplicate origins and cross-site Fetch metadata. Denials now return JSON and the normal CSP/cache/frame headers.

Gemini404 saved-key diagnostics can list generation-supported models using only read-only official GET calls; up to3 bounded pages, no redirects, no automatic model change or alternative generation. OpenAI401 is reported as credential rejection. The UI distinguishes configured from verified, blocks tests of unsaved changes, escapes all model-list content, and gives manual Save→Test instructions.

Verification at the 29 October patch: 217 application tests plus12 subtests passed; 340 existing repository tests and Ruff checks pass separately. Actual Chromium checked localhost login while configured127, authenticated CSRF mutation, unsaved-test blocking and mocked Gemini404/OpenAI401 rendering. No unexpected browser errors or external requests. Deployment Docker/PostgreSQL/Caddy results above describe the prior reviewed v2 deployment, not a new production rollout. Neither the user's keys nor their machine were accessed by these tests.

## Subsequent founder-bootstrap startup repair

A real restart failed when SECOND_FOUNDER_EMAIL referred to a member whose plan was paid. Optional provisioning now skips that conflict with a non-sensitive warning rather than taking down the app. It never replaces an existing account's password, encrypted tokens, role or subscription, and never silently promotes a paid member to free/admin. Optional invalid settings/full slots are skipped; mandatory first-owner setup remains fail-closed. Passwords are decoded only if that account actually needs creation. A savepoint isolates email-insert races, and new optional founders share the admin owner-row lock before the two-seat count.

Regression tests restart the real app lifespan with the conflicting paid member, verify health and both logins, preserve account/password/API/Freelancer token snapshots across restarts, check mandatory-owner failures and optional invalid configuration, and race Admin seat assignment against optional bootstrap. Explicit test mode skips local dotenv files; fixtures clear inherited bootstrap-password encodings to avoid real-secret precedence. Latest full application run:178 tests plus12 subtests passed. No new migration or dependency change is required by this repair; the existing database and .env must be retained.

## Safe provider-reason diagnostics

Repeated Gemini404 results after changing to another listed model did not confirm a missing-model root cause. Diagnostic errors now retain actual HTTP status plus allowlisted Google canonical status/ErrorInfo reason, OpenAI type/code, fixed failure classification and bounded response-format enum. Non-success bodies are inspected up to64KiB; remote raw text, credentials, metadata/IDs, headers, prompts and URLs are never reflected. A catalog-listed selected model no longer produces switch-model advice. Catalog display is collapsed and limited to10 visible entries; no model is chosen automatically. OpenAI401 remains credential rejection.

Eight additional provider regressions cover known project/service/auth restrictions, unconfirmed listed-model404, malicious response bodies, non-JSON/oversized responses and preservation of HTTP status. Frontend regression coverage checks escaped provider codes and body-format values. Latest application run:186 tests plus12 subtests passed; Node syntax, Python compilation and diff checks passed. No user's live key was accessed or transmitted by these tests. This diagnostic patch does not itself validate or repair an invalid credential or grant provider access.

## Saved-key transport verification and standalone checker

Official OpenAI documentation distinguishes the key record ID from the secret value, even where the record's project access is active. The UI now gives a possible-ID explanation only after the official OpenAI host returned401 with an explicit invalid-key reason and the saved value resembles a record ID; prefix alone never blocks a key, and custom providers receive no such assumption.

A full endpoint regression saves an identifier-like value and observes its controlled401, then saves a replacement secret, verifies encrypted storage/decryption and the exact outgoing Bearer header at the official chat endpoint, and observes a controlled success. This verifies value preservation, not real-key validity. Five API hint regressions plus the full saved-key test pass.

A new independent `check_openai_key.py` uses only standard-library HTTPS, fixed official model-list GET, hidden input, no app/config/database imports, no credential persistence, bounded responses and disabled redirects.25 mocked regressions cover auth transport, unexpected/oversized responses,401/403 distinctions, secret redaction, hidden-input failure and no app-state access. At that patch, the full application suite had217 tests plus12 subtests passed. User live authentication still requires private testing on their own machine; no actual supplied secret was used here.

## Current-model compatibility and save/test races — 30 October

Current full application suite: **222 passed plus 19 subtests**, 29.59 seconds. JavaScript syntax and git diff checks passed. One upstream TestClient deprecation warning remains.

Google's [model access policy](https://ai.google.dev/gemini-api/docs/deprecations) now limits Gemini 2.5 access to existing users. This is a possible explanation for a catalog-listed model returning 404, not proof of the user's account configuration. New accounts and setup files default to `gemini-3.5-flash-lite`; existing saved choices and server environment overrides are preserved. Failed 2.5 generation tests recommend that exact replacement only when the read-only catalog reports generateContent support and the error classification does not identify a credential failure. The personal-key UI action fills the model field only; explicit Save and Test remain necessary. Shared-key users receive operator configuration guidance. No alternative generation request is sent automatically.

Gemini requests omit the optional temperature parameter, as required by the [current generation migration guidance](https://ai.google.dev/gemini-api/docs/generate-content/whats-new-gemini-3.6). Legacy 2.5 Flash retains its supported zero-thinking budget; the current Flash-Lite model uses its default configuration. Protocol regressions check the exact path, header and supported payload.

Three reproduced frontend defects are repaired: an in-flight Save no longer erases a newer key typed before the response; Save/Test cannot overlap or be re-enabled by background status polling; and results from a configuration edited during a pending Test are discarded. Configuration snapshots, revision checks and account-epoch guards preserve newer edits and prevent old responses from applying to a new session. The Node harness covers concurrent submissions, edits/reverts, saved-state changes and explicit recommendation controls. A database regression verifies new defaults while preserving existing encrypted keys, selected models and server model overrides across restarts.

Actual Chromium passed five current-revision flows covering new defaults, explicit model selection and Save, a controlled successful result, pending-save input preservation, polling busy states and stale-result rejection. There were no JavaScript/CSP errors or external requests; the only API error was the expected initial anonymous `/api/me` 401. AI diagnostic responses were controlled browser routes, not live provider authentication. Existing Docker/PostgreSQL results above remain prior-revision deployment checks.

OpenAI's actual `401 invalid_api_key` still requires a valid full secret. The app cannot recover a secret from its record ID, and these changes do not claim to validate the user's credentials. The supplied 16 MB ZIP contained 1,036 entries consisting only of a venv and requirements.txt; its dependencies were not installed or trusted as application code.

## What still requires your Ubuntu/staging installation

1. **Fresh AI keys:** revoke credentials shared in chat and save replacement credentials privately. The environment proxy blocked the official OpenAI and Google endpoints with CONNECT 403 before authentication; this is not a provider response and says nothing about whether the supplied credentials are valid. No supplied key was transmitted in this review. Run Settings → Test Both AI Keys on your server and check the actual provider/model/billing response.
2. **Freelancer:** no Freelancer OAuth token was supplied. Connect a sandbox token, confirm the identity and test one sandbox bid against a project you control. Verify it appears exactly once. Real OAuth scopes, account restrictions, bid balance and marketplace acceptance were not validated here.
3. **Domain and TLS:** point the domain to Ubuntu, configure private `.env`, then use the included Compose/Caddy instructions. Real DNS, ACME certificates, firewall and server Internet access were not validated here.
4. **Payments:** configure real payment destinations, verify actual settlement outside this app and only then approve receipts. Automatic recurring billing, verified Stripe webhooks, refunds and card handling are not implemented.
5. **Operations:** restore a backup on an isolated database, protect keys/backups and monitor logs. No independent penetration test or load test was performed. Email ownership verification and password-reset email delivery are not implemented; arrange operator-supported recovery.

For an existing database, back it up before upgrade. Legacy paid accounts without verified expiry become inactive until reviewed. Existing duplicate payment references or duplicate same-environment Freelancer connections must be reconciled before migration can succeed. Keep the existing Fernet key to preserve encrypted-token access.

Start with `README_ROMAN_URDU.md` or `README.md`. Keep automation disabled until sandbox verification is complete.
