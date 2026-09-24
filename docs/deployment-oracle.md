# Deploying POLICYGUARD to Oracle Cloud Always Free ($0/month)

**Why this exists:** Truehost shared hosting blocks all outbound database
connections (verified 2026-09-24: ports 3306/5432/27017 BLOCKED, only 80/443
open; support confirmed "connection to external databases is disabled").
This app requires MongoDB, which cPanel shared hosting does not offer. The app
therefore moves to an Oracle Cloud **Always Free** VM; the database stays on
**MongoDB Atlas M0 (free)**; Truehost keeps only the **domain registration and
email mailboxes**.

## Target architecture

```
Browser ──HTTPS──► Oracle VM (Always Free, $0)
                     nginx :80/:443  ──► gunicorn 127.0.0.1:8000 (3 workers)
                     │                        │
                     ├─ /static/ (direct)      └──► MongoDB Atlas M0 (free, unchanged)
                     └─ cron * * * * * ─► scripts/sms_scheduler.py --once ─► Africa's Talking
M-Pesa Daraja C2B callbacks ──HTTPS──► /payments/c2b/{confirm,validate}
westlakeinsuranceltd.com DNS ──► A record → VM public IP (MX records untouched → email keeps working)
```

Cost: **$0/month** (Oracle Always Free VM + Atlas M0). One-time work: ~1–2 hours.

---

## Part A — Create the Oracle Cloud account (10 min)

1. Go to https://www.oracle.com/cloud/free/ → **Start for free**.
2. Sign up with a real email. Oracle requires a **debit/credit card** for
   identity verification — it makes a small temporary hold, **it is never
   charged** as long as you only create "Always Free Eligible" resources.
3. **Home region — this choice is permanent** and Always Free compute only
   exists in your home region:
   - **Recommended: Johannesburg (`af-johannesburg-1`)** — closest to Kenya
     (~40 ms), keeps data on the continent.
   - If you hit repeated "Out of capacity" errors there (Part N gotcha), pick
     **Frankfurt or Amsterdam** instead (~160 ms — still fine).
4. Finish signup → you land in the OCI Console.

## Part B — Create the VM (10 min)

1. On your Windows machine, create an SSH key (PowerShell):
   ```powershell
   ssh-keygen -t ed25519 -f $env:USERPROFILE\.ssh\oracle_policyguard
   ```
   This makes `oracle_policyguard` (private — never shares) and
   `oracle_policyguard.pub` (public — paste into Oracle).
2. OCI Console → **Compute → Instances → Create instance**:
   - **Name:** `policyguard`
   - **Image:** Ubuntu 24.04 (**aarch64**)
   - **Shape:** `VM.Standard.A1.Flex` — set **2 OCPU / 12 GB RAM**
     (Oracle halved the free ARM allowance in June 2026: 2 OCPU/12 GB is now
     the max free; still far more than this app needs. Must show the
     **"Always Free Eligible"** label.)
   - **Networking:** create new VCN + public subnet (wizard defaults are fine)
   - **Assign a public IPv4 address:** yes
   - **SSH keys:** paste the contents of `oracle_policyguard.pub`
3. Create → wait ~1 min → note the **public IP** (e.g. `129.151.x.x`).

> "Out of capacity for shape VM.Standard.A1.Flex"? See Part N.

## Part C — Open ports 80/443 in Oracle's cloud firewall (5 min)

The VM has **two** firewalls; this is the cloud one (the OS one is handled by
`setup.sh`):

