# Smart Mart — Data Flow & Interconnection Map

## What is automatic (enter once, syncs everywhere)

| Action | Auto-syncs to |
|--------|--------------|
| Create Sale | Stock ↓, StockMovement, Customer (upsert), LoyaltyWallet (points), AuditLog, BI cache invalidated |
| Create Purchase | Stock ↑, StockMovement, product.cost_price updated, Expense (type=purchase), BI OPEX (via sync), BI PurchaseBatch (draft auto-created) |
| Create Expense | BI OperatingExpense (mirror via expense_sync) |
| Edit Expense | BI OPEX mirror updated |
| Delete Expense | BI OPEX mirror deleted |
| Delete Sale | Stock reversed, StockMovement (reversal) |
| Finalize BI Batch | product.cost_price (weighted avg), BI InventoryLedger entries |

## What requires manual entry in only ONE place

- Products → enter once in Inventory
- Suppliers → enter once in Purchasing
- Customers → auto-created on first sale, or manually in Customers section
- Expenses → enter once in Expense Management (auto-syncs to BI)
- Sales → enter once in POS
- Purchases → enter once in Purchasing

## Known design decisions (not bugs)

- **BI Purchase Batches**: Advanced cost allocation tool. Regular purchases auto-create a draft batch. You only need to use the BI Batch UI if you want to allocate shared freight/customs costs across items.
- **BI OPEX page**: Shows same data as Expense Management (auto-synced). No need to enter separately.
- **Cash flow**: Derived from Sale + Expense tables. No separate entry needed.
- **Loyalty points**: Auto-calculated from sale amount using Shop Settings rates.
