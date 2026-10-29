# BidPilot AI — second review / updated v2

Second review completed 29 October 2026. Supersedes the earlier reviewed ZIP. This release repairs the uploaded project; the accompanying pasted Gemini response was reference material, not code automatically trusted or installed. This is a tested revision, not a guarantee that every possible defect or attack has been eliminated.

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
| Full automated suite | **125 passed plus 12 subtests**, 21.61 seconds on the final run |
| External API protocol/security cases | Controlled mocks; no real credentials or bids |
| Chromium desktop/mobile smoke | 13 flows passed (including same-browser owner→member privacy and post-approval plan refresh): owner login, demo proposal/draft, settings, billing, member creation, logout, mobile signup, unpaid local demo, $40 receipt submission, owner approval |
| Browser errors | No JavaScript page errors, CSP violations, unexpected API failures or external requests; only expected anonymous `/api/me` 401 responses |
| Docker build | Passed with pinned dependencies and temporary build-only proxy CA mount |
| Actual PostgreSQL 17 + production app | Passed startup, login, Secure/HttpOnly cookies, CSRF rejection, revoked logout, registration, member/admin isolation, fixed prices and refusal of unconfigured payment methods |
| Actual PostgreSQL concurrency/migrations | Distinct-project hourly reservation cap, one-time payment approval, renewal/anchor preservation, legacy chat-status migration and duplicate-account constraint passed |
| Worker container | Startup passed; automatic outbound actions remained off in this deployment smoke |
| Container health | Custom PORT=18000 healthcheck passed; database-outage health endpoint returns 503; Caddy configuration validates without issuing certificates |
| Image isolation | UID 10001; app file hashes matched final source; no `.env` or session CA persisted |
| Other checks | Compose configuration, JavaScript/Python syntax, backup shell syntax, `pip check` passed |
| Shared-credential scan | Supplied credentials absent from release source |

The suite emits one upstream TestClient deprecation warning; tests pass. Docker smoke containers/network were removed. No domain was published and no customer data was used.

## What still requires your Ubuntu/staging installation

1. **Fresh AI keys:** revoke credentials shared in chat and save replacement credentials privately. The environment proxy blocked the official OpenAI and Google endpoints with CONNECT 403 before authentication; this is not a provider response and says nothing about whether the supplied credentials are valid. No supplied key was transmitted in this review. Run Settings → Test Both AI Keys on your server and check the actual provider/model/billing response.
2. **Freelancer:** no Freelancer OAuth token was supplied. Connect a sandbox token, confirm the identity and test one sandbox bid against a project you control. Verify it appears exactly once. Real OAuth scopes, account restrictions, bid balance and marketplace acceptance were not validated here.
3. **Domain and TLS:** point the domain to Ubuntu, configure private `.env`, then use the included Compose/Caddy instructions. Real DNS, ACME certificates, firewall and server Internet access were not validated here.
4. **Payments:** configure real payment destinations, verify actual settlement outside this app and only then approve receipts. Automatic recurring billing, verified Stripe webhooks, refunds and card handling are not implemented.
5. **Operations:** restore a backup on an isolated database, protect keys/backups and monitor logs. No independent penetration test or load test was performed. Email ownership verification and password-reset email delivery are not implemented; arrange operator-supported recovery.

For an existing database, back it up before upgrade. Legacy paid accounts without verified expiry become inactive until reviewed. Existing duplicate payment references or duplicate same-environment Freelancer connections must be reconciled before migration can succeed. Keep the existing Fernet key to preserve encrypted-token access.

Start with `README_ROMAN_URDU.md` or `README.md`. Keep automation disabled until sandbox verification is complete.
