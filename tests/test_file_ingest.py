from io import BytesIO

import pytest
from openpyxl import Workbook

from app.services.file_ingest import (
    parse_replenishment_file,
    parse_usage_file,
    preview_replenishment_file,
)


def test_parse_replenishment_file_returns_csv_records() -> None:
    content = "\n".join(
        [
            "sku,name,current_stock,daily_demand,lead_time_days,safety_stock,min_order_qty",
            "SKU-100,Widget,12,2.5,4,6,10",
        ]
    ).encode("utf-8")

    records = parse_replenishment_file("inventory.csv", content)

    assert len(records) == 1
    assert records[0].sku == "SKU-100"
    assert records[0].daily_demand == 2.5


def test_parse_replenishment_file_returns_excel_records() -> None:
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(
        [
            "Product Code",
            "Product Description",
            "On Hand",
            "Lead Time",
            "Safety Buffer",
            "MOQ",
            "Last Week",
        ]
    )
    sheet.append(["SKU-101", "Excel Widget", 15, 5, 6, 10, 21])

    buffer = BytesIO()
    workbook.save(buffer)

    records = parse_replenishment_file("inventory.xlsx", buffer.getvalue())

    assert len(records) == 1
    assert records[0].sku == "SKU-101"
    assert records[0].daily_demand == 3


def test_parse_replenishment_file_infers_weekly_columns_and_incoming_stock() -> None:
    content = "\n".join(
        [
            "Item Code,Description,On Hand,On Order,1wk,2wk,Lead Time,MOQ",
            "SKU-200,Messy Widget,10,5,14,28,6,12",
        ]
    ).encode("utf-8")

    records = parse_replenishment_file("messy.csv", content)

    assert len(records) == 1
    assert records[0].incoming_stock == 5
    assert records[0].daily_demand == pytest.approx((14 + 28) / 21)


def test_parse_replenishment_file_allows_zero_daily_demand() -> None:
    content = "\n".join(
        [
            "Item Code,Description,On Hand,Daily Usage",
            "SKU-201,Dormant Widget,10,0",
        ]
    ).encode("utf-8")

    records = parse_replenishment_file("dormant.csv", content)

    assert len(records) == 1
    assert records[0].daily_demand == 0


def test_parse_replenishment_file_allows_zero_minimum_order_quantity() -> None:
    content = "\n".join(
        [
            "Item Code,Description,On Hand,Daily Usage,MOQ",
            "SKU-202,Flexible Widget,10,2,0",
        ]
    ).encode("utf-8")

    records = parse_replenishment_file("flexible.csv", content)

    assert len(records) == 1
    assert records[0].min_order_qty == 0


def test_parse_replenishment_file_requires_core_business_columns() -> None:
    content = "last week,1wk,2wk\n12,12,24\n".encode("utf-8")

    with pytest.raises(ValueError, match="missing recognizable columns"):
        parse_replenishment_file("inventory.csv", content)


def test_parse_replenishment_file_requires_some_kind_of_demand_signal() -> None:
    content = "SKU,Description,On Hand\nA-100,Widget,12\n".encode("utf-8")

    with pytest.raises(ValueError, match="recognizable demand column"):
        parse_replenishment_file("inventory.csv", content)


def test_parse_replenishment_file_rejects_unsupported_extensions() -> None:
    with pytest.raises(ValueError, match=r"\.csv or \.xlsx"):
        parse_replenishment_file("inventory.txt", b"hello")


def test_parse_usage_file_accepts_messy_usage_headers() -> None:
    content = "\n".join(
        [
            "Item Code,Sales Date,Units Sold",
            "SKU-900,08/20/2026,12",
        ]
    ).encode("utf-8")

    records = parse_usage_file("usage.csv", content)

    assert len(records) == 1
    assert records[0].sku == "SKU-900"
    assert records[0].units_used == 12


def test_parse_usage_file_requires_usage_fields() -> None:
    content = "Item Code,Description\nSKU-900,Widget\n".encode("utf-8")

    with pytest.raises(ValueError, match="missing recognizable usage columns"):
        parse_usage_file("usage.csv", content)


def test_preview_replenishment_file_shows_mapping_sources() -> None:
    content = "\n".join(
        [
            "Item Code,Description,On Hand,On Order,1wk,Lead Time,MOQ",
            "SKU-200,Messy Widget,10,5,14,6,12",
        ]
    ).encode("utf-8")

    preview = preview_replenishment_file("messy.csv", content)

    assert preview.dataset_kind == "replenishment"
    assert any(item.field == "sku" and item.header == "Item Code" for item in preview.mappings)
