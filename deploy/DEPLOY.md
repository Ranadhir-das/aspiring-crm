# Deploying Vaani to your droplet (vaaniapp.co.in)

Written for your actual setup: the DigitalOcean droplet named `vaani`
(159.89.172.172, BLR1, 1 GB RAM / 25 GB disk) with `vaaniapp.co.in` already
pointed at it. Run everything below over SSH on the server (`ssh root@159.89.172.172`
or whatever user you set up) unless marked otherwise.

**Heads-up on the droplet size**: 1 GB RAM is tight once Postgres, Redis, Daphne,
and OpenCV's face-detection models are all resident at once. Step 1 adds a swap
file as a safety net, which is enough for light traffic — but if the site feels
sluggish or the app OOM-kills under real usage, resize the droplet to 2 GB in the
DigitalOcean control panel (Droplet → Resize; a quick reboot, no rebuild needed)
before debugging further.

## 0. Remove the placeholder site

Your droplet is currently serving a demo "VAANI calling app" landing page
template — that's not this project, it's leftover content from before. Find out
what's serving it and remove it:

```bash
# See what's listening on 80/443 and what's serving the current site
sudo systemctl status nginx apache2 2>&1 | head -20
ls /var/www/ /var/www/html/ 2>/dev/null
```

Most likely it's Nginx (or Apache) serving static files from `/var/www/html` or
similar. Once you've confirmed where it lives:

```bash
# If Nginx:
sudo rm -f /etc/nginx/sites-enabled/default
sudo rm -rf /var/www/html/*          # back it up first if you want to keep it: cp -r /var/www/html ~/old-vaani-template-backup

# If it's Apache instead, disable/remove it so it stops competing with Nginx for port 80:
sudo systemctl disable --now apache2 2>/dev/null
sudo apt remove -y apache2 2>/dev/null
```

Don't `certbot`/DNS anything yet — just get port 80 free and confirm
`vaaniapp.co.in` still points at `159.89.172.172` (Domains → vaaniapp.co.in in
your DigitalOcean project, which your screenshot shows is already set up: 1 A
record).

## 1. System packages + swap

```bash
sudo apt update && sudo apt upgrade -y
sudo apt install -y python3-venv python3-pip postgresql postgresql-contrib \
    redis-server nginx git libglib2.0-0 libgl1 libsm6 libxext6

# 1 GB RAM safety net — a 2 GB swap file
sudo fallocate -l 2G /swapfile
sudo chmod 600 /swapfile
sudo mkswap /swapfile
sudo swapon /swapfile
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
```

`libglib2.0-0 libgl1 libsm6 libxext6` are for `opencv-python-headless` — without
them face verification fails to import `cv2` on a minimal server image, even
though it works fine on your dev machine. `redis-server` backs the team-chat
WebSocket channel layer — local dev doesn't need it (in-memory fallback), but
production does.

