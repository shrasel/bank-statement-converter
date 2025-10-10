from __future__ import annotations

import io
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Iterable, List, Optional

import pdfplumber

from app.models.schemas import Transaction

DATE_PATTERNS = [
    "%d/%m/%Y",
    "%m/%d/%Y",
    "%Y-%m-%d",
    "%d-%m-%Y",
    "%d/%m/%y",
    "%m/%d/%y",
    "%b %d, %Y",
    "%B %d, %Y",
    "%d %b %Y",
    "%d %B %Y",
]

HEADER_KEYWORDS = {
    "date": {"date"},
    "description": {"description", "details", "transaction", "narrative"},
    "debit": {"debit", "withdrawal", "paid out"},
    "credit": {"credit", "deposit", "paid in"},
    "amount": {"amount", "value"},
    "balance": {"balance", "running"},
}

AMOUNT_REGEX = re.compile(r"[-+]?\$?\£?\€?\d{1,3}(?:,\d{3})*(?:\.\d{1,2})?")
DATE_REGEX = re.compile(
    r"(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})|"  # numeric with slashes/dashes
    r"(\d{4}-\d{2}-\d{2})|"  # ISO format
    r"([A-Za-z]{3,9}\s+\d{1,2},\s+\d{2,4})|"  # Month name day, year
    r"(\d{1,2}\s+[A-Za-z]{3,9}\s+\d{2,4})"  # day Month year
)


@dataclass
class ParseResult:
    transactions: List[Transaction]
    warnings: List[str] = field(default_factory=list)


