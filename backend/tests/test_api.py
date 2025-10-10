from __future__ import annotations

import io
from datetime import date
from decimal import Decimal

import pytest

from app.models.schemas import Transaction
from app.services.parser import ParseResult


@pytest.fixture
def sample_transactions() -> list[Transaction]:
    return [
        Transaction(
            id="txn-1",
            date=date(2024, 5, 1),
            description="Invoice 123",
            debit=None,
            credit=Decimal("150.25"),
            balance=Decimal("1000.50"),
        )
    ]


def test_upload_and_download_flow(client, monkeypatch, sample_transactions):
    parse_result = ParseResult(transactions=sample_transactions, warnings=["Sample warning"])
    monkeypatch.setattr("app.api.routes.statements.parse_pdf", lambda _: parse_result)

    files = {"file": ("statement.pdf", b"%PDF-1.4", "application/pdf")}
    response = client.post("/api/statements/upload", files=files)
    assert response.status_code == 200
    payload = response.json()
    assert payload["summary"]["row_count"] == 1
    assert payload["warnings"] == ["Sample warning"]

    transactions_response = client.get("/api/statements/transactions")
    assert transactions_response.status_code == 200
    data = transactions_response.json()
    assert data["summary"]["total_credit"] == "150.25"

    download_response = client.get("/api/statements/download?format=csv")
    assert download_response.status_code == 200
    assert download_response.headers["content-type"].startswith("text/csv")
    assert b"Invoice 123" in download_response.content

    clear_response = client.delete("/api/statements/session")
    assert clear_response.status_code == 204

    missing_response = client.get("/api/statements/transactions")
    assert missing_response.status_code == 404
