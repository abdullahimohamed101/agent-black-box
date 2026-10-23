# Hosting guide: Agent BlackBox on one small server

This guide takes you from nothing to Agent BlackBox at `https://abb.<your-domain>` on one rented server: automatic HTTPS, sign-in with
Google for the people you invite, encrypted nightly backups at a second provider, and monitoring that includes disk and memory. It is the
owner-facing end to end; the design is [ADR-178](decisions/ADR-178-private-hosting-topology.md), the files are under
[`deploy/hosting/`](../deploy/hosting/README.md), and the day-to-day procedures are in the
[hosting operations runbook](runbooks/hosting-operations.md). The shorter notes on running the stack with your own proxy are in
[deployment](deployment.md).

## What has and has not been checked

Read this before you start, because the guide would otherwise sound more certain than it is.

- **VERIFIED locally (a command was run and its output read):** the production compose overlay renders with no published ports; the whole proxy chain
  (headers, `ABB_TRUST_PROXY`, HSTS, `__Host-` cookies, nonce CSP, event streams, body limit, flood limits, `/metrics` not public) behind a real
  Caddy using Caddy's own local certificate authority (`make hosting-e2e`); the backup, the off-site copy and the restore drill against a local
  stand-in for the second provider (`make hosting-backup-e2e`); the upgrade and automatic rollback (`make hosting-upgrade-e2e`); the monitoring
  rules with unit tests (`make hosting-obs-e2e`); and the server bootstrap inside a throwaway Ubuntu 24.04 container (`make hosting-bootstrap-e2e`).
- **UNVERIFIED (env):** everything that needs a real server, a real domain or a real account. That is: Let's Encrypt issuing a certificate,
  DNS and ports 80/443 being reachable, a real Google sign-in, a real Backblaze upload and restore, the real `ufw`/`fail2ban`/`sshd` behaviour,
  the Hetzner firewall, a reboot, systemd firing the timers, and a notification arriving at a real receiver. Each of these is checked by
  the steps below when you run them (`scripts/hosting-verify.sh https://abb.<your-domain> --server`), and until you have run them the project does not claim them.
- Prices and plan names below are what the plan was written against. Hetzner and Backblaze change them: **confirm on their pages before you pay.**

No part of this makes Agent BlackBox a managed service or adds compliance claims. You are the operator of this server, and what your agents
send to it is yours to protect: the default capture mode records metadata only; read the [security notes](SECURITY.md) and the
[retention runbook](runbooks/retention.md) before you switch to full capture.

## What you will end up with

```text
internet --443/80--> Caddy (/opt/edge, the only listener)  --Docker network "edge"-->  abb-web:3000   dashboard
                                                                                       abb-api:8000   /v1/* and /healthz
Agent BlackBox (/opt/abb, its own compose project, its own PostgreSQL, no published ports)
Grafana, Prometheus, Alertmanager: 127.0.0.1 only, reached with an SSH tunnel
A second app (for example ServerFlow): its own project and database, one more Caddy site file
```

One hostname, `abb.<your-domain>`, serves the dashboard and, by path, the ingestion API. Your agents send events to `https://abb.<your-domain>`.

## What you need to create (and what it costs)

Everything in this list is something only you can do. Each item is marked `ACTION NEEDED` where you meet it in the order below.

