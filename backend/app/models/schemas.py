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


class ConfirmTransactionsRequest(BaseModel):
    transaction_ids: List[str] = Field(..., description="List of transaction IDs to store in session")


class PdfPageContent(BaseModel):
    page_number: int
    text: str
    width: float
    height: float


class PdfTextResponse(BaseModel):
    pages: List[PdfPageContent]
    total_pages: int


class DetectedTransactionItem(BaseModel):
    row_number: int
    date: Optional[str] = None
    description: Optional[str] = None
    debit: Optional[str] = None
    credit: Optional[str] = None
    balance: Optional[str] = None
    confidence: float
    raw_text: str
    is_confirmed: bool = False


class DetectedTransactionsResponse(BaseModel):
    detected_transactions: List[DetectedTransactionItem]
    total_found: int
    confidence_summary: dict  # {"high": int, "medium": int, "low": int}


class ConfirmDetectedTransactionsRequest(BaseModel):
    """Request to confirm which detected transactions to convert to final transactions."""
    confirmed_transactions: List[DetectedTransactionItem] = Field(
        ..., 
        description="List of edited and confirmed transaction items"
    )
