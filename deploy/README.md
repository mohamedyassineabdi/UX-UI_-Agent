# UX/UI Auditor VPS Deployment

This deployment path runs the full Python UI server and audit pipeline on a Linux VPS.
It is the recommended path for website audits, screenshot audits, Vercel report deployment, and optional mobile audits.

## What You Need

- Ubuntu 22.04 or 24.04 server.
- SSH access to the server.
- A git remote containing this project.
- A domain name pointing to the server if you want HTTPS.
- Vercel CLI authentication for report deployment.
- AI provider configuration in `/etc/ux-ui-auditor.env`.
- Optional mobile stack: Android SDK, adb, Appium server, and an attached/emulated Android device.

## Server Setup

Run these commands on the server after cloning the project:

```bash
cd /opt/ux-ui-auditor
bash deploy/setup_ubuntu.sh
```

Create the production environment file:

```bash
sudo cp deploy/ux-ui-auditor.env.example /etc/ux-ui-auditor.env
sudo nano /etc/ux-ui-auditor.env
sudo chmod 600 /etc/ux-ui-auditor.env
```

Install the systemd service:

```bash
sudo cp deploy/ux-ui-auditor.service /etc/systemd/system/ux-ui-auditor.service
sudo systemctl daemon-reload
sudo systemctl enable ux-ui-auditor
sudo systemctl start ux-ui-auditor
sudo systemctl status ux-ui-auditor
```

The app will run locally on the server at:

```text
http://127.0.0.1:8787
```

## Nginx Reverse Proxy

Edit `deploy/nginx-ux-ui-auditor.conf` and replace `your-domain.com`.

Then install it:

```bash
sudo cp deploy/nginx-ux-ui-auditor.conf /etc/nginx/sites-available/ux-ui-auditor
sudo ln -s /etc/nginx/sites-available/ux-ui-auditor /etc/nginx/sites-enabled/ux-ui-auditor
sudo nginx -t
sudo systemctl reload nginx
```

Add HTTPS:

```bash
sudo apt install -y certbot python3-certbot-nginx
sudo certbot --nginx -d your-domain.com
```

## Vercel Report Deployment

The UI server deploys completed website and screenshot reports through the Vercel CLI.
Authenticate Vercel as the same user that runs the service:

```bash
cd /opt/ux-ui-auditor
sudo -u uxauditor -H vercel login
sudo -u uxauditor -H vercel link --yes
```

For non-interactive deployments, set `VERCEL_TOKEN` in `/etc/ux-ui-auditor.env` and make sure the project is linked.

## Updating The Deployment

Manual update:

```bash
cd /opt/ux-ui-auditor
sudo -u uxauditor git pull
source .venv/bin/activate
pip install -r requirements.txt
python -m playwright install --with-deps chromium
sudo systemctl restart ux-ui-auditor
```

## Logs

```bash
sudo journalctl -u ux-ui-auditor -f
```

## Mobile Audits

Mobile audits require Android tooling outside the Python app:

- Android SDK platform-tools with `adb`.
- Appium server reachable at the URL entered in the UI.
- Appium UiAutomator2 driver.
- Real Android device, emulator, or remote device farm.

The VPS can run website and screenshot audits without mobile tooling.
