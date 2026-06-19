# Planet Stopper — Production Deployment Runbook

> Generic, secrets-free runbook for deploying Planet Stopper to a Linux VPS/droplet.
> All site-specific values (IP addresses, domain names, SSH key paths) are represented
> by placeholders — fill them in for your environment.

---

## Architecture Overview

```
                     Internet
                         |
              [Cloud Firewall: 22/80/443 only]
                         |
                    [Linux VPS]
                         |
         +---------------+---------------+
         |                               |
   [Caddy — TLS termination]    [systemd daemon units]
   port 443 (HTTPS)             planetstopper.service (Flask, :8090)
   Let's Encrypt cert            planetstopper-prism.service + .timer (nightly council)
         |
   reverse proxy → localhost:8090
```

**Key decisions:**

- The daemon and council run as a **non-root service user** (e.g. `planetstopper`) from `/opt/planetstopper`. Root cannot run headless `claude -p` (`--dangerously-skip-permissions` is blocked for root).
- The Flask app binds to `localhost:8090` only; the cloud firewall blocks direct external access to port 8090. All public traffic enters on 443 (HTTPS) via Caddy.
- `LIVE_EXECUTION='False'` on the droplet — the droplet is **shadow/advisory only**. No live trading ever.
- The nightly Market Prism council authenticates via the operator's **Claude subscription** (`CLAUDE_CODE_OAUTH_TOKEN`), not the metered `ANTHROPIC_API_KEY`. See §Nightly Council below.

---

## Prerequisites

- A Linux VPS (Ubuntu 22.04 LTS or later recommended). 2 vCPU / 4 GB RAM minimum.
- A domain name pointing at the VPS's public IP (for Let's Encrypt TLS).
- SSH access to the VPS as root (for initial setup; the daemon runs as a non-root user).
- A Claude Code subscription with an OAuth token (`claude setup-token`).
- Composer.trade API credentials, Alpaca API credentials, Discord webhook URL, Anthropic API key (for the on-demand Flask advisor endpoints).

---

## Step 1 — Create the service user

```bash
# On the VPS as root:
useradd --system --create-home --home-dir /opt/planetstopper \
        --shell /bin/bash planetstopper
```

The `planetstopper` user owns all application files. The Flask daemon and nightly council run as this user.

---

## Step 2 — Clone and set up the project

```bash
# As root:
cd /opt/planetstopper
git clone <repository-url> .
chown -R planetstopper:planetstopper /opt/planetstopper

# Switch to the service user for the rest of setup:
su - planetstopper

# Create the virtual environment:
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

---

## Step 3 — Configure `.env`

Copy `.env.example` to `.env` and fill in your credentials:

```bash
cp .env.example .env
chmod 600 .env    # only planetstopper can read it
```

Edit `.env` with your actual values. The critical deployment settings are:

```
LIVE_EXECUTION='False'          # NEVER set True on the droplet — advisory only
SESSION_COOKIE_SECURE=true      # required for TLS deployment
TRUST_PROXY=1                   # Caddy terminates TLS and forwards via X-Forwarded-For
DISABLE_DAEMON_LENS_PIPELINE=1  # silence daemon 03:00 slot; council is the sole nightly producer
```

See `.env.example` for the full list of required and optional keys.

> The `.env` file MUST NOT be committed to source control. It is listed in `.gitignore`.

---

## Step 4 — Install Caddy (reverse proxy + TLS)

```bash
# As root:
apt install -y debian-keyring debian-archive-keyring apt-transport-https
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' \
  | gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' \
  | tee /etc/apt/sources.list.d/caddy-stable.list
apt update && apt install caddy
```

Create `/etc/caddy/Caddyfile`:

```
your-domain.example {
    reverse_proxy localhost:8090
}
```

Replace `your-domain.example` with your actual domain. Caddy will automatically obtain and renew a Let's Encrypt certificate.

```bash
systemctl enable --now caddy
```

---

## Step 5 — Create the Flask daemon systemd unit

Create `/etc/systemd/system/planetstopper.service`:

```ini
[Unit]
Description=Planet Stopper Flask daemon
After=network.target

