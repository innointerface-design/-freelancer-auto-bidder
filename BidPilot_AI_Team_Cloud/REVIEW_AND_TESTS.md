# BidPilot AI — second review / updated v2

Second review and subsequent login/API-diagnostic patch completed 29 October 2026. Supersedes the earlier reviewed ZIP. This release repairs the uploaded project; the accompanying pasted Gemini response was reference material, not code automatically trusted or installed. This is a tested revision, not a guarantee that every possible defect or attack has been eliminated.

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
| Full automated suite | **217 passed plus 12 subtests**, 48.74 seconds on the latest patch run |
| External API protocol/security cases | Controlled mocks; no real credentials or bids |
| Chromium desktop/mobile smoke | 13 flows passed (including same-browser owner→member privacy and post-approval plan refresh): owner login, demo proposal/draft, settings, billing, member creation, logout, mobile signup, unpaid local demo, $40 receipt submission, owner approval |
| Browser errors | No JavaScript page errors, CSP violations, unexpected API failures or external requests; only expected anonymous `/api/me` 401 responses |
| Docker build | Previous reviewed v2 passed with pinned dependencies and temporary build-only proxy CA mount; not repeated for this HTTP/diagnostic-only patch |
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

Current verification: 217 application tests plus12 subtests pass; 340 existing repository tests and Ruff checks pass separately. Actual Chromium checked localhost login while configured127, authenticated CSRF mutation, unsaved-test blocking and mocked Gemini404/OpenAI401 rendering. No unexpected browser errors or external requests. Deployment Docker/PostgreSQL/Caddy results above describe the prior reviewed v2 deployment, not a new production rollout. Neither the user's keys nor their machine were accessed by these tests.

## Subsequent founder-bootstrap startup repair

A real restart failed when SECOND_FOUNDER_EMAIL referred to a member whose plan was paid. Optional provisioning now skips that conflict with a non-sensitive warning rather than taking down the app. It never replaces an existing account's password, encrypted tokens, role or subscription, and never silently promotes a paid member to free/admin. Optional invalid settings/full slots are skipped; mandatory first-owner setup remains fail-closed. Passwords are decoded only if that account actually needs creation. A savepoint isolates email-insert races, and new optional founders share the admin owner-row lock before the two-seat count.

Regression tests restart the real app lifespan with the conflicting paid member, verify health and both logins, preserve account/password/API/Freelancer token snapshots across restarts, check mandatory-owner failures and optional invalid configuration, and race Admin seat assignment against optional bootstrap. Explicit test mode skips local dotenv files; fixtures clear inherited bootstrap-password encodings to avoid real-secret precedence. Latest full application run:178 tests plus12 subtests passed. No new migration or dependency change is required by this repair; the existing database and .env must be retained.

## Safe provider-reason diagnostics

Repeated Gemini404 results after changing to another listed model did not confirm a missing-model root cause. Diagnostic errors now retain actual HTTP status plus allowlisted Google canonical status/ErrorInfo reason, OpenAI type/code, fixed failure classification and bounded response-format enum. Non-success bodies are inspected up to64KiB; remote raw text, credentials, metadata/IDs, headers, prompts and URLs are never reflected. A catalog-listed selected model no longer produces switch-model advice. Catalog display is collapsed and limited to10 visible entries; no model is chosen automatically. OpenAI401 remains credential rejection.

Eight additional provider regressions cover known project/service/auth restrictions, unconfirmed listed-model404, malicious response bodies, non-JSON/oversized responses and preservation of HTTP status. Frontend regression coverage checks escaped provider codes and body-format values. Latest application run:186 tests plus12 subtests passed; Node syntax, Python compilation and diff checks passed. No user's live key was accessed or transmitted by these tests. This diagnostic patch does not itself validate or repair an invalid credential or grant provider access.

## Saved-key transport verification and standalone checker

Official OpenAI documentation distinguishes the key record ID from the secret value, even where the record's project access is active. The UI now gives a possible-ID explanation only after the official OpenAI host returned401 with an explicit invalid-key reason and the saved value resembles a record ID; prefix alone never blocks a key, and custom providers receive no such assumption.

A full endpoint regression saves an identifier-like value and observes its controlled401, then saves a replacement secret, verifies encrypted storage/decryption and the exact outgoing Bearer header at the official chat endpoint, and observes a controlled success. This verifies value preservation, not real-key validity. Five API hint regressions plus the full saved-key test pass.

A new independent `check_openai_key.py` uses only standard-library HTTPS, fixed official model-list GET, hidden input, no app/config/database imports, no credential persistence, bounded responses and disabled redirects.25 mocked regressions cover auth transport, unexpected/oversized responses,401/403 distinctions, secret redaction, hidden-input failure and no app-state access. Current full application suite:217 tests plus12 subtests passed. User live authentication still requires private testing on their own machine; no actual supplied secret was used here.

## What still requires your Ubuntu/staging installation

1. **Fresh AI keys:** revoke credentials shared in chat and save replacement credentials privately. The environment proxy blocked the official OpenAI and Google endpoints with CONNECT 403 before authentication; this is not a provider response and says nothing about whether the supplied credentials are valid. No supplied key was transmitted in this review. Run Settings → Test Both AI Keys on your server and check the actual provider/model/billing response.
2. **Freelancer:** no Freelancer OAuth token was supplied. Connect a sandbox token, confirm the identity and test one sandbox bid against a project you control. Verify it appears exactly once. Real OAuth scopes, account restrictions, bid balance and marketplace acceptance were not validated here.
3. **Domain and TLS:** point the domain to Ubuntu, configure private `.env`, then use the included Compose/Caddy instructions. Real DNS, ACME certificates, firewall and server Internet access were not validated here.
4. **Payments:** configure real payment destinations, verify actual settlement outside this app and only then approve receipts. Automatic recurring billing, verified Stripe webhooks, refunds and card handling are not implemented.
5. **Operations:** restore a backup on an isolated database, protect keys/backups and monitor logs. No independent penetration test or load test was performed. Email ownership verification and password-reset email delivery are not implemented; arrange operator-supported recovery.

For an existing database, back it up before upgrade. Legacy paid accounts without verified expiry become inactive until reviewed. Existing duplicate payment references or duplicate same-environment Freelancer connections must be reconciled before migration can succeed. Keep the existing Fernet key to preserve encrypted-token access.

Start with `README_ROMAN_URDU.md` or `README.md`. Keep automation disabled until sandbox verification is complete.
