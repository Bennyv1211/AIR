from __future__ import annotations

import json
from pathlib import Path

from app.models import BusinessAssumptions, DatasetImportMetadata, ReplenishmentRecord, UsageRecord


class JsonDataStore:
    def __init__(self, file_path: Path) -> None:
        self.file_path = file_path
        self.file_path.parent.mkdir(parents=True, exist_ok=True)
        if not self.file_path.exists():
            self._write(
                {
                    "source": None,
                    "records": [],
                    "inventory_metadata": DatasetImportMetadata().model_dump(),
                    "usage_source": None,
                    "usage_records": [],
                    "usage_metadata": DatasetImportMetadata().model_dump(),
                    "assumptions": BusinessAssumptions().model_dump(),
                }
            )

    def save_records(
        self,
        source: str,
        records: list[ReplenishmentRecord],
        metadata: DatasetImportMetadata | None = None,
    ) -> None:
        payload = self._read()
        payload = {
            "source": source,
            "records": [record.model_dump() for record in records],
            "inventory_metadata": (metadata or DatasetImportMetadata(source=source)).model_dump(),
            "usage_source": payload.get("usage_source"),
            "usage_records": payload.get("usage_records", []),
            "usage_metadata": payload.get("usage_metadata", DatasetImportMetadata().model_dump()),
            "assumptions": payload.get("assumptions", BusinessAssumptions().model_dump()),
        }
        self._write(payload)

    def load_records(self) -> tuple[str | None, list[ReplenishmentRecord]]:
        payload = self._read()
        records = [ReplenishmentRecord.model_validate(item) for item in payload.get("records", [])]
        return payload.get("source"), records

    def load_inventory_metadata(self) -> DatasetImportMetadata:
        payload = self._read()
        metadata = payload.get("inventory_metadata", {})
        if not metadata and payload.get("source"):
            metadata = {"source": payload.get("source")}
        return DatasetImportMetadata.model_validate(metadata)

    def save_usage_records(
        self,
        source: str,
        usage_records: list[UsageRecord],
        metadata: DatasetImportMetadata | None = None,
    ) -> None:
        payload = self._read()
        payload.update(
            {
                "usage_source": source,
                "usage_records": [record.model_dump(mode="json") for record in usage_records],
                "usage_metadata": (metadata or DatasetImportMetadata(source=source)).model_dump(),
            }
        )
        self._write(payload)

    def load_usage_records(self) -> tuple[str | None, list[UsageRecord]]:
        payload = self._read()
        records = [UsageRecord.model_validate(item) for item in payload.get("usage_records", [])]
        return payload.get("usage_source"), records

    def load_usage_metadata(self) -> DatasetImportMetadata:
        payload = self._read()
        metadata = payload.get("usage_metadata", {})
        if not metadata and payload.get("usage_source"):
            metadata = {"source": payload.get("usage_source")}
        return DatasetImportMetadata.model_validate(metadata)

    def save_assumptions(self, assumptions: BusinessAssumptions) -> None:
        payload = self._read()
        payload["assumptions"] = assumptions.model_dump()
        self._write(payload)

    def load_assumptions(self) -> BusinessAssumptions:
        payload = self._read()
        return BusinessAssumptions.model_validate(payload.get("assumptions", {}))

    def _read(self) -> dict:
        with self.file_path.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
        payload.setdefault("source", None)
        payload.setdefault("records", [])
        payload.setdefault("inventory_metadata", DatasetImportMetadata().model_dump())
        payload.setdefault("usage_source", None)
        payload.setdefault("usage_records", [])
        payload.setdefault("usage_metadata", DatasetImportMetadata().model_dump())
        payload.setdefault("assumptions", BusinessAssumptions().model_dump())
        return payload

    def _write(self, payload: dict) -> None:
        with self.file_path.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2)
