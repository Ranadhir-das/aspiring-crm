# Deploying aspiring-crm to a VPS

Tested against a plain Ubuntu 22.04/24.04 VPS (DigitalOcean, Hetzner, Lightsail — the
steps are identical regardless of provider). Run everything below over SSH on the server
unless marked otherwise.

## 0. Before you start

- Pick a domain or subdomain (`crm.example.com`) and point its DNS `A` record at the
  VPS's public IP. No domain yet? A free DDNS hostname (DuckDNS, No-IP) works the same
  way for Certbot/Nginx below — just use that hostname everywhere `crm.example.com`
  appears.
- Note the VPS's public IP; you'll need it for the DNS record.

## 1. System packages

```bash
sudo apt update && sudo apt upgrade -y
sudo apt install -y python3-venv python3-pip postgresql postgresql-contrib \
    nginx git libglib2.0-0 libgl1 libsm6 libxext6
```

The last four (`libglib2.0-0 libgl1 libsm6 libxext6`) are for `opencv-python-headless`
— without them the face-verification code will fail to import `cv2` on a minimal
server image, even though it works fine on your dev machine.

## 2. PostgreSQL

```bash
sudo -u postgres psql
```
```sql
CREATE DATABASE aspiring_crm;
CREATE USER aspiring_crm_user WITH PASSWORD 'a-real-strong-password';
ALTER ROLE aspiring_crm_user SET client_encoding TO 'utf8';
GRANT ALL PRIVILEGES ON DATABASE aspiring_crm TO aspiring_crm_user;
\q
```

Use that same password in `.env` in step 4. **Do not** reuse the placeholder
`YOUR_STRONG_PASSWORD` from local dev.

## 3. Get the code onto the server

```bash
sudo mkdir -p /opt/aspiring-crm
sudo chown $USER:$USER /opt/aspiring-crm
git clone https://github.com/Ranadhir-das/aspiring-crm.git /opt/aspiring-crm
cd /opt/aspiring-crm
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

## 4. Configure environment

```bash
cp deploy/.env.production.example .env
nano .env   # fill in SECRET_KEY, DB_PASSWORD, ALLOWED_HOSTS, CORS/CSRF origins
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
python manage.py createsuperuser       # your first admin login for the CRM
```

## 6. Gunicorn as a service

```bash
sudo cp deploy/aspiring-crm.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now aspiring-crm
sudo systemctl status aspiring-crm     # should say "active (running)"
```

If it doesn't start, check `sudo journalctl -u aspiring-crm -n 50` — the file paths
in `deploy/aspiring-crm.service` assume `/opt/aspiring-crm`; edit them if you cloned
elsewhere.

## 7. Nginx reverse proxy

```bash
sudo cp deploy/nginx.conf /etc/nginx/sites-available/aspiring-crm
sudo nano /etc/nginx/sites-available/aspiring-crm   # replace crm.example.com
sudo ln -s /etc/nginx/sites-available/aspiring-crm /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx
```

At this point `http://crm.example.com` should load the CRM over plain HTTP — confirm
that before moving to HTTPS.

## 8. HTTPS (required — do not skip)

```bash
sudo apt install -y certbot python3-certbot-nginx
sudo certbot --nginx -d crm.example.com
```

Certbot edits the Nginx config to add the HTTPS server block and an HTTP→HTTPS
redirect, and sets up auto-renewal. Confirm `https://crm.example.com` loads.

## 9. Firewall

```bash
sudo ufw allow OpenSSH
sudo ufw allow 'Nginx Full'   # 80 + 443
sudo ufw enable
```

Postgres (5432) should **not** be open externally — it's only accessed locally via
`DB_HOST=127.0.0.1`, which `ufw` leaves untouched by default.

## 10. Point caller-app at the new server

In `caller-app/.env.local`:
```
EXPO_PUBLIC_API_BASE_URL=https://crm.example.com/api/v1
```
Then `npx expo start -c` and reload the app. No more LAN-IP dependency — this works
from any network, not just your home Wi-Fi.

## Redeploying after code changes

```bash
cd /opt/aspiring-crm
git pull
source venv/bin/activate
pip install -r requirements.txt
python manage.py migrate
python manage.py collectstatic --noinput
sudo systemctl restart aspiring-crm
```

## Backups

Postgres holds everything, including attendance photos (stored as DB blobs, not
files — there's no `media/` directory to separately back up). At minimum:
```bash
pg_dump -U aspiring_crm_user aspiring_crm > backup-$(date +%F).sql
```
Put that in a daily cron job writing somewhere off the VPS (e.g. synced to your own
machine or object storage) — a single-server backup that lives only on that same
server doesn't protect you if the server itself is lost.
