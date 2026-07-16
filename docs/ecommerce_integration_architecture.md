# GoldKernel E-commerce + POS Integration Architecture

This document defines the integrated customer website, admin dashboard, API backend, and POS synchronization plan for GoldKernel Dry Fruits in Nepal.

## 1. System Architecture Diagram

```text
Customers
  |
  v
Next.js Customer Website
  - home, product listing, product details
  - cart, checkout, login/register
  - order tracking/history
  |
  | HTTPS JSON API
  v
API Backend / Integration Facade
  - implemented now inside existing Flask POS app
  - future-ready for a separate FastAPI edge if needed
  - validates input, checks stock, creates orders
  - reserves stock, records payments, writes sync logs
  |
  +--------------------------+
  |                          |
  v                          v
Existing POS Database        Payment Providers
PostgreSQL recommended       eSewa, Khalti, COD
  |                          |
  v                          v
Existing POS UI              Payment callbacks/webhooks
http://127.0.0.1:5000        update payment/order state
  |
  v
POS Online Orders Section
/online-orders/
```

## 2. Technology Stack

Frontend: Next.js
Reason: customer SEO, product pages, server-side checkout calls that keep API keys private, and a clean path for customer account pages.

Backend: existing Flask POS app as the first integration API
Reason: the POS is already Flask with SQLAlchemy models for products, customers, and online orders. Extending it is safer than adding a second backend before the integration contract is proven. If a separate API layer is later required, FastAPI is the preferred Python edge because it matches the existing Python ecosystem.

Database: PostgreSQL for production
Reason: stronger concurrency, row locking, backups, and safer inventory transactions than SQLite. SQLite can stay for local development.

Real time: Redis queue/pub-sub plus WebSockets or Server-Sent Events
Reason: order/status/inventory events can be retried, replayed, and pushed to website/admin screens.

Deployment: VPS
Reason: the POS integration needs predictable network access, database backups, TLS, worker processes, and reverse proxy control.

## 3. Data Flow

Website to POS order flow:

```text
Customer checkout
  -> POST /api/orders/create with Idempotency-Key
  -> API validates customer/items/payment
  -> API checks POS product.quantity minus active reservations
  -> API creates online_orders + online_order_items
  -> API creates stock_reservations, payment row, sync_logs row
  -> Order appears in POS /online-orders/
```

POS to website status flow:

```text
POS staff updates order status
  -> POS route calls ecommerce_sync.apply_order_status()
  -> confirmed/packed/shipped/delivered consumes reservation once
  -> cancelled releases reservation or restores consumed stock
  -> Website reads GET /api/orders or receives future webhook/event
```

POS to website inventory flow:

```text
POS inventory is master
  -> Website reads GET /api/products and GET /api/inventory
  -> available_quantity = products.quantity - active unexpired reservations
  -> Future worker publishes inventory.updated events after POS stock changes
```

Conflict handling:

- POS wins inventory conflicts.
- Website creates reservations, not final deductions.
- Final stock deduction happens only when an order reaches confirmed or later.
- If POS stock drops below reserved quantity before confirmation, the API returns 409 and the order needs manual review.
- Duplicate order posts are prevented with `Idempotency-Key` and `sync_logs.idempotency_key`.

Status mapping:

| Website status | POS status |
|---|---|
| pending | pending |
| confirmed | confirmed |
| packed | preparing |
| shipped | out_for_delivery |
| delivered | delivered |
| cancelled | cancelled |

## 4. Implemented API Documentation

Authentication:

- Protected endpoints accept `Authorization: Bearer <key>`, `X-API-Key`, `X-Website-API-Key`, or `X-POS-API-Key`.
- Configure `ECOMMERCE_API_KEY` for website/backend calls.
- Configure `POS_SYNC_API_KEY` for POS sync workers/webhooks.
- Existing admin browser sessions can also access protected endpoints.
- Local debug/testing mode allows calls without keys only when no API key env var is configured.

### POST /api/orders/create

Creates a website order inside POS Online Orders and reserves stock.

Request:

```json
{
  "customer": {
    "name": "Sita Sharma",
    "phone": "9800000000",
    "email": "sita@example.com",
    "address": "Kathmandu",
    "area": "Lazimpat"
  },
  "items": [
    {
      "product_id": 1,
      "quantity": 2,
      "unit_price": 850
    }
  ],
  "payment": {
    "method": "cod",
    "status": "pending",
    "provider": "cod",
    "transaction_id": null
  },
  "delivery_charge": 100,
  "discount_amount": 0,
  "reservation_minutes": 30,
  "notes": "Leave at reception"
}
```

Headers:

```text
Idempotency-Key: checkout-cart-uuid
X-Website-API-Key: <ECOMMERCE_API_KEY>
```

