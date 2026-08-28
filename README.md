# AIR

AIR stands for **Artificial Intelligence Replenisher**.

This starter project is a first MVP for a system that gets fed operational data and turns it into replenishment recommendations. The current version focuses on inventory-style input so we have a clear, useful foundation to build on.

## What AIR does right now

- Accepts batches of product data through an API
- Accepts CSV and Excel uploads for inventory-style datasets
- Infers business meaning from messy spreadsheet headers
- Accepts an optional daily usage import to refine demand trends
- Can optionally use OpenAI for stronger semantic header mapping
- Can optionally use OpenAI to refine current-cycle order recommendations
- Lets you preview inferred column mappings before import
- Stores the latest records locally in a JSON data store
- Calculates reorder points and recommended order quantities
- Projects stockout timing and plain-English explanations
- Shows the final verdict in a browser table and exports it to Excel
- Exposes recommendations through a simple API and dashboard

## Example use case

You feed AIR records like:

- SKU
- Product name
- Current stock
- Daily demand
- Lead time in days
- Safety stock
- Minimum order quantity

AIR then estimates:

- Whether the item should be reordered
- The reorder point
- The stock gap
- A recommended order quantity
- A priority level

AIR can also recognize messy real-world labels like:

- `item code`, `product code`, `part number`
- `description`, `product description`
- `on hand`, `available`, `qoh`
- `on order`, `arriving`, `incoming`
- `last week`, `1wk`, `2wk`, `3wk`, `4wk`
- `lead time`, `vendor lead time`
- `buffer stock`, `moq`

AIR can optionally refine ordering analysis from a second daily-usage file with headers like:

- `sku`, `item code`, `product code`
- `date`, `usage date`, `sales date`
- `usage`, `units sold`, `qty sold`, `consumption`

## Project structure

```text
app/
  main.py
  models.py
  storage.py
  services/
    file_ingest.py
    replenisher.py
tests/
  test_api.py
  test_file_ingest.py
  test_replenisher.py
data/
  air_store.json
```

## Run locally

1. Create a virtual environment
2. Install dependencies
3. Start the API server

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -e .[dev]
uvicorn app.main:app --reload
```

The API will be available at `http://127.0.0.1:8000`.

## API endpoints

### `GET /health`

Health check.

### `GET /`

Simple AIR dashboard for viewing recommendations in a browser.

### `POST /datasets/ingest`

Ingest a batch of replenishment records.

Example payload:

```json
{
  "source": "demo-upload",
  "records": [
    {
      "sku": "SKU-1001",
      "name": "Widget A",
      "current_stock": 18,
      "daily_demand": 5,
      "lead_time_days": 4,
      "safety_stock": 10,
      "min_order_qty": 20
    }
  ]
}
```

### `GET /recommendations`

Returns the current replenishment recommendations.

Each recommendation now includes:

- `days_until_stockout`
- `projected_stockout_date`
- `demand_source`
- `explanation`

AIR only returns the final verdict after required planner questions are answered.

### `GET /recommendations/export`

Exports the final recommendation verdict as an Excel `.xlsx` file.

If AIR still needs required answers, export is blocked until those answers are provided.

### `POST /datasets/ingest-file`

Accepts a CSV or Excel upload plus a `source` form field.

Supported formats:

- `.csv`
- `.xlsx`

AIR infers likely column meanings instead of requiring exact header names.

Recognized patterns include:

- `sku`, `item code`, `product code`
- `name`, `description`, `product description`
- `current stock`, `on hand`, `available`, `qoh`
- `daily demand`, `daily usage`, `daily velocity`
- `last week`, `1wk`, `2wk`, `3wk`, `4wk`
- `lead time`, `vendor lead time`
- `safety stock`, `buffer stock`
- `moq`, `minimum order qty`
- `on order`, `arriving`, `incoming`

If a direct daily demand column is missing, AIR estimates daily demand from weekly columns such as `last week`, `1wk`, `2wk`, `3wk`, or `4wk`.

### `POST /datasets/preview-file`

Previews AIR's inferred inventory column mappings before import.

### `POST /datasets/preview-usage-file`

Previews AIR's inferred daily usage column mappings before import.

### `POST /datasets/ingest-usage-file`

Accepts an optional CSV or Excel daily-usage file plus a `source` form field.

AIR uses this file to refine demand by SKU from recent usage history and drive replenishment recommendations from actual daily outbound movement.

Recognized patterns include:

- `sku`, `item code`, `product code`
- `date`, `usage date`, `sales date`
- `usage`, `units sold`, `qty sold`, `consumption`

Example:

```powershell
curl -X POST "http://127.0.0.1:8000/datasets/ingest-file" `
  -F "source=sample-csv" `
  -F "file=@sample_inventory.csv"
```

```powershell
curl -X POST "http://127.0.0.1:8000/datasets/ingest-usage-file" `
  -F "source=usage-history" `
  -F "file=@sample_daily_usage.csv"
```

## Optional OpenAI setup

AIR works without OpenAI, but if you want stronger semantic spreadsheet understanding, set an API key in your server environment.

PowerShell example:

```powershell
$env:OPENAI_API_KEY="your_api_key_here"
$env:OPENAI_MODEL="gpt-5.6-luna"
uvicorn app.main:app --reload
```

When `OPENAI_API_KEY` is set, AIR will try OpenAI-assisted header mapping during file import and can also run an AI planning review on top of the rules-based replenishment result. AIR still falls back to its local analysis if the AI step is unavailable.

You can also place these values in [`.env.example`](C:/AIR/.env.example) style form by creating a local `.env` file in [C:\AIR](C:/AIR). AIR loads `.env` automatically on startup.

## Deploy to Render

AIR now includes a Render Blueprint file at [render.yaml](C:/AIR/render.yaml).

### Quick setup

1. Push this project to GitHub.
2. In Render, click `New +` and choose `Blueprint`.
3. Connect your GitHub repository.
4. Render will detect `render.yaml` and propose the `air-replenisher` web service.
5. When prompted, set your `OPENAI_API_KEY`.
6. Deploy the Blueprint.

### Current Render settings

- Build command: `pip install -e .`
- Start command: `uvicorn app.main:app --host 0.0.0.0 --port $PORT`
- Health check path: `/health`
- Default model: `gpt-5.6-luna`

### Important storage note

By default, AIR stores its latest imported data and saved assumptions in a local JSON file.

Render's docs state that:
- Web services use an ephemeral filesystem by default.
- Free web services cannot attach a persistent disk.
- Paid web services can attach a persistent disk and preserve files written under the disk mount path.

For AIR, this means:
- On the `free` plan, uploaded data and saved planner memory can reset after a restart or redeploy.
- If you want AIR to remember data reliably on the web, switch the service to a paid plan and attach a persistent disk.

If you attach a disk, set:

- Disk mount path: for example `/var/data`
- Environment variable: `AIR_DATA_PATH=/var/data/air_store.json`

Then AIR will keep its saved data on the disk instead of the temporary service filesystem.

## Next ideas

- Forecasting from historical sales
- Supplier-aware ordering logic
- Dashboard UI
- Alerts for urgent replenishment risks

## Product blueprint

The larger product direction is documented in [PRODUCT_BLUEPRINT.md](PRODUCT_BLUEPRINT.md).

## Assumption used for this MVP

I treated AIR as a replenishment engine for inventory and supply data because of the product name and your note that it will be "fed data." If you want, we can next turn this into a dashboard, add CSV upload, or reshape the model around a different type of replenishment data.
