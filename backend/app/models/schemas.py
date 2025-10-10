from __future__ import annotations

from datetime import date
from decimal import Decimal
from enum import Enum
from typing import List, Optional

from pydantic import BaseModel, Field


class DownloadFormat(str, Enum):
    xlsx = "xlsx"
    csv = "csv"


class Transaction(BaseModel):
    id: str
    date: date
    description: str
    debit: Optional[Decimal] = None
    credit: Optional[Decimal] = None
    balance: Optional[Decimal] = None
    warnings: List[str] = Field(default_factory=list)


class SummaryTotals(BaseModel):
    row_count: int
    total_debit: Decimal
    total_credit: Decimal


class UploadResponse(BaseModel):
    transactions: List[Transaction]
    summary: SummaryTotals
    warnings: List[str] = Field(default_factory=list)


class TransactionsResponse(BaseModel):
    transactions: List[Transaction]
    summary: SummaryTotals
    warnings: List[str] = Field(default_factory=list)


class ErrorResponse(BaseModel):
    detail: str
