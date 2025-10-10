from __future__ import annotations

import types
from datetime import date

from app.models.schemas import Transaction
from app.services import parser as parser_service
from app.services.parser import parse_pdf


class FakePage:
    def __init__(self, tables=None, text="") -> None:
        self._tables = tables or []
        self._text = text

    def extract_tables(self):
        return self._tables

    def extract_text(self):
        return self._text


class FakePDF:
    def __init__(self, pages) -> None:
        self.pages = pages

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        return False


def make_transaction() -> Transaction:
    return Transaction(
        id="txn-1",
        date=date(2024, 5, 1),
        description="Sample",
        debit=None,
        credit=None,
        balance=None,
    )


def test_parse_pdf_with_table(monkeypatch):
    table = [
        ["Date", "Description", "Credit"],
        ["2024-05-01", "Invoice 123", "120.00"],
    ]
    fake_pdf = FakePDF([FakePage(tables=[table])])

    monkeypatch.setattr(parser_service.pdfplumber, "open", lambda _: fake_pdf)

    result = parse_pdf(b"dummy")
    assert len(result.transactions) == 1
    txn = result.transactions[0]
    assert txn.description == "Invoice 123"
    assert txn.credit and float(txn.credit) == 120.0


def test_parse_pdf_fallback(monkeypatch):
    text_line = "2024-05-02 Grocery Store -45.67"
    fake_pdf = FakePDF([FakePage(tables=[], text=text_line)])

    monkeypatch.setattr(parser_service.pdfplumber, "open", lambda _: fake_pdf)

    result = parse_pdf(b"dummy")
    assert len(result.transactions) == 1
    txn = result.transactions[0]
    assert txn.debit and float(txn.debit) == 45.67
    assert "fallback" in txn.id