Confirm Redis is running: `sudo systemctl status redis-server` (should be
"active (running)"; it's enabled by default on Ubuntu after install).

## 2. PostgreSQL

```bash
sudo -u postgres psql
```
```sql
CREATE DATABASE vaani;
CREATE USER vaani_user WITH PASSWORD 'a-real-strong-password';
ALTER ROLE vaani_user SET client_encoding TO 'utf8';
GRANT ALL PRIVILEGES ON DATABASE vaani TO vaani_user;
\q
```

Use that same password in `.env` in step 4.

## 3. Get the code onto the server

```bash
sudo mkdir -p /opt/vaani
sudo chown $USER:$USER /opt/vaani
git clone https://github.com/Ranadhir-das/aspiring-crm.git /opt/vaani
cd /opt/vaani
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

## 4. Configure environment

```bash
cp deploy/.env.production.example .env
nano .env   # fill in SECRET_KEY and DB_PASSWORD at minimum — the rest already
            # matches this droplet/domain
```

Generate a real secret key:
```bash
python -c "import secrets; print(secrets.token_urlsafe(50))"
```

## 5. Database schema, static files, face models

```bash
python manage.py migrate
python manage.py collectstatic --noinput
python manage.py install_face_models   # downloads + SHA-256-verifies the ONNX models
python manage.py createsuperuser       # your first admin login
```

## 6. Daphne as a service

Daphne (already in `requirements.txt`) serves both the regular CRM pages/API
*and* the team-chat WebSocket over one process — there's no separate Gunicorn
step.

```bash
sudo cp deploy/vaani.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now vaani
sudo systemctl status vaani     # should say "active (running)"
```

If it doesn't start: `sudo journalctl -u vaani -n 50`. The paths in
`deploy/vaani.service` assume `/opt/vaani`; edit them if you cloned elsewhere.

## 7. Nginx reverse proxy

First add the WebSocket upgrade map (once, at the `http{}` level — not inside
a site file):

```bash
sudo nano /etc/nginx/nginx.conf
```
Add this inside the `http { ... }` block:
```
map $http_upgrade $connection_upgrade {
    default upgrade;
    ''      close;
}
```

Then install the site config:
```bash
sudo cp deploy/nginx.conf /etc/nginx/sites-available/vaani
sudo ln -s /etc/nginx/sites-available/vaani /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx
```

At this point `http://vaaniapp.co.in` should load Vaani over plain HTTP —
confirm that before moving to HTTPS.

## 8. HTTPS (required — do not skip)

```bash
sudo apt install -y certbot python3-certbot-nginx
sudo certbot --nginx -d vaaniapp.co.in -d www.vaaniapp.co.in
```

Certbot edits the Nginx config to add the HTTPS server block and an HTTP→HTTPS
redirect, and sets up auto-renewal. Confirm `https://vaaniapp.co.in` loads.

## 9. Firewall

```bash
sudo ufw allow OpenSSH
sudo ufw allow 'Nginx Full'   # 80 + 443
sudo ufw enable
```

Postgres (5432) and Redis (6379) should **not** be open externally — both are
only accessed locally (`127.0.0.1`), which `ufw` leaves untouched by default.

## 10. Point the app at the new server

In `caller-app/.env.local`:
```
EXPO_PUBLIC_API_BASE_URL=https://vaaniapp.co.in/api/v1
```
Then `npx expo start -c` and reload, or rebuild (see step 11) — no more LAN-IP
dependency, this works from any network.

## 11. Build the release APK and host it on the download page

The `/app/` download page (linked from the CRM sidebar and the login page)
reads a file straight off *this server's* disk — it doesn't build the app
itself. Build locally as usual, then upload:

```bash
# on your dev machine, after step 10's .env.local change:
cd caller-app
./scripts/build-android.ps1
# produces android/app/build/outputs/apk/debug/app-debug.apk

scp android/app/build/outputs/apk/debug/app-debug.apk root@159.89.172.172:/opt/vaani/dist/vaani.apk
```

(`/opt/vaani/dist/vaani.apk` matches `CALLER_APK_PATH` in
`deploy/.env.production.example` — create the `dist/` folder first with
`mkdir -p /opt/vaani/dist` if it doesn't exist. No service restart needed; the
page re-reads the file's size/date on every load.)

This ships the **debug** build for now. Before real users install it, replace
this with a signed **release** APK (new Android signing keystore + a
`signingConfig` in `android/app/build.gradle` — the app's package id is now
`com.vaani.caller`, set once, before anyone installs it, so this is the right
time to also finalize the signing key). Anyone who already installed the app
under the old `com.anonymous.callerapp` id needs to uninstall it once before
installing the renamed one — they're different apps as far as Android is
concerned.

## Redeploying after code changes

```bash
cd /opt/vaani
git pull
source venv/bin/activate
pip install -r requirements.txt
python manage.py migrate
python manage.py collectstatic --noinput
sudo systemctl restart vaani
```

## Backups

Postgres holds everything, including attendance/report photos (stored as DB
blobs, not files — there's no `media/` directory to separately back up). At
minimum:
```bash
pg_dump -U vaani_user vaani > backup-$(date +%F).sql
```
Put that in a daily cron job writing somewhere off the VPS (e.g. synced to your
own machine or object storage) — a single-server backup that lives only on
that same server doesn't protect you if the server itself is lost.
