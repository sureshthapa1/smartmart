# Smart Mart — Retail Management System

A full-featured, AI-powered retail management system built with Python/Flask. Designed for small-to-medium retail shops in Nepal and South Asia.

---

## Features

### Core POS & Operations
- **Point of Sale** — Fast billing with product search, barcode support, discounts, loyalty points, credit/Udharo
- **Inventory Management** — Products, categories, variants, stock adjustments, bulk upload, label printing
- **Purchase Management** — Supplier purchases, purchase orders, bulk upload, supplier returns
- **Sales Returns** — Full return flow with refund modes and stock restoration
- **Expenses** — Track rent, salary, utilities, and other costs
- **Stock Take** — Physical inventory counting with variance reporting
- **Stock Transfers** — Move stock between branches

### Finance & Reporting
- **10+ Reports** — Sales, profitability, dead stock, inventory valuation, staff efficiency, credit/Udharo, category performance
- **Cash Flow** — Income vs expense tracking with daily balance
- **Credit / Udharo** — Full credit sale tracking with due dates, collection, and risk scoring
- **Customer Credit Risk** — Automated risk scoring (Safe / Moderate / Risky) with admin override

### Customer & Loyalty
- **Customer Profiles** — Purchase history, loyalty points, credit outstanding
- **Loyalty Wallet** — Points earned on sales, redeemable at checkout
- **Online Orders** — Order creation, status tracking, delivery management, analytics

### AI & Intelligence
- **AI Business Advisor** — KPI scorecard, revenue forecasting, business insights
- **Trend Analysis** — Fast/slow movers, seasonal patterns, demand forecasting
- **Anomaly Detection** — Unusual sales patterns and stock movements
- **Customer Intelligence** — Segmentation, churn prediction, CLV, personalized offers
- **NLG Reports** — Auto-generated daily/weekly business summaries
- **Profit Leak Detection** — Identify discount losses and low-margin products
- **AI Chatbot** *(coming soon)* — Natural language queries about sales, stock, customers
- **Voice Assistant** *(coming soon)* — Voice-driven business queries via browser Web Speech API
- **Competitor Pricing** *(coming soon)* — Track competitor prices and get pricing suggestions
- **Cash Flow Prediction** *(coming soon)* — 30-day cash flow forecasting

### Admin & Security
- **Role-Based Access** — Admin and Staff roles with 50+ granular permissions
- **Audit Log** — Full trail of user actions
- **Backup** — One-click database backup download
- **Multi-Branch** — Branch management and stock transfers
- **Promotions** — Time-based discounts, buy-get-free, category/product scoped

---

## Tech Stack

| Layer | Technology |
|---|---|
| Backend | Python 3.11+, Flask 3.x |
| ORM | SQLAlchemy 2.x (Flask-SQLAlchemy) |
| Database | SQLite (dev) / PostgreSQL (prod) |
| Auth | Flask-Login + Flask-Bcrypt |
| Forms | Flask-WTF / WTForms |
| Frontend | Bootstrap 5.3, Bootstrap Icons, Chart.js, Inter font |
| Exports | ReportLab (PDF), openpyxl (Excel), csv |
| Testing | pytest, pytest-flask, Hypothesis (property-based) |
| Server | Gunicorn (production) |

---

## Project Structure

```
smart_mart/
├── app.py                  # Application factory
├── config.py               # Dev / Prod / Test configs
├── extensions.py           # db, login_manager, bcrypt
├── models/                 # 30+ SQLAlchemy models (one per entity)
├── services/               # 45+ business logic services
├── blueprints/             # 23 Flask blueprints (one per domain)
│   ├── auth/
│   ├── dashboard/
│   ├── inventory/
│   ├── sales/
│   ├── purchases/
│   ├── reports/
│   ├── operations/
│   ├── ai/
│   └── ...
├── templates/              # Jinja2 templates (one folder per blueprint)
└── static/                 # CSS, JS, uploads
```

---

## Quick Start

### 1. Clone & install

```bash
git clone https://github.com/sureshthapa1/smartmart.git
cd smartmart
pip install -r requirements.txt
```

### 2. Configure environment

Create a `.env` file:

```
SECRET_KEY=your-secret-key-here
FLASK_ENV=development
```

Generate a secret key:
```bash
python -c "import secrets; print(secrets.token_hex(32))"
```

### 3. Initialize database

```bash
python migrate.py
python seed.py        # optional: load sample data
```

### 4. Run

```bash
python run.py
```

Open **http://127.0.0.1:5000** — default login: `admin` / (set during seed)

### Production

```bash
FLASK_ENV=production SECRET_KEY=your-key gunicorn "smart_mart.app:create_app('production')" --bind 0.0.0.0:5000 --workers 2
```

---

## Running Tests

```bash
pytest tests/ -q
```

84 tests covering unit, integration, and property-based tests (Hypothesis).

---

## Known Limitations

The following features are scaffolded but not yet active. Each shows a "coming soon" page:

| Feature | Status | Notes |
|---|---|---|
| **AI Chatbot** | Coming soon | Will support plain-English queries about sales, stock, profit, and customers |
| **Voice Assistant** | Coming soon | Will use the browser's Web Speech API — no extra software required |
| **Competitor Pricing** | Coming soon | Manual price entry + AI-powered pricing suggestions |
| **Cash Flow Prediction** | Coming soon | 30-day forecast using moving averages and day-of-week patterns |

---

## Screenshots

> Dashboard · POS · Reports · AI Insights · Permissions

*(Add screenshots here)*

---

## License

MIT