1. Instance page → click the **Subnet** → **Default Security List** →
   **Add Ingress Rules**, twice:
   - Stateless: No · Source CIDR: `0.0.0.0/0` · Protocol: TCP · Port: **80**
   - Stateless: No · Source CIDR: `0.0.0.0/0` · Protocol: TCP · Port: **443**
   (Port 22/SSH is already open — that's how you log in.)

## Part D — SSH in, upload the code, run setup (15 min)

From PowerShell on your Windows machine:

```powershell
# 1. Log in (user is 'ubuntu' on Ubuntu images)
ssh -i $env:USERPROFILE\.ssh\oracle_policyguard ubuntu@<VM-IP>

# 2. On the VM: prepare the app directory
sudo mkdir -p /srv/policyguard
sudo chown ubuntu:ubuntu /srv/policyguard

# 3. Get the code onto the VM — pick ONE:
#    A. Git (repo is already on GitHub):
git clone https://github.com/DhuolK/POLICY-GIUARD.git /srv/policyguard
#    B. Or zip upload: on Windows run
#       scp -i $env:USERPROFILE\.ssh\oracle_policyguard policyguard.zip ubuntu@<VM-IP>:~/
#       then on the VM: unzip ~/policyguard.zip -d /srv/policyguard

# 4. Run the automated setup (packages, venv, gunicorn service, nginx,
#    firewall, cron — see deploy/oracle/setup.sh for exactly what it does)
chmod +x /srv/policyguard/deploy/oracle/setup.sh
sudo /srv/policyguard/deploy/oracle/setup.sh
```

The script stops before starting the app because `.env` doesn't exist yet —
that's next.

## Part E — Create `.env` with production values (10 min)

Copy the values you already have in cPanel → **Setup Python App → Environment
variables** (or your local `.env`). On the VM:

```bash
sudo -u policyguard nano /srv/policyguard/.env
```

Required changes vs the Truehost values:

| Variable | Set to |
|---|---|
| `FLASK_ENV` | `production` |
| `SECRET_KEY` | reuse existing (changing it logs everyone out — fine either way) |
| `MONGO_URI` | unchanged — same Atlas cluster |
| `APP_BASE_URL` | `https://westlakeinsuranceltd.com` |
| `MPESA_C2B_CONFIRM_URL` | `https://westlakeinsuranceltd.com/payments/c2b/confirm` |
| `MPESA_C2B_VALIDATE_URL` | `https://westlakeinsuranceltd.com/payments/c2b/validate` |
| `MPESA_CALLBACK_ALLOWED_IPS` | **REQUIRED** — with `MPESA_ENV=production` the app refuses to boot without it (see `.env.example` for the community-documented Safaricom ranges) |
| `SMS_SIMULATE` | `0` only when you're ready for real SMS spend |

Lock it down and start:

```bash
sudo chmod 600 /srv/policyguard/.env
sudo systemctl start policyguard
sudo systemctl status policyguard --no-pager   # expect: active (running)
```

## Part F — Smoke test before touching DNS

```bash
curl http://localhost/healthz        # {"status":"ok"}  (nginx → gunicorn)
curl http://<VM-IP>/healthz          # same, through Oracle's firewall
```
Open `http://<VM-IP>/login` in a browser and log in — this proves the whole
chain (nginx → gunicorn → Atlas). **This is the moment Truehost could never
give you.**

## Part G — DNS cutover at Truehost (5 min + propagation)

In Truehost's DNS zone editor (hPanel/cPanel → Zone Editor) for
`westlakeinsuranceltd.com`:

- **Edit** the `A` record for `@` → `<VM-IP>`
- **Edit/add** `www` → `A` record → `<VM-IP>` (or CNAME → `@`)
- **DO NOT TOUCH** `MX`, `TXT` (SPF), or `mail` records — those keep your
  `@westlakeinsuranceltd.com` email working on Truehost.

Propagation: minutes to a few hours. Check with `nslookup westlakeinsuranceltd.com`.

> Only after DNS resolves to the VM, run TLS below. Then the old Truehost app
> is dead — logins, policies, and claims all live on the VM.

## Part H — Free HTTPS via Let's Encrypt (5 min)

```bash
sudo snap install --classic certbot
sudo certbot --nginx -d westlakeinsuranceltd.com -d www.westlakeinsuranceltd.com
```
Certbot edits the nginx config to 443 + redirect and auto-renews via a systemd
timer — nothing else to do. Verify: `https://westlakeinsuranceltd.com/healthz`.

## Part I — Migrate claim-evidence uploads from Truehost

```bash
# In cPanel → Terminal (Truehost side):
cd ~/policyguard && zip -r ~/claims-uploads.zip uploads/

# Download claims-uploads.zip via cPanel File Manager to your PC, then:
scp -i $env:USERPROFILE\.ssh\oracle_policyguard claims-uploads.zip ubuntu@<VM-IP>:~/

# On the VM:
sudo unzip ~/claims-uploads.zip -d /srv/policyguard/
sudo chown -R policyguard:policyguard /srv/policyguard/uploads
```

## Part J — Tighten MongoDB Atlas (2 min)

1. Atlas → **Network Access** → add `<VM-IP>/32`.
2. **Delete** the temporary `0.0.0.0/0` entry.
3. Note: Oracle's *ephemeral* public IP is lost if the VM is ever stopped
   (reboots keep it; stop/start does not). To make it permanent, OCI lets you
   convert it to a **Reserved Public IP** (free while attached): Instance →
   Attached VNICs → (⋮) → Edit → Reserved public IP. Recommended.

## Part K — Re-register webhooks (they point at the public URL)

- **M-Pesa Daraja:** log in as admin → Payments accounts page → **Register C2B
  URLs** (re-registers using the new `APP_BASE_URL`).
- **Africa's Talking:** AT dashboard → SMS callbacks → set DLR to
  `https://westlakeinsuranceltd.com/sms/dlr` and inbound to
  `https://westlakeinsuranceltd.com/sms/inbound`.

## Part L — Go-live checklist

- [ ] `https://westlakeinsuranceltd.com/healthz` → ok (padlock, no warning)
- [ ] Login works (admin + a worker + a customer)
- [ ] Issue a test policy → appears in Atlas
- [ ] Upload claim evidence → file lands in `/srv/policyguard/uploads/claims/`
- [ ] `tail /var/log/policyguard-sms.log` — cron ticks every minute
- [ ] Test M-Pesa C2B payment confirms against the new URL
- [ ] Atlas allowlist = VM IP only
- [ ] Old Truehost Python app stopped (cPanel → Setup Python App → Stop)

## Part M — Day-2 operations cheatsheet

```bash
sudo journalctl -u policyguard -f          # live app logs
sudo systemctl restart policyguard         # restart app
tail -f /var/log/policyguard-sms.log       # SMS scheduler ticks
sudo nginx -t && sudo systemctl reload nginx

# Deploy an update:
cd /srv/policyguard && git pull
sudo chown -R policyguard:policyguard /srv/policyguard
sudo /srv/policyguard/venv/bin/pip install -r requirements.txt   # if deps changed
sudo systemctl restart policyguard
```

**Backups (you are the sysadmin now):** uploads are the only data living on
this VM (the DB is on Atlas). Add a weekly cron that tars
`/srv/policyguard/uploads` and copies it off-box (e.g. `scp` to your PC, or
Oracle Object Storage's free 20 GB tier).

## Part N — Gotchas

- **"Out of capacity for shape VM.Standard.A1.Flex"** — free ARM capacity is
  first-come-first-served per region. Tricks: retry at different hours (it
  genuinely comes and goes), try each Availability Domain, or temporarily use
  the always-available `VM.Standard.E2.1.Micro` (x86, 1 GB RAM — add a 2 GB
  swapfile) and switch shapes later. If a region is dry for weeks, that's why
  Part A told you to choose carefully: **home region cannot be changed**.
- **Idle reclaim:** Oracle may reclaim Always Free compute that's idle for 7+
  days. A real app with per-minute cron and daily logins is never "idle", but
  a free UptimeRobot monitor on `/healthz` is a good belt-and-braces.
- **ARM64 wheels:** all pinned deps (pymongo, gunicorn, etc.) ship aarch64
  Linux wheels or are pure Python — `setup.sh` needs no compiler toolchain.
- **Email:** sending from the VM (ports 25/465/587) is blocked by Oracle by
  default — irrelevant here: the app sends SMS, not email, and your mailboxes
  stay on Truehost.
