# Smart Mart — Render Deployment Guide

Step-by-step guide to deploy Smart Mart on Render with PostgreSQL and automated backups.

---

## 1. Create a PostgreSQL Database on Render

1. Go to [render.com](https://render.com) → **New** → **PostgreSQL**
2. Fill in:
   - **Name**: `smart-mart-db`
   - **Region**: Singapore (closest to Nepal)
   - **Plan**: Starter ($7/month) — includes daily backups retained for 7 days
3. Click **Create Database**
4. Once created, go to the database page and copy the **Internal Database URL**
   - It looks like: `postgresql://user:password@host/dbname`

---

## 2. Create the Web Service

1. Go to **New** → **Web Service**
2. Connect your GitHub repository
3. Fill in:
   - **Name**: `smart-mart`
   - **Region**: Singapore
   - **Branch**: `main`
   - **Runtime**: Python 3
   - **Build Command**: `pip install -r requirements.txt && bash build.sh`
   - **Start Command**: `gunicorn "smart_mart.app:create_app('production')" --bind 0.0.0.0:$PORT --workers 2 --threads 2 --worker-class gthread --timeout 120 --keep-alive 5 --max-requests 1000 --max-requests-jitter 100 --access-logfile -`
   - **Plan**: Starter ($7/month)

---

## 3. Set Environment Variables

In the web service → **Environment** tab, add these:

| Key | Value | Notes |
|-----|-------|-------|
| `FLASK_ENV` | `production` | |
| `SECRET_KEY` | *(generate below)* | Required — app won't start without it |
| `DATABASE_URL` | *(paste Internal Database URL from step 1)* | Required — app won't start without it |
| `ADMIN_PASSWORD` | *(your chosen password)* | Required — build fails without it |
| `ADMIN_USERNAME` | `admin` | Optional, defaults to `admin` |
| `BOT_SECRET` | *(generate a strong random value)* | Required for scheduled bot calls |
| `APP_URL` | `https://your-app.onrender.com` | Required by the cron service |
| `LOG_LEVEL` | `INFO` | |

**Generate a SECRET_KEY:**
```bash
python -c "import secrets; print(secrets.token_hex(32))"
```

> **Important:** Never use the same `SECRET_KEY` across environments. Never commit it to git.

---

## 4. Deploy

1. Click **Create Web Service**
2. Render will run `build.sh` which:
   - Installs dependencies
   - Creates all database tables
   - Runs schema migrations
   - Creates the admin user with your `ADMIN_PASSWORD`
3. Watch the build logs — look for `Build complete` at the end
4. Once deployed, visit your Render URL and log in with `admin` / your password

---

## 5. Automated Backups

### Render's Built-in Backups (PostgreSQL Starter plan)
- Daily automatic backups, retained for **7 days**
- To restore: Render dashboard → your database → **Backups** tab → click **Restore**
- No setup needed — it's automatic on the Starter plan

### Manual Backup via Smart Mart
- Log in as admin → **Admin** → **Backup** → **Create Backup**
- Downloads a JSON file of all your data
- Store this file somewhere safe (Google Drive, email to yourself, etc.)
- **Recommended:** Do this weekly as an extra safety net

### Backup Retention Recommendation
| Backup Type | Frequency | Retention | Where |
|-------------|-----------|-----------|-------|
| Render PostgreSQL | Daily (automatic) | 7 days | Render |
| Smart Mart JSON | Weekly (manual) | Forever | Google Drive / local |

---

## 6. Custom Domain (Optional)

1. Render dashboard → your web service → **Settings** → **Custom Domains**
2. Add your domain (e.g. `pos.yourshop.com`)
3. Add the CNAME record to your DNS provider as shown
4. Render provisions SSL automatically

---

## 7. Upgrading the Database Plan

If your shop grows and you need more storage or longer backup retention:

| Plan | Price | Storage | Backup Retention |
|------|-------|---------|-----------------|
| Starter | $7/mo | 1 GB | 7 days |
| Standard | $20/mo | 10 GB | 30 days |
| Pro | $65/mo | 50 GB | 90 days |

For most small retail shops, **Starter is sufficient for years**.

---

## 7b. Daily Bot (Automated Alerts)

The daily bot runs 5 tasks automatically:
- Low stock alerts (+ SMS to admin if Sparrow/Twilio configured)
- Overdue credit reminders (+ SMS to customers with phone numbers)
- Expiry date warnings
- Recurring expense reminders
- Daily business summary

**Setup on Render:**
1. The `render.yaml` includes a `cron` service (`smart-mart-daily-bot`)
2. After deploying, go to the cron service → **Environment** tab and set:
   - `APP_URL` = your web service URL (e.g. `https://smart-mart.onrender.com`)
   - `BOT_SECRET` = same value as the `BOT_SECRET` on your web service
3. The bot runs daily at **8:00 AM Nepal time** (2:15 AM UTC)

**To trigger manually:**
```bash
curl -X POST https://your-app.onrender.com/api/bot/run \
     -H "X-Bot-Secret: YOUR_BOT_SECRET"
```

**To enable SMS (optional):**
Add to your web service environment:
- `NOTIFICATION_PROVIDER` = `sparrow` (Nepal) or `twilio`
- For Sparrow: `SPARROW_TOKEN` = your token from sparrowsms.com
- For Twilio: `TWILIO_SID`, `TWILIO_TOKEN`, `TWILIO_FROM`

---

## 8. Monitoring

- **Logs**: Render dashboard → your service → **Logs** tab
- **Metrics**: CPU and memory usage visible in the dashboard
- **Uptime**: Render Starter web services spin down after 15 minutes of inactivity (free tier). Upgrade to a paid plan to keep it always-on.

> **Note:** The Starter web service plan ($7/mo) has a spin-down on inactivity. For a production shop that needs instant response, use the **Standard** plan ($25/mo) which stays always-on.

---

## 9. Checklist Before Going Live

- [ ] `DATABASE_URL` set to PostgreSQL Internal URL
- [ ] `SECRET_KEY` set to a strong random value
- [ ] `ADMIN_PASSWORD` set to a strong password (not `Admin@1234`)
- [ ] First login works at your Render URL
- [ ] Shop settings configured (name, address, phone, PAN)
- [ ] At least one product added
- [ ] Test sale created and invoice printed
- [ ] Manual backup downloaded and stored safely
- [ ] Staff users created with appropriate permissions

---

## 10. Troubleshooting

**Build fails with "ADMIN_PASSWORD environment variable is not set"**
→ Add `ADMIN_PASSWORD` in the Environment tab and redeploy.

**Build fails with "DATABASE_URL must be set in production"**
→ Add `DATABASE_URL` (the Internal Database URL from your Render PostgreSQL) and redeploy.

**App loads but shows 500 error**
→ Check the Logs tab. Usually a missing migration or environment variable.

**"column does not exist" error**
→ The app auto-runs migrations on startup. If it persists, trigger a manual redeploy.

**Forgot admin password**
→ Go to the login page → "Forgot password?" → enter username `admin` → check the Render logs for the reset link.
