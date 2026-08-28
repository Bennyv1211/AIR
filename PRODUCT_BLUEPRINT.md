# AIR Product Blueprint

Last updated: August 27, 2026

## Product vision

AIR, short for **Artificial Intelligence Replenisher**, is an operations decision engine that turns incoming business data into replenishment actions.

The product should answer a simple question:

**What do we need to replenish, when, and why?**

## Problem AIR solves

Teams often have data scattered across spreadsheets, ERPs, e-commerce tools, and warehouse systems, but they still make replenishment decisions manually. That creates:

- stockouts
- over-ordering
- slow reaction to demand changes
- poor visibility into supplier risk
- wasted time building reorder reports by hand

AIR should reduce that friction by turning raw operational data into ranked, explainable recommendations.

## Ideal first customer

The best MVP user is a small or mid-sized business that:

- sells physical products
- tracks inventory in spreadsheets or a basic system
- needs better reorder visibility
- is comfortable uploading CSV data weekly or daily

Examples:

- e-commerce brands
- retail operators
- small warehouses
- distributors

## MVP outcome

A user uploads inventory and demand data, and AIR returns:

- which SKUs need reorder attention
- recommended order quantities
- urgency ranking
- projected risk based on lead time and demand

## Core workflow

1. Data ingestion
   AIR accepts CSV files, API payloads, or manual entries.

2. Data normalization
   AIR maps incoming fields into a standard replenishment schema.

3. Decision engine
   AIR calculates reorder points, target stock, stock gaps, and urgency.

4. Recommendation delivery
   AIR shows recommendations in the API, exports them, and later in a dashboard.

5. Action layer
   AIR eventually pushes purchase suggestions, notifications, or system updates.

## MVP features

- JSON ingestion API
- CSV upload ingestion
- local dataset persistence
- replenishment scoring engine
- recommendations API
- health endpoint

## Phase 2 features

- forecasting from historical sales
- supplier-specific lead time logic
- email or Slack alerts
- dashboard with filters and search
- exception reporting
- reorder explanation text for each SKU

## Phase 3 features

- ERP and e-commerce integrations
- automated purchase order drafting
- multi-warehouse balancing
- what-if simulations
- machine learning demand forecasting
- role-based access and team workflows

## Users and roles

### Operator

Uses AIR daily to see what needs replenishment.

### Inventory manager

Reviews recommendations, adjusts thresholds, and approves action.

### Operations leader

Monitors high-risk products and supplier performance.

### Admin

Configures data mappings, integrations, and global settings.

## Core screens

### 1. Upload screen

- upload CSV
- preview mapped columns
- validate missing fields
- ingest dataset

### 2. Recommendations screen

- list of SKUs
- current stock
- reorder point
- target stock
- recommended quantity
- priority
- explanation

### 3. Item detail screen

- item trend
- historical demand
- supplier lead time
- reorder history
- notes

### 4. Settings screen

- default safety stock rules
- review period
- supplier parameters
- data mappings

## Data model direction

### Core entities

- Product
- InventorySnapshot
- DemandSnapshot
- Supplier
- ReplenishmentRule
- Recommendation
- IngestionJob

## Suggested database shape later

### Product

- id
- sku
- name
- supplier_id
- min_order_qty
- active

### InventorySnapshot

- product_id
- current_stock
- warehouse
- captured_at

### DemandSnapshot

- product_id
- daily_demand
- weekly_demand
- captured_at

### Supplier

- id
- name
- default_lead_time_days
- reliability_score

### Recommendation

- product_id
- reorder_point
- target_stock
- stock_gap
- recommended_order_qty
- priority
- generated_at

## AI layer ideas

The “AI” part of AIR should be more than branding. It can grow into:

- demand trend detection
- anomaly detection on sales spikes
- natural-language recommendation explanations
- simulation of delayed suppliers
- dynamic safety stock suggestions

Example explanation:

“Reorder SKU-104 now. At the current demand rate, stock is projected to fall below the reorder point before the supplier lead time window closes.”

## Business model ideas

- monthly SaaS subscription by number of SKUs
- tiered plans by integrations and forecast features
- premium enterprise plan for multi-warehouse automation

## Recommended build order

1. CSV upload and data validation
2. Recommendation API and ranking logic
3. basic dashboard
4. alerting
5. forecasting
6. integrations

## Immediate next milestone

Make AIR useful for one real workflow:

**A user uploads a CSV and immediately gets a ranked replenishment list.**