class StatementParser:
    def __init__(self, currency_default: str = "USD") -> None:
        self.currency_default = currency_default

    def parse(self, file_bytes: bytes) -> ParseResult:
        warnings: List[str] = []
        transactions = self._parse_with_tables(file_bytes, warnings)
        if not transactions:
            warnings.append("Falling back to text-based extraction; table structure not detected.")
            transactions.extend(self._parse_from_text(file_bytes, warnings))
        deduped = self._deduplicate(transactions)
        return ParseResult(transactions=deduped, warnings=warnings)

    def _parse_with_tables(self, file_bytes: bytes, warnings: List[str]) -> List[Transaction]:
        buffer = io.BytesIO(file_bytes)
        transactions: List[Transaction] = []
        with pdfplumber.open(buffer) as pdf:
            for page_index, page in enumerate(pdf.pages):
                tables = page.extract_tables()
                if not tables:
                    continue
                for table_index, table in enumerate(tables):
                    header_map = self._infer_header_map(table)
                    if not header_map:
                        continue
                    for raw_row in table[1:]:
                        transaction = self._row_to_transaction(
                            raw_row,
                            header_map,
                            warnings,
                            context=f"page {page_index + 1}, table {table_index + 1}",
                        )
                        if transaction:
                            transactions.append(transaction)
        return transactions

    def _infer_header_map(self, table: List[List[Optional[str]]]) -> Optional[dict]:
        if not table:
            return None
        header_row = table[0]
        normalized = [self._normalize_cell(cell) for cell in header_row]
        header_map: dict[str, int] = {}
        for index, cell in enumerate(normalized):
            if not cell:
                continue
            for key, values in HEADER_KEYWORDS.items():
                if cell in values:
                    header_map[key] = index
        required = {"date", "description"}
        if not required.issubset(header_map):
            return None
        if "debit" not in header_map and "credit" not in header_map and "amount" not in header_map:
            return None
        return header_map

    def _row_to_transaction(
        self,
        row: Iterable[Optional[str]],
        header_map: dict,
        warnings: List[str],
        context: str,
    ) -> Optional[Transaction]:
        cells = list(row)
        try:
            date_value = self._parse_date(cells[header_map["date"]])
        except Exception:
            warnings.append(f"Unable to parse date for row in {context}.")
            return None

        description = self._clean_text(cells[header_map["description"]])
        if not description:
            warnings.append(f"Missing description for row in {context}.")

        debit = self._extract_amount(cells, header_map, "debit")
        credit = self._extract_amount(cells, header_map, "credit")

        if debit is None and credit is None:
            amount = self._extract_amount(cells, header_map, "amount")
            if amount is None:
                warnings.append(f"No debit/credit amount found for row in {context}.")
            elif amount >= Decimal("0"):
                credit = amount
            else:
                debit = abs(amount)

        balance = self._extract_amount(cells, header_map, "balance")
        timestamp = int(datetime.now(timezone.utc).timestamp())
        transaction_id = f"{date_value.isoformat()}-{abs(hash(description)) & 0xFFFF:04x}-{timestamp}"
        return Transaction(
            id=transaction_id,
            date=date_value,
            description=description,
            debit=debit,
            credit=credit,
            balance=balance,
        )

    def _parse_from_text(self, file_bytes: bytes, warnings: List[str]) -> List[Transaction]:
        buffer = io.BytesIO(file_bytes)
        transactions: List[Transaction] = []
        with pdfplumber.open(buffer) as pdf:
            for page_index, page in enumerate(pdf.pages):
                text = page.extract_text() or ""
                for line in text.splitlines():
                    maybe = self._line_to_transaction(line, warnings, page_index + 1)
                    if maybe:
                        transactions.append(maybe)
        return transactions

    def _line_to_transaction(
        self, line: str, warnings: List[str], page_number: int
    ) -> Optional[Transaction]:
        date_match = DATE_REGEX.search(line)
        amount_matches = AMOUNT_REGEX.findall(line)
        if not date_match or len(amount_matches) == 0:
            return None
        try:
            date_value = self._parse_date(date_match.group(0))
        except Exception:
            warnings.append(f"Fallback parser could not parse date in line '{line}'.")
            return None
        description = line.replace(date_match.group(0), "").strip()
        amount = self._to_decimal(amount_matches[-1])
        credit = amount if amount and amount > 0 else None
        debit = abs(amount) if amount and amount < 0 else None
        transaction_id = (
            f"{date_value.isoformat()}-fallback-{page_number}-{abs(hash(description)) & 0xFFFF:04x}"
        )
        return Transaction(
            id=transaction_id,
            date=date_value,
            description=description,
            debit=debit,
            credit=credit,
        )

    def _deduplicate(self, transactions: List[Transaction]) -> List[Transaction]:
        seen = set()
        deduped: List[Transaction] = []
        for txn in transactions:
            fingerprint = (txn.date, txn.description, txn.debit, txn.credit, txn.balance)
            if fingerprint in seen:
                continue
            seen.add(fingerprint)
            deduped.append(txn)
        return deduped

    def _parse_date(self, raw: Optional[str]) -> datetime.date:
        value = self._clean_text(raw)
        if not value:
            raise ValueError("Empty date cell")
        for fmt in DATE_PATTERNS:
            try:
                return datetime.strptime(value, fmt).date()
            except ValueError:
                continue
        raise ValueError(f"Unsupported date format: {value}")

    def _extract_amount(
        self, cells: List[Optional[str]], header_map: dict, key: str
    ) -> Optional[Decimal]:
        index = header_map.get(key)
        if index is None:
            return None
        return self._to_decimal(cells[index])

    def _normalize_cell(self, cell: Optional[str]) -> str:
        if not cell:
            return ""
        return re.sub(r"\s+", " ", cell.strip().lower())

    def _clean_text(self, cell: Optional[str]) -> str:
        if not cell:
            return ""
        return re.sub(r"\s+", " ", cell).strip()

    def _to_decimal(self, raw: Optional[str]) -> Optional[Decimal]:
        if raw is None:
            return None
        is_negative = False
        text = str(raw)
        if "(" in text and ")" in text:
            is_negative = True
        cleaned = re.sub(r"[^0-9+\-.,]", "", text)
        cleaned = cleaned.replace(",", "")
        if is_negative and not cleaned.startswith("-"):
            cleaned = f"-{cleaned}"
        if cleaned in {"", "+", "-", "."}:
            return None
        try:
            return Decimal(cleaned)
        except (InvalidOperation, ValueError):
            return None


def parse_pdf(file_bytes: bytes) -> ParseResult:
    return StatementParser().parse(file_bytes)