Response 201:

```json
{
  "ok": true,
  "duplicate": false,
  "reservation_expires_at": "2026-06-07T12:30:00+00:00",
  "order": {
    "id": 12,
    "order_number": "GK-20260607-A1B2C3",
    "status": "pending",
    "pos_status": "pending",
    "amounts": {
      "subtotal": 1700,
      "delivery_charge": 100,
      "discount": 0,
      "grand_total": 1800,
      "currency": "NPR"
    }
  }
}
```

Errors:

- 400 invalid payload
- 401 missing/invalid API key
- 404 product not found
- 409 insufficient stock

### GET /api/orders

Lists orders for admin website/sync clients.

Query params:

```text
status=pending|confirmed|packed|shipped|delivered|cancelled
order_number=GK-20260607-A1B2C3
limit=100
```

Response:

```json
{
  "ok": true,
  "orders": [
    {
      "order_number": "GK-20260607-A1B2C3",
      "status": "confirmed",
      "pos_status": "confirmed",
      "customer": {
        "name": "Sita Sharma",
        "phone": "9800000000"
      }
    }
  ]
}
```

### PUT /api/orders/update-status

Updates order status and applies reservation/stock rules.

Request:

```json
{
  "order_number": "GK-20260607-A1B2C3",
  "status": "confirmed",
  "note": "Confirmed by POS staff",
  "actor": "admin"
}
```

Response:

```json
{
  "ok": true,
  "order": {
    "order_number": "GK-20260607-A1B2C3",
    "status": "confirmed"
  }
}
```

### GET /api/products

Public product catalog for the website.

Query params:

```text
q=almond
category=Dry Fruits
limit=200
```

Response:

```json
{
  "ok": true,
  "products": [
    {
      "id": 1,
      "sku": "ALM-001",
      "name": "Premium Almonds",
      "category": "Dry Fruits",
      "price": 850,
      "stock_quantity": 10,
      "reserved_quantity": 2,
      "available_quantity": 8,
      "is_low_stock": false
    }
  ]
}
```

### GET /api/inventory

Protected inventory snapshot from POS.

Response:

```json
{
  "ok": true,
  "source": "pos",
  "synced_at": "2026-06-07T12:00:00+00:00",
  "inventory": []
}
```

### POST /api/sync-pos-order

Creates/syncs an order into POS Online Orders. Same payload as `/api/orders/create`; intended for a website backend or queue worker.

Response includes:

```json
{
  "ok": true,
  "sync_target": "pos_online_orders",
  "order": {}
}
```

### POST /api/sync-inventory

Accepts POS inventory updates and returns the latest snapshot.

Request:

```json
{
  "items": [
    {
      "sku": "ALM-001",
      "quantity": 25,
      "selling_price": 850,
      "is_active": true
    }
  ]
}
```

Response:

```json
{
  "ok": true,
  "updated": 1,
  "inventory": []
}
```

## 5. Database Schema

Implemented/current table mapping:

| Required table | Current table/model |
|---|---|
| products | `products` / `Product` |
| inventory | `products.quantity` plus `stock_reservations` |
| customers | `customers` / `Customer` |
| orders | `online_orders` / `OnlineOrder` |
| order_items | `online_order_items` / `OnlineOrderItem` |
| payments | `payments` / `EcommercePayment` |
| stock_reservations | `stock_reservations` / `StockReservation` |
| admin_users | `users` / `User` |
| sync_logs | `sync_logs` / `SyncLog` |

Key relationships:

- `products.id` -> `online_order_items.product_id`
- `products.id` -> `stock_reservations.product_id`
- `online_orders.id` -> `online_order_items.order_id`
- `online_orders.id` -> `payments.order_id`
- `online_orders.id` -> `stock_reservations.order_id`
- `users.id` -> existing POS-created order/user/audit references

Important constraints/indexes:

- `products.sku` unique
- `online_orders.order_number` unique
- `stock_reservations.reservation_key` unique
- `sync_logs.idempotency_key` unique
- `payments(provider, transaction_id)` unique
- stock reservation indexes on product/status, order/status, expires_at
- sync log indexes on direction/status, entity, created_at

## 6. Folder Structure

Implemented now:

```text
smart_mart/
  blueprints/
    ecommerce_api/
      __init__.py
      routes.py
  models/
    ecommerce.py
    online_order.py
    product.py
    customer.py
  services/
    ecommerce_sync.py
tests/
  unit/
    test_ecommerce_api.py
docs/
  ecommerce_integration_architecture.md
```

Recommended Next.js app structure for the next phase:

