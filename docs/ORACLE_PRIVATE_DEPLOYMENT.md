# BidPilot private Oracle deployment (no paid domain)

This is an **optional, private, tailnet-only** deployment of the existing BidPilot application. It is **not deployed yet**. It requires an Oracle Cloud VM created and owned by the account holder and Tailscale installed on each device that should access BidPilot.

The URL is HTTPS, typically `https://bidpilot-private.<your-tailnet>.ts.net`. It is NOT a public website; authorized Tailscale devices can access it, and the existing BidPilot admin login is still required. Do not use `tailscale funnel`.

> Note: Oracle Always Free VM availability is not guaranteed. Verify the VM shape, disk, public IP, and all other usage are inside your account's current Always Free allowance. Tailscale's free Personal plan has eligibility limits (including personal/non-commercial usage). Verify that the plan fits your intended use; use an appropriate paid plan if it is business use.

## Before starting

- Finish creating your own Oracle Cloud account, identity/card checks, and an **Always Free eligible** Ubuntu 24.04 VM. Recommended for evaluation: VM.Standard.A1.Flex with 1 OCPU / 4 GB RAM (subject to free eligibility and home-region capacity).
- Retain the SSH private key **only** on your device. Never send it, the admin password/hash, OAuth token, or payment information to a chat or GitHub.
- Only permit the required SSH administrative access in the Oracle network security rules. **Do not expose web ports 8000, 80, or 443**. You do not need public web ports for Tailscale Serve.
- The existing GitHub repository is public: secrets and the local SQLite database must **not** be committed.
- Rotate any password that was visible in an earlier screenshot.

## On the VM, after connecting through SSH

Example connection from your own computer (replace host and key path):

```bash
ssh -i /path/to/your-PRIVATE-key ubuntu@YOUR_ORACLE_VM_PUBLIC_IP
```

Install **Docker Engine plus Docker Compose plugin v2.30 or later** using the vendor's documented Ubuntu steps:
https://docs.docker.com/engine/install/ubuntu/

Install **Tailscale** on the VM using the official Linux installation guide:
https://tailscale.com/docs/install/linux

Sign into your own tailnet (authorization link will require your action):

```bash
sudo tailscale up --hostname=bidpilot-private
tailscale status
```

In the Tailscale admin console, check the machine's **exact full MagicDNS name** (for example, `bidpilot-private.example-tailnet.ts.net`). Enable HTTPS certificates in your tailnet when prompted by Tailscale Serve. Install and sign in to Tailscale on every laptop/mobile that should privately open BidPilot.

Install Git and clone your already-created repository:

```bash
sudo apt-get update
sudo apt-get install -y git
git clone https://github.com/innointerface-design/-freelancer-auto-bidder.git
cd -- -freelancer-auto-bidder
```

**If this deployment configuration is still only on a pull-request branch**, check it out before continuing:

```bash
git fetch origin bidpilot-private-oracle-tailnet-setup
git checkout bidpilot-private-oracle-tailnet-setup
```

The app uses a dedicated SQLite database and a separate TOML config. Copy the current example (then review your future budget and search preferences; these are illustrative):

```bash
sudo cp config/example.toml /etc/freelancer-web.toml
sudo chmod 644 /etc/freelancer-web.toml
```

Build the app image and generate a *new* private password hash (do not show the result in screenshots):

```bash
sudo docker build -f deploy/Dockerfile.web -t freelancer-auto-bidder-web:local .
sudo docker run --rm -it freelancer-auto-bidder-web:local freelancer-web hash-password
```

Create `/etc/freelancer-web.env` on the VM using `sudoedit` or an editor of your choice. Do NOT save this file in the git repository. Replace the placeholder hash and hostname with the exact values you obtained:

```dotenv
FREELANCER_ADMIN_USERNAME=admin
FREELANCER_ADMIN_PASSWORD_HASH=PASTE_YOUR_NEW_SCRYPT_HASH_PRIVATELY
FREELANCER_WEB_ALLOWED_HOSTS=localhost,127.0.0.1,bidpilot-private.example-tailnet.ts.net
FREELANCER_WEB_ORIGIN=https://bidpilot-private.example-tailnet.ts.net
FREELANCER_WEB_COOKIE_SECURE=true
FREELANCER_WEB_DEMO_MODE=true
FREELANCER_DRY_RUN=true
FREELANCER_ENABLE_AUTO_BIDDING=false
FREELANCER_AUTO_BID_PERMISSION_VERIFIED=false
FREELANCER_PERMISSION_REFERENCE=
FREELANCER_ENVIRONMENT=production
```

This initially launches the **offline demo**. No real Freelancer API searches or real bids run. To later show real jobs, separately connect a properly authorized OAuth token through the VM's private environment, disable demo mode and restart the app, while keeping dry-run and real bidding disabled. Search access is not permission for unattended automatic bids.

Check file permissions and launch one web worker:

```bash
sudo chmod 600 /etc/freelancer-web.env
sudo docker compose -f deploy/compose.private.yml config --quiet
sudo docker compose -f deploy/compose.private.yml up -d --build
sudo docker compose -f deploy/compose.private.yml ps
```

Confirm local container health. The request includes an explicit Host header because the app validates allowed hostnames:

```bash
curl --fail -H 'Host: bidpilot-private.example-tailnet.ts.net' http://127.0.0.1:8000/healthz
```

Now activate *private* HTTPS proxying through Tailscale:

```bash
sudo tailscale serve --bg 8000
sudo tailscale serve status
```

Tailscale might require interactive approval to enable HTTPS certificates. Complete that approval from the **official Tailscale interface**. It should print an `https://...ts.net` URL that works **only for devices authorized on your tailnet**. Open that URL from a signed-in device and then log in with the BidPilot admin username and password.

## Verify and operate

- Confirm the top dashboard banner says **Demo workspace**.
- Confirm **Job monitoring OFF**, **Auto Bidder OFF**, and real bid attempts **0** before testing.
- Check `sudo docker compose -f deploy/compose.private.yml logs --tail 100 web` for server errors; do not post full logs if they could contain secrets.
- Run `sudo tailscale serve status` to verify the proxy is private. Never run `tailscale funnel` for this dashboard.
- Check that the Oracle ingress rules do not permit public web access, and that Docker publishes only `127.0.0.1:8000:8000`.
- Docker has `restart: unless-stopped`; the service starts automatically after a reboot when Docker is enabled. Tailscale Serve background configuration also persists with its daemon; verify after reboot.
- To stop the application: `sudo docker compose -f deploy/compose.private.yml down` (do not use `down -v`, which deletes the named data volume).
- To turn off private website access without stopping the app: `sudo tailscale serve off`.
- Back up the persistent Docker `bidder_data` volume with an SQLite-consistent backup or while the app is stopped, then test a restore in a separate environment. Do not delete or overwrite the existing local WSL data.
- **Existing WSL settings/drafts are not transferred by a Git clone**. Demo databases, private secrets, local TOML changes and saved drafts must be migrated separately and carefully if required.

## When account provisioning is unavailable

Without your own Oracle instance, this repository change does not allocate a server, create a domain, or produce a working link. Cloud account registration and payments/identity confirmations must be completed by the account owner. If Always Free compute is unavailable, stop here rather than silently choosing a billed VM.

References:
- https://docs.oracle.com/en-us/iaas/Content/FreeTier/freetier_topic-Always_Free_Resources.htm
- https://tailscale.com/docs/features/tailscale-serve
- https://tailscale.com/docs/reference/tailscale-cli/serve
- https://tailscale.com/pricing
- https://docs.docker.com/engine/install/ubuntu/
