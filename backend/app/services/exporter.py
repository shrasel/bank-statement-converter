from __future__ import annotations

import io
from decimal import Decimal
from typing import Iterable, Tuple

import pandas as pd

from app.models.schemas import Transaction


def _transactions_to_records(transactions: Iterable[Transaction]) -> Tuple[list[dict], dict]:
    records = []
    totals = {"debit": Decimal("0"), "credit": Decimal("0")}
    for txn in transactions:
        record = {
            "ID": txn.id,
            "Date": txn.date,
            "Description": txn.description,
            "Debit": txn.debit if txn.debit is not None else Decimal("0"),
            "Credit": txn.credit if txn.credit is not None else Decimal("0"),
            "Balance": txn.balance if txn.balance is not None else "",
        }
        records.append(record)
        if txn.debit:
            totals["debit"] += txn.debit
        if txn.credit:
            totals["credit"] += txn.credit
    return records, totals


def export_to_excel(transactions: Iterable[Transaction]) -> bytes:
    records, _ = _transactions_to_records(transactions)
    df = pd.DataFrame.from_records(records)
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        df.to_excel(writer, sheet_name="Transactions", index=False)
    return output.getvalue()


def export_to_csv(transactions: Iterable[Transaction]) -> bytes:
    records, _ = _transactions_to_records(transactions)
    df = pd.DataFrame.from_records(records)
    output = io.StringIO()
    df.to_csv(output, index=False)
    return output.getvalue().encode("utf-8")