[Service]
Type=simple
User=planetstopper
WorkingDirectory=/opt/planetstopper
EnvironmentFile=/opt/planetstopper/.env
ExecStart=/opt/planetstopper/.venv/bin/python app.py
Restart=on-failure
RestartSec=10
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
```

```bash
systemctl daemon-reload
systemctl enable --now planetstopper
```

The daemon binds to `localhost:8090` by default (overridable via the `PORT` env var).

Verify it is healthy:

```bash
systemctl status planetstopper
journalctl -u planetstopper -f
```

---

## Step 6 — Cloud firewall

Configure your cloud provider's firewall to allow inbound TCP on **22, 80, 443 only**. Block port 8090 from external access — it is localhost-only behind Caddy.

---

## Step 7 — Dashboard password setup

Before the daemon is accessible, set `DASHBOARD_PASSWORD_HASH` in `.env`.

Generate a werkzeug hash (run this on any machine with werkzeug installed):

```python
from werkzeug.security import generate_password_hash
print(generate_password_hash("your-password-here"))
# Output: pbkdf2:sha256:...
```

Set in `.env`:

```
DASHBOARD_PASSWORD_HASH=pbkdf2:sha256:<your-generated-hash>
SECRET_KEY=<a-long-random-string>
```

Restart the daemon after editing `.env`:

```bash
systemctl restart planetstopper
```

---

## Step 8 — Nightly Market Prism council (systemd oneshot + timer)

The council runs `prism_scheduler.py` nightly at 03:00 America/New_York as the sole `MARKET_PRISM` producer. It authenticates via the operator's Claude subscription, NOT the metered API key.

### 8a — Set up the Claude OAuth token

On the VPS as the `planetstopper` user, run:

```bash
claude setup-token
```

This stores `CLAUDE_CODE_OAUTH_TOKEN` in the user's Claude config. Alternatively, store it in a root-600 `EnvironmentFile` that the council systemd unit injects.

Create `/etc/planetstopper/council-env` (root-owned, 600):

```
CLAUDE_CODE_OAUTH_TOKEN=<your-oauth-token>
```

```bash
chmod 600 /etc/planetstopper/council-env
```

### 8b — SAFE TRANSITION ORDER

**Set `DISABLE_DAEMON_LENS_PIPELINE=1` in `.env` and restart the daemon BEFORE registering the timer.** The daemon's 03:00 slot and the council timer both write a `MARKET_PRISM` row and have no mutual idempotency guard. Running both on the same night produces two rows; the Overview tab reads the most-recent, silently overwriting the council's considered verdict with the simpler pipeline output.

```bash
# In .env:  DISABLE_DAEMON_LENS_PIPELINE=1
systemctl restart planetstopper
# Verify the INFO log fires at 03:00 with no MARKET_PRISM DB write, then proceed:
```

### 8c — Create the oneshot service and timer units

`/etc/systemd/system/planetstopper-prism.service`:

```ini
[Unit]
Description=Planet Stopper Market Prism nightly council
After=network.target planetstopper.service

[Service]
Type=oneshot
User=planetstopper
WorkingDirectory=/opt/planetstopper
EnvironmentFile=/opt/planetstopper/.env
EnvironmentFile=/etc/planetstopper/council-env
ExecStart=/opt/planetstopper/.venv/bin/python prism_scheduler.py
StandardOutput=journal
StandardError=journal
```

`/etc/systemd/system/planetstopper-prism.timer`:

```ini
[Unit]
Description=Planet Stopper Market Prism — nightly at 03:00 ET

[Timer]
OnCalendar=*-*-* 03:00:00 America/New_York
Persistent=true

[Install]
WantedBy=timers.target
```

```bash
systemctl daemon-reload
systemctl enable --now planetstopper-prism.timer
```

`Persistent=true` ensures a missed run (e.g., VPS rebooted at 03:00) fires as soon as the system is back up.

### 8d — Verify the council

Manually trigger a test run:

```bash
systemctl start planetstopper-prism
journalctl -u planetstopper-prism -f
```

Confirm exactly one `MARKET_PRISM` row appears in the DB and the Overview tab renders it.

---

## Step 9 — No-two-live-daemons cutover rule

**Never run two live daemons at once.** If migrating from a local machine to the droplet:

1. Verify the droplet daemon is healthy and serving correctly.
2. Stop the local daemon (`Ctrl+C` or `systemctl stop planetstopper` on the local machine).
3. The droplet becomes the sole live engine.

A simultaneous local + droplet daemon would produce duplicate Alpaca fetches and duplicate DB writes.

---

## Operational Notes

### Viewing logs

```bash
journalctl -u planetstopper -n 200 --no-pager   # daemon logs
journalctl -u planetstopper-prism -n 50          # council logs
```

### Restarting after .env changes

```bash
systemctl restart planetstopper
```

### Updating the codebase

```bash
su - planetstopper
cd /opt/planetstopper
git pull origin main
source .venv/bin/activate
pip install -r requirements.txt
exit
systemctl restart planetstopper
```

### Database backups

The state DB (`alphabot_state.db`) and optimization DB (`alphabot_studies.db`) live in `/opt/planetstopper/`. Back them up before any schema migration or major update.

```bash
cp /opt/planetstopper/alphabot_state.db /opt/planetstopper/alphabot_state.db.bak-$(date +%Y%m%d)
```

### SSH access

```bash
ssh -i ~/.ssh/<deploy_key> planetstopper@<DROPLET_IP>
```

Replace `<deploy_key>` with your SSH private key name and `<DROPLET_IP>` with the VPS public IP.

---

## Security Checklist (before go-live)

- [ ] `LIVE_EXECUTION='False'` confirmed in `.env`
- [ ] `DASHBOARD_PASSWORD_HASH` set (werkzeug format)
- [ ] `SECRET_KEY` set to a long random string
- [ ] `SESSION_COOKIE_SECURE=true` set
- [ ] `TRUST_PROXY=1` set (Caddy proxies `X-Forwarded-For`)
- [ ] TLS active (Caddy shows green cert for your domain)
- [ ] Cloud firewall: only 22/80/443 open; port 8090 blocked from external
- [ ] `.env` mode 600, owned by `planetstopper`
- [ ] `/etc/planetstopper/council-env` mode 600, owned by root
- [ ] No real credentials in any tracked file (check `git diff HEAD`)
- [ ] `DISABLE_DAEMON_LENS_PIPELINE=1` set before enabling the council timer
