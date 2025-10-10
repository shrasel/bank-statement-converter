from __future__ import annotations

from decimal import Decimal
from typing import Iterable

from app.models.schemas import SummaryTotals, Transaction


def compute_summary(transactions: Iterable[Transaction]) -> SummaryTotals:
    total_debit = Decimal("0")
    total_credit = Decimal("0")
    row_count = 0
    for txn in transactions:
        row_count += 1
        if txn.debit:
            total_debit += txn.debit
        if txn.credit:
            total_credit += txn.credit
    return SummaryTotals(row_count=row_count, total_debit=total_debit, total_credit=total_credit)