```text
ecommerce-web/
  src/
    app/
      page.tsx
      products/page.tsx
      products/[slug]/page.tsx
      cart/page.tsx
      checkout/page.tsx
      account/orders/page.tsx
      track/[orderNumber]/page.tsx
      admin/
        page.tsx
        products/page.tsx
        inventory/page.tsx
        orders/page.tsx
        customers/page.tsx
        reports/page.tsx
    components/
      product-card.tsx
      cart-drawer.tsx
      order-status-timeline.tsx
      admin-table.tsx
    lib/
      api.ts
      auth.ts
      payments.ts
      format.ts
```

## 7. Admin Dashboard Scope

Existing POS already covers much of the admin system. The web admin should call the same APIs and later add:

- Product management: add/edit/delete, image upload, categories, pricing.
- Inventory management: stock view, stock adjustments, low stock alerts.
- Order management: all orders, status changes, delivery assignment, cancellation.
- Customer management: profiles, addresses, order history.
- Reports: daily sales, profit summary, top products, inventory report.

For phase one, POS remains the authoritative admin dashboard. The Next.js admin can be added as a remote-friendly management layer.

## 8. Customer Website Scope

Pages to build next:

- Homepage with featured dry fruits and offers
- Product listing with search/filter/category
- Product details with stock availability
- Cart with reservation countdown
- Checkout with COD/eSewa/Khalti
- Login/register
- Order tracking
- Order history

## 9. Real-Time Sync Design

Current build:

- API writes `sync_logs`.
- Website/admin clients can poll `GET /api/orders` and `GET /api/inventory`.
- Idempotency prevents duplicate order creation.
- Failed sync attempts are logged.

Production real-time layer:

```text
Flask POS/API
  -> write DB transaction
  -> write sync_logs
  -> enqueue Redis event
  -> worker sends webhook to website backend
  -> website broadcasts WebSocket/SSE event to browser
```

Event examples:

```json
{
  "type": "order.created",
  "order_number": "GK-20260607-A1B2C3"
}
```

```json
{
  "type": "inventory.updated",
  "sku": "ALM-001",
  "available_quantity": 8
}
```

Retry strategy:

- Each event has an idempotency key.
- Failed webhooks increment `sync_logs.attempts`.
- Retry with exponential backoff.
- Move to manual review after max retries.

## 10. Inventory Control

Rules implemented:

- Website order creation reserves stock for 15-30 minutes.
- Product availability is POS stock minus active reservations.
- Final deduction occurs only on confirmed/packed/shipped/delivered.
- Cancellation releases active reservations.
- Cancellation after confirmation restores consumed stock.

Next improvement:

- Add a scheduled cleanup job for expired reservations.
- Add cart-level reservation endpoint before checkout if you want stock held before order submission.

## 11. Nepal Payment Integration

Supported methods in the order/payment model:

- `esewa`
- `khalti`
- `cod`

Payment adapter flow:

```text
Checkout
  -> create pending order/reservation
  -> initialize payment provider
  -> redirect/open provider checkout
  -> provider callback/webhook verifies transaction
  -> update payments.status
  -> update online_orders.payment_status
  -> POS order remains visible for staff confirmation
```

Provider credentials must be server-side only. Do not put merchant secrets in the browser.

## 12. Security

Implemented:

- API key protection for order, inventory, and sync endpoints.
- Existing Flask-Login admin session support.
- Rate limits on API endpoints.
- Input validation in service layer.
- Sync logs for success/failure audit.
- Idempotency keys for duplicate prevention.

Next:

- Customer JWT/session auth in Next.js.
- Admin RBAC for the remote dashboard.
- Webhook signature verification.
- Redis-backed rate limiting in production.
- Structured audit entries for every admin change.

## 13. Deployment Steps

1. Back up the existing POS database.
2. Move production DB to PostgreSQL if it is still SQLite.
3. Set environment variables:

```text
SECRET_KEY=...
DATABASE_URL=postgresql://...
ECOMMERCE_API_KEY=...
POS_SYNC_API_KEY=...
ESEWA_CLIENT_ID=...
ESEWA_CLIENT_SECRET=...
KHALTI_SECRET_KEY=...
IMEPAY_MERCHANT_CODE=...
REDIS_URL=redis://...
```

4. Install dependencies and run migrations/startup:

```bash
pip install -r requirements.txt
flask db upgrade
python run.py
```

5. Deploy behind Nginx/Caddy with HTTPS.
6. Deploy Next.js storefront with server-side API proxy routes.
7. Configure payment callback URLs.
8. Configure DB backups and log rotation.
9. Add Redis worker/WebSocket process for real-time sync.
10. Smoke test:

```bash
curl http://127.0.0.1:5000/api/products
curl -H "X-API-Key: $ECOMMERCE_API_KEY" http://127.0.0.1:5000/api/inventory
```