| You create | Why | Rough cost (confirm on the provider's page) |
| --- | --- | --- |
| A Hetzner Cloud server, type **CX23** (2 vCPU, 4 GB RAM, about 40 GB disk), Ubuntu 24.04 | runs everything | about EUR 4-4.5 per month |
| A domain name and access to its DNS records | `abb.<domain>` and the certificate | the domain's yearly price |
| A Google Cloud OAuth client | sign-in for invited people | free |
| A Backblaze B2 bucket and an application key (or a Hetzner Storage Box) | the second copy of the backups | a few cents per GB-month; the first 10 GB are usually free (confirm) |
| An alert receiver (email, Slack or a webhook) and, optionally, a dead-man's-switch service | so alerts reach you | free tiers exist |
| Optional: Hetzner automatic server backups | fast rebuild of the host, not a substitute for the logical backups | about 20 percent of the server price |

Memory arithmetic, so the choice of size is not a guess. The overlay sets a ceiling per container (`deploy/hosting/compose.production.yml`):
PostgreSQL 512 MB, API 768 MB, worker 384 MB, web 384 MB, retention 256 MB, Caddy 128 MB. They sum to about 2.4 GB, but they are ceilings, not
reservations: with a small amount of data the stack measured about 0.5 GB in the local rig (a smoke load of 2,040 events, `make hosting-e2e`;
numbers grow with data and use). A second app idles near 0.7 GB in ServerFlow's case, so the pair idles near 1.3 GB on a 4 GB machine, with swap as a safety net. The monitoring profile adds
about 0.3 GB when running (Grafana is the heavy part; stop it when you are not looking). If memory alerts fire, resize: [Growing the server](#growing-the-server).

## Step 1: the Hetzner server

**ACTION NEEDED (you):** create a Hetzner Cloud account and a project, then a server.

1. Location: your choice. The cheapest are in Germany and Finland; pick the one closest to the people and agents that will use it. The choice cannot be changed later without rebuilding.
2. Image: **Ubuntu 24.04**. Type: **CX23**. Networking: IPv4 on (the edge listens on IPv4 only, see step 2).
3. SSH key: add the **public** half of a key pair you created on your own machine (`ssh-keygen -t ed25519`). Never paste a private key anywhere. Without a key, Hetzner emails a root password; do not use that route.
4. Optional: tick **Backups** (automatic server backups). They help rebuild the host quickly; they are not a consistent database backup, which is what the nightly bundles are for.
5. Name it, create it, and note its IPv4 address.
6. **ACTION NEEDED (you):** create a **Cloud Firewall** in the Hetzner project and attach it to the server: allow inbound TCP 22, 80 and 443 (and nothing else). It sits outside the machine, so it still works if a container publishes a port by mistake.

## Step 2: the domain and DNS

**ACTION NEEDED (you):** at your DNS host, create these records for the name you will use (here `abb.example.org`; replace it everywhere):

| Type | Name | Value |
| --- | --- | --- |
| A | `abb` | the server's IPv4 address |
| CAA (optional) | the domain | `0 issue "letsencrypt.org"` (only that authority may issue for the domain) |

Publish **no AAAA record**: the Caddy edge listens on IPv4 only (`0.0.0.0:80/443`), so an IPv6 address would advertise a listener that does not exist and make IPv6 clients retry before falling back. (IPv6 support is a later change: publish `[::]:80/443` in `deploy/hosting/edge/compose.yml`, then add the record.)

Wait until `dig +short abb.example.org` from your machine shows the address. Caddy asks Let's Encrypt for the certificate on its first start, and it fails (and retries
with a back-off) if the name does not resolve yet. Do not run a second site on port 80 or 443 on the same machine.

## Step 3: the Google sign-in client

**ACTION NEEDED (you):** in the Google Cloud console create (or pick) a project, then:

1. APIs and Services, **OAuth consent screen**: user type **External** (or Internal if your Google account is a Workspace organisation and everyone who signs in belongs to it). App name: anything you will recognise. Add your own email as a test user while the app is in Testing mode. Scopes: the defaults (`openid`, `email`, `profile`) are enough.
2. APIs and Services, **Credentials**, Create credentials, **OAuth client ID**, application type **Web application**.
3. Authorised redirect URI, exactly: `https://abb.example.org/api/auth/callback` (your host name; https; no trailing slash). Authorised JavaScript origins are not needed.
4. Create it and keep the page open: you need the **client ID** now and the **client secret** in step 6. The secret goes into a file on the server with an editor. Do not paste it into a chat, a ticket or a shell command line.

Invitation-only access is enforced by Agent BlackBox itself: anyone with a Google account can reach the sign-in, and a person who is not invited sees "No access yet" and nothing is created
([sign-in runbook](runbooks/auth-and-access.md)). Google verifies email addresses, so the `email_verified` rule is satisfied. Other identity providers are described in the
sign-in runbook but are not part of this guide.

## Step 4: the second backup provider (Backblaze B2)

**ACTION NEEDED (you):** create a Backblaze account, then (the console wording changes; confirm as you go):

1. Buckets, **Create a bucket**: private, a unique name, default encryption on. Note the bucket name and its endpoint region.
2. Lifecycle settings for the bucket: keep only the last version of a file and delete files older than 30 days, as a second layer under the script's own pruning.
3. App Keys, **Add a New Application Key**: restrict it to **this one bucket**. The console's read-and-write key can also delete files; that is what `ABB_OFFSITE_PRUNE=1` (the default) needs. If you want a key that cannot delete, so
   that a compromised server cannot erase the history, create a key without delete rights with Backblaze's command line tool, set `ABB_OFFSITE_PRUNE=0` and rely on the lifecycle rule in step 2.
4. Copy the **keyID** and **applicationKey** once. They are entered into rclone's configuration on the server in step 8, never into the repository.

A Hetzner Storage Box over SFTP also works: the backup script takes any `rclone` remote.

## Step 5: prepare the server

**ACTION NEEDED (you):** run these from your own machine and terminal. Replace `SERVER` with the IPv4 address.

```bash
ssh root@SERVER
```

On the server, as root, get the repository and run the bootstrap in two stages. The bootstrap installs Docker from Docker's own apt repository, the firewall (`ufw`), `fail2ban`, automatic security updates, `rclone`,
the `deploy` user, a 2 GiB swapfile, log rotation, the `edge` Docker network and the backup units. It is idempotent: a second run changes nothing.

```bash
apt-get update && apt-get install -y git
git clone <the address of your copy of the repository> /root/abb-setup
cd /root/abb-setup
# paste your PUBLIC key (one line) into a file, for example with nano
nano /root/deploy.pub
scripts/hosting-bootstrap.sh --check
scripts/hosting-bootstrap.sh --apply --ssh-key-file /root/deploy.pub
```

`--check` lists every action and changes nothing; read it. `--apply` refuses a private key and a file with no valid key.

**Do not skip the lockout test.** Stage 1 leaves password login on so that you cannot lock yourself out. Open a **second terminal** on your machine and log in as the new user with your key:

```bash
ssh deploy@SERVER
```

Keep that second session open. Only when it works, run stage 2 from the first terminal. It refuses unless it finds a key login by `deploy` in the ssh log and a live `deploy` session, and it restores the old
state if `sshd` stops listening:

```bash
scripts/hosting-bootstrap.sh --harden-ssh
```

If you are ever locked out, the Hetzner console (rescue or VNC) works without ssh. From now on work as `deploy`, never as root.
The real `ufw` and `sshd` behaviour is `UNVERIFIED (env)` until you have run `scripts/hosting-verify.sh ... --server` in step 10.

## Step 6: the code, the secrets and the configuration

As `deploy` on the server:

```bash
cd /opt/abb
git init -q && git remote add origin <the address of your copy of the repository>
git fetch --tags origin && git checkout -f main      # or a release tag
scripts/hosting-secrets.sh --origin https://abb.example.org --acme-email you@example.org
```

`hosting-secrets.sh` creates `/opt/abb/.env` (mode 0600) with the three PostgreSQL passwords and the metrics token generated, `secrets/` for the monitoring containers, `/etc/abb/backup.pass` (the backup
passphrase) and `/etc/abb/backup.env`. It prints no secret and never overwrites an existing file (the backup passphrase is never regenerated: a new one would make every existing bundle unreadable).
Use a real git tag for production rather than `main` once you have one; `main` is fine while you are the only user.

**ACTION NEEDED (you), in order:**

1. Put the **backup passphrase** in your password manager now: `sudo cat /etc/abb/backup.pass` on the server, copy it. If it is lost, every backup is unreadable and no one can help.
2. Edit `/opt/abb/.env` with an editor and fill `ABB_OIDC_CLIENT_ID` and `ABB_OIDC_CLIENT_SECRET` from step 3. Check `ABB_OIDC_EXTRA_HOSTS` is still `oauth2.googleapis.com,www.googleapis.com`. Leave `ABB_RETENTION_MODE=dry_run`.
3. Copy the Grafana admin password from `/opt/abb/secrets/grafana_admin_password` into your password manager (you need it for the tunnel in [Grafana](#grafana-over-an-ssh-tunnel)).
4. Backups: put the B2 key into rclone's configuration and set the remote in `/etc/abb/backup.env`. Run these as `deploy`, **not with `sudo`**: the backup service runs as `deploy`, and a config
   file made by root is unreadable to it (the run then fails with `Failed to load config file: permission denied`; `hosting-backup.sh --check` and `hosting-verify.sh --server` name this cause).

```bash
rclone --config /etc/abb/rclone.conf config        # new remote named b2, type Backblaze B2: it asks for the keyID and applicationKey
chmod 600 /etc/abb/rclone.conf
# only if you ever ran the line above with sudo, the file is root-owned: sudo chown deploy:deploy /etc/abb/rclone.conf
rclone --config /etc/abb/rclone.conf lsd b2:       # lists your bucket if the key works
nano /etc/abb/backup.env                           # ABB_OFFSITE_REMOTE=b2:YOUR-BUCKET/abb ; ABB_OFFSITE_PRUNE=0 if the key cannot delete
```

## Step 7: the shared front door (Caddy)

Caddy is the only thing that listens on 80 and 443. It lives in `/opt/edge` as its own compose project so that a second app can share it.

```bash
cd /opt/edge
cp /opt/abb/deploy/hosting/edge/compose.yml /opt/abb/deploy/hosting/edge/Caddyfile .
mkdir -p sites globals
cp /opt/abb/deploy/hosting/edge/abb.caddy.template sites/abb.caddy
printf 'ABB_ACME_EMAIL=you@example.org\nABB_SITE_ADDRESS=abb.example.org\n' > .env
docker compose config -q && echo config ok
```

Do not start it yet: Caddy would answer 502 until the application is up. The site file is the Caddy side of the design (the applications keep their own security headers; Caddy owns TLS, the redirect, the 8 MiB body limit, timeouts, compression
that skips event streams, and the real client address in `X-Forwarded-For`).

## Step 8: first deploy

```bash
cd /opt/abb
scripts/hosting-upgrade.sh main --no-verify
```

This is the same script used for every upgrade. On the first run there is no database, so it takes no backup; it builds the images, runs the migrations as the owner role before any API process starts, then starts `api`, `worker`,
`retention` and `web` and waits for their health checks. Building takes several minutes on this machine. Then start the front door:

```bash
cd /opt/edge
docker compose up -d
docker compose logs --tail 30 caddy       # look for "certificate obtained successfully"
```

If the certificate is not obtained, the log line names the failed challenge: almost always DNS not pointing at the server yet, or port 80/443 blocked
([the runbook](runbooks/hosting-operations.md#the-site-is-down-or-the-certificate-is-wrong)). Caddy backs off between attempts, so fix the cause rather than restarting repeatedly (Let's Encrypt rate-limits failures).

Create the first workspace and its owner. Nobody is seeded in production, and a person who signs in without an invitation sees no workspaces ([sign-in runbook](runbooks/auth-and-access.md#2-the-first-owner-and-everyone-after)):

```bash
cd /opt/abb
dc="docker compose -f docker-compose.yml -f deploy/hosting/compose.production.yml --profile app --profile ops"
$dc exec api python -m abb_api.cli create-workspace --name "Home" --slug home
$dc exec api python -m abb_api.cli add-member --workspace home --email you@example.org --role OWNER
$dc exec api python -m abb_api.cli create-project --workspace home --name "First project" --slug first
```

Use the Google account whose email you gave to `add-member`. Create an ingestion key for your agents (Settings, API keys in the dashboard, or `create-key`) and point the SDK at `https://abb.example.org`
([quickstart](quickstart.md) for the SDK).

## Step 9: monitoring and alerts

```bash
cd /opt/abb
echo "ABB_OBS_UID=$(id -u)" >> .env && echo "ABB_OBS_GID=$(id -g)" >> .env
dco="docker compose -f docker-compose.yml -f deploy/hosting/compose.production.yml -f deploy/hosting/compose.observability.yml --profile app --profile observability"
$dco up -d --wait
sudo systemctl enable --now abb-container-metrics.timer abb-backup.timer
sudo systemctl start abb-backup.service            # the first backup now; read its output
```

Writing `ABB_OBS_UID` into `.env` is also what tells `hosting-upgrade.sh`, `hosting-backup.sh` and the restore drill to keep the overlay (and the worker's metrics port) in every later command; do not skip that line. Use `$dco` instead of `$dc` from here on.

The observability overlay adds Prometheus (30 days, 4 GiB at most), node_exporter, Alertmanager, a probe of your public `/healthz` and certificate, and Grafana, all bound to `127.0.0.1`.

**ACTION NEEDED (you):** choose where alerts go. Alertmanager ships with **no receiver**: alerts are evaluated and visible, and are sent nowhere until you configure one. **Do not edit the tracked file**
`deploy/hosting/alertmanager/alertmanager.yml.tmpl`: a modified checkout makes `hosting-upgrade.sh` refuse. Put the receiver in an untracked file instead, `/opt/abb/secrets/alertmanager/notify.yml`
(mode 0600, owner `deploy`, the same directory as the secret files it points to). Its content is the integration blocks of the `notify` receiver. For Gmail (an
[app password](https://myaccount.google.com/apppasswords), not your account password; put it in `/opt/abb/secrets/alertmanager/smtp_password`, mode 0600):

```yaml
email_configs:
  - to: <you>@gmail.com
    from: <you>@gmail.com
    smarthost: smtp.gmail.com:587
    auth_username: <you>@gmail.com
    auth_password_file: /run/alertmanager-secrets/smtp_password
    headers:
      subject: '{{ template "abb.title" . }}'
    text: '{{ template "abb.text" . }}'
```

Slack (`slack_configs:` with `api_url_file: /run/alertmanager-secrets/slack_webhook_url`) and webhook (`webhook_configs:` with `url_file: /run/alertmanager-secrets/webhook_url`) examples are in the template's comments.
Then `$dco up -d` (it re-renders the configuration, `amtool check-config` validates it, and only then is Alertmanager restarted; a malformed file stops there with amtool's message and the running Alertmanager is not replaced).
The dead-man's-switch receiver works the same way with an optional `deadman.yml` (`webhook_configs:` with `url_file: /run/alertmanager-secrets/deadman_url`).
Optionally create a dead-man's-switch check (a Healthchecks-style URL, put in a file under that directory) for the always-firing `Watchdog` alert and set `ABB_DEADMAN_URL` in `/etc/abb/backup.env`, so you also hear when the whole pipeline goes quiet.
Set `ABB_RUNBOOK_BASE_URL` in `.env` to the address where you serve these docs if you want clickable runbook links in notifications.

## Step 10: verify

From your own machine (checks the public edge, no server access needed):

```bash
scripts/hosting-verify.sh https://abb.example.org
```

It asserts the certificate and the redirect, HSTS exactly once, a per-request nonce CSP, `/docs`, `/readyz` and `/metrics` closed, cookie attributes, forged `X-Forwarded-For` not evading the throttle, event streams delivering incrementally, the 8 MiB body limit, compression, and flood limits. The same
script passes 30 assertions against the local rig; against your server it also proves Let's Encrypt, DNS and the real network path.

On the server, **with sudo** (checks the host itself: firewall, ssh, published ports, secrets modes, backup freshness, disk, log rotation). Without root, `ufw`, `sshd -T`, `fail2ban` and `timedatectl` cannot be read and show `SKIP`, which is not a pass:

```bash
cd /opt/abb && sudo scripts/hosting-verify.sh https://abb.example.org --server
```

From another machine (what the internet can reach; only 80 and 443 should answer):

```bash
scripts/hosting-verify.sh --server-probe SERVER
```

**ACTION NEEDED (you):** sign in with Google as the owner; then try a Google account that is not invited and confirm it sees "No access yet". Run the restore drill once now ([Backups](#backups-and-the-restore-drill)). Force one alert (for example
stop the worker for ten minutes) and confirm it reaches your receiver. Until you have done these, "live HTTPS", "real sign-in", "off-site restore" and "alert delivery" remain `UNVERIFIED (env)`.

## Backups and the restore drill

The nightly timer (`abb-backup.timer`, 02:30 server time) makes an **encrypted** bundle (database dump plus artifact files) in `/opt/abb/backups`, verifies it, copies it to the second provider, checks the copy,
prunes old copies (local: 7 days but always the newest 3; off-site: 30 days but always the newest 7) and writes the metrics that alert when it is late. A backup that is not scheduled is invisible to its own alert, so
`hosting-verify.sh --server` fails when no complete backup exists or the last is older than 36 hours.

The bundle plus `/etc/abb/backup.pass` is the keys to the installation. Keep the passphrase and the rclone key in your password manager, and keep them out of the repository.

A backup is only real once it has been restored. The drill reads `/etc/abb/backup.env` itself and uses `/etc/abb/rclone.conf` when `RCLONE_CONFIG` is not set, so no exports are needed. The drill restores the newest off-site bundle into a throwaway database next to the real one, compares every table with the manifest, prints `DRILL OK` and removes what it created:

```bash
cd /opt/abb
scripts/hosting-restore-drill.sh --latest
```

Do it after the first backup, then monthly (`AbbHostingRestoreDrillOld` reminds you after 45 days), and **always before** you set `ABB_RETENTION_MODE=enforce`, which deletes data permanently. The full restore
into a replacement server is in [the operations runbook](runbooks/hosting-operations.md#restoring-onto-a-new-server) and the tool's own procedure is [backup and restore](runbooks/backup-restore.md).

## Upgrades and rollback

```bash
cd /opt/abb
scripts/hosting-upgrade.sh --dry-run v1.2.0        # prints the plan and the commits that would arrive
scripts/hosting-upgrade.sh v1.2.0
```

The script takes a verified, off-site-copied backup first and stops if it cannot; tags the running images `rollback`; checks out and builds the new release; runs the migrations as the owner role before the new API starts; starts the services with `--wait`;
and runs `hosting-verify.sh`. If the new release fails to start and the schema revision did not change, it puts the previous code and images back by itself. If the schema did change, it does **not** roll back (an older release may not run on a newer schema): it
names the pre-upgrade bundle, and you either fix forward or restore that bundle ([migrations](runbooks/migrations.md) says which migrations can be reversed). A PostgreSQL image change in a new release is refused: a major version needs a dump and restore, never an in-place swap.
Read the release notes and `git log` between the tags first. Never run `docker volume prune` or `docker compose down -v` on this server: both delete the database.

## Adding a second app (for example ServerFlow)

**Trust assumption (KI-316):** every container attached to the shared `edge` network can reach Agent BlackBox's `api` and `web` directly, bypassing Caddy's closed paths, and can forge `X-Forwarded-For` (the API trusts it because Caddy is meant to be the only path in). Attach only apps you trust as much as Caddy itself. Per-app edge networks are the planned fix; until then, do not put an untrusted or internet-exposed-and-weakly-maintained app on `edge`.

Each app keeps its own compose project and its own PostgreSQL; only Caddy is shared. Nothing in the ServerFlow repository changes. The example below is a pattern, not something this project applied or tested against ServerFlow.

1. On the server, keep an override file outside the ServerFlow checkout, for example `/opt/serverflow/compose.edge.yml`, that attaches its gateway to the `edge` network under a unique alias, and start ServerFlow with `-f` for its own file and this one:

   ```yaml
   services:
     gateway:
       networks:
         default: {}
         edge:
           aliases: [serverflow-gateway]
   networks:
     edge:
       external: true
   ```

2. **ACTION NEEDED (you):** add a DNS record (A, no AAAA) for `sf.example.org`.
3. Add one Caddy site file from [`deploy/hosting/edge/serverflow.caddy.example`](../deploy/hosting/edge/serverflow.caddy.example), edit the host name, and reload (validate first; a reload keeps the old configuration if the new one is broken, a restart does not):

```bash
cd /opt/edge
cp /opt/abb/deploy/hosting/edge/serverflow.caddy.example sites/serverflow.caddy
nano sites/serverflow.caddy
docker compose exec caddy caddy validate --config /etc/caddy/Caddyfile
docker compose exec caddy caddy reload --config /etc/caddy/Caddyfile
```

ServerFlow's control plane, Grafana and Prometheus stay behind SSH tunnels; only the gateway is routed. A mistake in one site file can stop Caddy for both apps, which is why validation comes first. Both applications' memory ceilings share the one machine; the memory
alerts watch the host as a whole and the container alerts watch every container on it.

## Growing the server

If `HostMemoryLow` fires for sustained periods, or you add a heavier app: Hetzner Cloud console, the server, **Rescale**, choose **CX33** (4 vCPU, 8 GB; about EUR 6.5-8.5 per month, confirm) and tick **CPU and RAM only**. That keeps the disk size, so the resize is reversible; a resize that also grows the disk cannot be undone. The server restarts
(a few minutes of downtime); Docker brings the containers back because every service has `restart: unless-stopped`. Afterwards run `scripts/hosting-verify.sh https://abb.example.org --server`. The memory ceilings in the overlay do not need to change. To use the extra RAM for PostgreSQL, raise its limit in
`deploy/hosting/compose.production.yml` and the `shared_buffers` setting deliberately, not as a side effect. If the disk is the limit, Hetzner volumes can be attached; see [disk is filling up](runbooks/hosting-operations.md#disk-is-filling-up).

## Grafana over an SSH tunnel

Grafana (3001), Prometheus (9090) and Alertmanager (9093) listen on the server's loopback only. From your machine:

```bash
ssh -L 3001:127.0.0.1:3001 -L 9090:127.0.0.1:9090 deploy@SERVER
```

Then open `http://127.0.0.1:3001` (user `admin`, the password from step 6) and `http://127.0.0.1:9090`. The Agent BlackBox dashboard is provisioned. No host dashboard was built; the host alerts are visible in Prometheus and Alertmanager.

## Why not Vercel or Coolify, and other notes

- **Vercel and similar:** the product needs an always-on API, a worker and a retention job, event streams, PostgreSQL and a persistent disk. A split (dashboard on a static or serverless host, API elsewhere) is possible later but is not documented or tested.
- **Coolify or another platform layer:** one more moving part to secure and upgrade. Nothing here prevents using one; this guide does not cover it.
- **A separate `api.<domain>`:** possible by editing the Caddy site file, at the cost of a second certificate and cookie and CORS questions. One hostname is what was verified.
- **The five dependency questions** (build prompt): the host tools (`rclone`, `fail2ban`, `ufw`, `unattended-upgrades`, Docker) are installed on the server by the bootstrap script and are not project dependencies; no library was added to the repository; the container images are digest-pinned and covered by `scripts/check-pins.sh`.

## When something is wrong

Start with the [hosting operations runbook](runbooks/hosting-operations.md): disk, memory, restarts, backups, certificates, alerts that never arrive, rotating secrets, upgrades and basic incident handling. The application's own alerts and runbooks are indexed in the [runbooks](runbooks/README.md).
