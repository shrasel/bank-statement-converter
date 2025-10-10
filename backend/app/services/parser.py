from __future__ import annotations

import io
import re
import uuid
import base64
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import List, Optional, Iterable

import pdfplumber
import pytesseract
from pdf2image import convert_from_bytes
from PIL import Image
from pdfminer.high_level import extract_pages
from pdfminer.layout import LTTextContainer, LTChar, LTAnno, LTPage
from dateutil import parser as dateutil_parser

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


@dataclass
@dataclass
class DetectedTransaction:
    """A potential transaction found by the smart detector with confidence score."""
    row_number: int
    page_number: int = 1
    date: Optional[str] = None
    description: Optional[str] = None
    debit: Optional[str] = None
    credit: Optional[str] = None
    balance: Optional[str] = None
    confidence: float = 0.0  # 0.0 to 1.0
    raw_text: str = ""
    is_confirmed: bool = False


@dataclass
class DetectionResult:
    """Result from smart transaction detection."""
    detected_transactions: List[DetectedTransaction]
    total_found: int
    confidence_summary: dict  # e.g., {"high": 10, "medium": 5, "low": 2}


@dataclass
class PdfPage:
    page_number: int
    html: str  # Changed from 'text' to 'html'
    width: float
    height: float


def detect_transactions_from_ocr(file_bytes: bytes) -> DetectionResult:
    """
    Advanced transaction detection using OCR extraction from PDF images.
    Analyzes text positioning and patterns to identify transaction rows.
    
    Steps:
    1. Convert PDF to images
    2. Run OCR with positioning data
    3. Group words into logical lines based on Y-coordinates
    4. Detect transaction patterns in each line
    5. Extract date, description, and amounts
    """
    try:
        # Convert PDF to images for OCR
        print("🔍 Converting PDF to images for transaction detection...")
        images = convert_from_bytes(file_bytes, dpi=300, fmt='png')
        
        detected = []
        row_number = 0
        
        for page_num, image in enumerate(images, start=1):
            print(f"  📄 Analyzing page {page_num} for transactions...")
            
            # Extract text with positioning using OCR
            custom_config = r'--oem 3 --psm 6'
            ocr_data = pytesseract.image_to_data(
                image,
                output_type=pytesseract.Output.DICT,
                config=custom_config
            )
            
            # Group words into lines based on Y-coordinate
            lines = _group_ocr_words_into_lines(ocr_data)
            
            # Analyze each line for transaction patterns
            for line_data in lines:
                row_number += 1
                detected_txn = _detect_transaction_from_line(line_data, row_number, page_num)
                
                if detected_txn and detected_txn.confidence > 0.3:
                    detected.append(detected_txn)
        
        # IMPORTANT: Filter out any transactions without a date
        detected = [txn for txn in detected if txn.date and txn.date.strip()]
        
        # Remove duplicates
        detected = _remove_duplicate_detections(detected)
        
        # Sort by page then row
        detected.sort(key=lambda x: (x.page_number if hasattr(x, 'page_number') else 0, x.row_number))
        
        # Calculate confidence summary
        high = sum(1 for d in detected if d.confidence >= 0.7)
        medium = sum(1 for d in detected if 0.5 <= d.confidence < 0.7)
        low = sum(1 for d in detected if d.confidence < 0.5)
        
        print(f"✅ Detected {len(detected)} transactions with dates (High: {high}, Medium: {medium}, Low: {low})")
        
        return DetectionResult(
            detected_transactions=detected,
            total_found=len(detected),
            confidence_summary={"high": high, "medium": medium, "low": low}
        )
    
    except Exception as e:
        print(f"❌ OCR-based detection failed: {e}")
        import traceback
        traceback.print_exc()
        # Fallback to original method
        return detect_transactions_smart(file_bytes)


def _group_ocr_words_into_lines(ocr_data: dict, tolerance: int = 10) -> List[dict]:
    """
    Group OCR words into lines based on Y-coordinate proximity.
    Returns list of line data with words, positions, and text.
    """
    n_boxes = len(ocr_data['text'])
    words_with_positions = []
    
    # Collect all words with their positions
    for i in range(n_boxes):
        text = ocr_data['text'][i]
        conf = int(ocr_data['conf'][i]) if ocr_data['conf'][i] != '-1' else 0
        
        if text.strip() and conf > 20:
            words_with_positions.append({
                'text': text.strip(),
                'x': ocr_data['left'][i],
                'y': ocr_data['top'][i],
                'width': ocr_data['width'][i],
                'height': ocr_data['height'][i],
                'conf': conf
            })
    
    # Sort by Y position (top to bottom)
    words_with_positions.sort(key=lambda w: (w['y'], w['x']))
    
    # Group into lines
    lines = []
    current_line = []
    current_y = None
    
    for word in words_with_positions:
        if current_y is None:
            current_y = word['y']
            current_line = [word]
        elif abs(word['y'] - current_y) <= tolerance:
            # Same line
            current_line.append(word)
        else:
            # New line
            if current_line:
                # Sort words in line by X position (left to right)
                current_line.sort(key=lambda w: w['x'])
                lines.append({
                    'words': current_line,
                    'text': ' '.join(w['text'] for w in current_line),
                    'y': current_y,
                    'avg_conf': sum(w['conf'] for w in current_line) / len(current_line)
                })
            current_line = [word]
            current_y = word['y']
    
    # Don't forget the last line
    if current_line:
        current_line.sort(key=lambda w: w['x'])
        lines.append({
            'words': current_line,
            'text': ' '.join(w['text'] for w in current_line),
            'y': current_y,
            'avg_conf': sum(w['conf'] for w in current_line) / len(current_line)
        })
    
    return lines


def _detect_transaction_from_line(line_data: dict, row_number: int, page_number: int) -> Optional[DetectedTransaction]:
    """
    Analyze a single line of OCR data to detect if it's a transaction.
    Extracts date, description, and amount fields.
    """
    text = line_data['text']
    words = line_data['words']
    
    # Skip lines that are too short or look like headers
    if len(text) < 5:
        return None
    
    # Skip common header patterns
    header_keywords = ['date', 'description', 'debit', 'credit', 'balance', 'transaction', 'amount', 'details']
    if any(text.lower().strip() == keyword for keyword in header_keywords):
        return None
    if text.lower().count('date') > 0 and text.lower().count('amount') > 0:
        return None
    
    confidence = 0.0
    date_str = None
    description = None
    debit = None
    credit = None
    balance = None
    
    # STEP 1: Find date (usually at the beginning)
    date_patterns = [
        r'\b(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})\b',  # 12/31/2024 or 31-12-2024
        r'\b(\d{4}[/-]\d{1,2}[/-]\d{1,2})\b',    # 2024-12-31
        r'\b(\d{1,2}\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+\d{2,4})\b',  # 31 Dec 2024
        r'\b((?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+\d{1,2},?\s+\d{2,4})\b',  # Dec 31, 2024
    ]
    
    for pattern in date_patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            date_str = match.group(1)
            confidence += 0.35
            break
    
    # STEP 2: Find amounts (currency values)
    # Look for patterns like: $1,234.56 or 1234.56 or 1,234 or (1,234.56)
    amount_pattern = r'(?:\$|€|£|₹)?\s*(\(?\d{1,3}(?:,\d{3})*(?:\.\d{2})?\)?)'
    amounts = re.findall(amount_pattern, text)
    
    # Clean amounts and determine debit/credit
    cleaned_amounts = []
    for amt in amounts:
        # Remove commas and parentheses
        clean_amt = amt.replace(',', '').replace('(', '').replace(')', '')
        try:
            val = float(clean_amt)
            is_negative = '(' in amt  # Parentheses indicate negative
            cleaned_amounts.append({'value': clean_amt, 'is_negative': is_negative})
        except:
            continue
    
    # Assign amounts intelligently
    if len(cleaned_amounts) == 1:
        # Single amount - could be debit or credit
        amount_val = cleaned_amounts[0]['value']
        if cleaned_amounts[0]['is_negative']:
            debit = amount_val
        else:
            # Try to guess based on keywords
            if any(kw in text.lower() for kw in ['payment', 'withdrawal', 'debit', 'purchase', 'fee']):
                debit = amount_val
            else:
                credit = amount_val
        confidence += 0.25
    elif len(cleaned_amounts) >= 2:
        # Multiple amounts - likely debit, credit, balance
        debit = cleaned_amounts[0]['value'] if not cleaned_amounts[0]['is_negative'] else None
        credit = cleaned_amounts[1]['value'] if len(cleaned_amounts) > 1 else None
        balance = cleaned_amounts[2]['value'] if len(cleaned_amounts) > 2 else cleaned_amounts[-1]['value']
        confidence += 0.30
    
    # STEP 3: Extract description (text between date and amounts)
    if date_str:
        # Find position of date in text
        date_pos = text.find(date_str)
        remaining_text = text[date_pos + len(date_str):].strip()
        
        # Remove amounts from description
        desc_text = remaining_text
        for amt in amounts:
            desc_text = desc_text.replace(amt, '')
        
        # Clean up description
        desc_text = re.sub(r'\$|€|£|₹', '', desc_text)
        desc_text = re.sub(r'\s+', ' ', desc_text).strip()
        
        if desc_text and len(desc_text) > 2:
            description = desc_text[:200]  # Limit length
            confidence += 0.25
    else:
        # No date found, use first part of text as description
        desc_parts = []
        for word in words[:min(5, len(words))]:
            if not re.match(r'[\d,.$€£₹()]+', word['text']):
                desc_parts.append(word['text'])
        if desc_parts:
            description = ' '.join(desc_parts)[:200]
            confidence += 0.15
    
    # Boost confidence if we have key components
    if date_str and (debit or credit):
        confidence += 0.10
    if description and len(description) > 5:
        confidence += 0.05
    
    # Only return if we found something meaningful
    if confidence < 0.3 or (not date_str and not debit and not credit):
        return None
    
    return DetectedTransaction(
        date=date_str,
        description=description or "",
        debit=debit,
        credit=credit,
        balance=balance,
        confidence=min(confidence, 0.95),  # Cap at 95%
        raw_text=text,
        row_number=row_number,
        page_number=page_number
    )


def detect_transactions_smart(file_bytes: bytes) -> DetectionResult:
    """
    Advanced transaction detection using multiple strategies:
    1. Table extraction (highest accuracy)
    2. Structured line parsing with column detection
    3. Pattern matching fallback
    
    Returns all detected transactions with confidence scores.
    """
    buffer = io.BytesIO(file_bytes)
    detected = []
    row_number = 0
    
    with pdfplumber.open(buffer) as pdf:
        for page_index, page in enumerate(pdf.pages):
            # STRATEGY 1: Try table extraction first (most accurate)
            tables = page.extract_tables()
            
            if tables:
                for table_idx, table in enumerate(tables):
                    if not table or len(table) < 2:
                        continue
                    
                    # Try to identify header row
                    header_row = None
                    data_start_idx = 0
                    
                    for idx, row in enumerate(table[:3]):  # Check first 3 rows
                        if row and any(cell for cell in row if cell):
                            row_text = ' '.join([str(cell).lower() for cell in row if cell])
                            if any(keyword in row_text for keyword in ['date', 'description', 'amount', 'debit', 'credit', 'balance']):
                                header_row = [str(cell).lower().strip() if cell else '' for cell in row]
                                data_start_idx = idx + 1
                                break
                    
                    # If no header found, assume first row is header
                    if header_row is None and table:
                        header_row = [str(cell).lower().strip() if cell else '' for cell in table[0]]
                        data_start_idx = 1
                    
                    # Map columns
                    col_map = _map_table_columns(header_row) if header_row else {}
                    
                    # Process data rows
                    for row_idx in range(data_start_idx, len(table)):
                        row = table[row_idx]
                        if not row or not any(cell for cell in row if cell):
                            continue
                        
                        row_number += 1
                        detected_txn = _parse_table_row(row, col_map, row_number)
                        
                        if detected_txn and detected_txn.confidence > 0.4:
                            detected.append(detected_txn)
            
            # STRATEGY 2: Text-based line parsing with spatial analysis
            text = page.extract_text(layout=True) or ""
            lines = text.splitlines()
            
            # Analyze line structure to detect columns
            column_positions = _detect_column_positions(lines)
            
            for line in lines:
                line = line.strip()
                if not line or len(line) < 10:
                    continue
                
                # Skip if already found in table
                if any(d.raw_text == line for d in detected):
                    continue
                
                row_number += 1
                detected_txn = _parse_text_line_advanced(line, column_positions, row_number)
                
                if detected_txn and detected_txn.confidence > 0.4:
                    detected.append(detected_txn)
    
    # Remove duplicates based on similarity
    detected = _remove_duplicate_detections(detected)
    
    # Sort by row number to maintain order
    detected.sort(key=lambda x: x.row_number)
    
    # Calculate confidence summary
    high = sum(1 for d in detected if d.confidence >= 0.7)
    medium = sum(1 for d in detected if 0.5 <= d.confidence < 0.7)
    low = sum(1 for d in detected if d.confidence < 0.5)
    
    return DetectionResult(
        detected_transactions=detected,
        total_found=len(detected),
        confidence_summary={"high": high, "medium": medium, "low": low}
    )


def _map_table_columns(header_row: List[str]) -> dict:
    """Map table columns to field types."""
    col_map = {}
    
    for idx, header in enumerate(header_row):
        header = header.lower().strip()
        
        if 'date' in header:
            col_map['date'] = idx
        elif any(kw in header for kw in ['description', 'detail', 'narrative', 'transaction']):
            col_map['description'] = idx
        elif any(kw in header for kw in ['debit', 'withdrawal', 'paid out', 'payment']):
            col_map['debit'] = idx
        elif any(kw in header for kw in ['credit', 'deposit', 'paid in']):
            col_map['credit'] = idx
        elif 'balance' in header:
            col_map['balance'] = idx
        elif 'amount' in header and 'debit' not in col_map:
            col_map['debit'] = idx  # Assume amount is debit if not specified
    
    return col_map


def _parse_table_row(row: List, col_map: dict, row_number: int) -> Optional[DetectedTransaction]:
    """Parse a table row into a detected transaction."""
    if not row:
        return None
    
    # Extract fields based on column map
    date_val = str(row[col_map['date']]).strip() if 'date' in col_map and len(row) > col_map['date'] and row[col_map['date']] else None
    desc_val = str(row[col_map['description']]).strip() if 'description' in col_map and len(row) > col_map['description'] and row[col_map['description']] else None
    debit_val = str(row[col_map['debit']]).strip() if 'debit' in col_map and len(row) > col_map['debit'] and row[col_map['debit']] else None
    credit_val = str(row[col_map['credit']]).strip() if 'credit' in col_map and len(row) > col_map['credit'] and row[col_map['credit']] else None
    balance_val = str(row[col_map['balance']]).strip() if 'balance' in col_map and len(row) > col_map['balance'] and row[col_map['balance']] else None
    
    # Clean up None/empty values
    if date_val and date_val.lower() in ['none', '', 'null']:
        date_val = None
    if desc_val and desc_val.lower() in ['none', '', 'null']:
        desc_val = None
    if debit_val and debit_val.lower() in ['none', '', 'null', '-']:
        debit_val = None
    if credit_val and credit_val.lower() in ['none', '', 'null', '-']:
        credit_val = None
    if balance_val and balance_val.lower() in ['none', '', 'null', '-']:
        balance_val = None
    
    # Calculate confidence
    confidence = 0.0
    
    # Has date
    if date_val and DATE_REGEX.search(date_val):
        confidence += 0.35
    
    # Has description
    if desc_val and len(desc_val) > 2:
        confidence += 0.25
    
    # Has at least one amount
    has_amount = False
    if debit_val and AMOUNT_REGEX.search(debit_val):
        confidence += 0.2
        has_amount = True
    if credit_val and AMOUNT_REGEX.search(credit_val):
        confidence += 0.2
        has_amount = True
    if balance_val and AMOUNT_REGEX.search(balance_val):
        confidence += 0.1
        has_amount = True
    
    # Minimum requirement: must have date OR description AND at least one amount
    if confidence < 0.4:
        return None
    
    raw_text = ' | '.join([str(cell) for cell in row if cell and str(cell).strip() and str(cell).lower() not in ['none', 'null']])
    
    return DetectedTransaction(
        row_number=row_number,
        date=date_val,
        description=desc_val,
        debit=debit_val,
        credit=credit_val,
        balance=balance_val,
        confidence=min(round(confidence, 2), 1.0),
        raw_text=raw_text,
        is_confirmed=False
    )


def _detect_column_positions(lines: List[str]) -> dict:
    """Analyze lines to detect consistent column positions."""
    # This is a simplified version - could be enhanced with more sophisticated analysis
    return {}


def _parse_text_line_advanced(line: str, column_positions: dict, row_number: int) -> Optional[DetectedTransaction]:
    """Parse a text line using advanced pattern matching."""
    confidence = 0.0
    date_match = None
    amounts = []
    
    # Find date
    date_matches = list(DATE_REGEX.finditer(line))
    if date_matches:
        confidence += 0.3
        date_match = date_matches[0].group(0)
    
    # Find amounts
    amount_matches = list(AMOUNT_REGEX.finditer(line))
    if amount_matches:
        num_amounts = min(len(amount_matches), 3)
        confidence += 0.4 * (num_amounts / 3)
        amounts = [m.group(0) for m in amount_matches]
    
    # Extract description
    remaining_text = line
    for match in date_matches + amount_matches:
        remaining_text = remaining_text.replace(match.group(0), '')
    remaining_text = remaining_text.strip()
    
    if len(remaining_text) > 3:
        confidence += 0.2
    
    # Transaction keywords boost
    line_lower = line.lower()
    if any(kw in line_lower for kw in ['payment', 'transfer', 'deposit', 'withdrawal', 'purchase', 'atm', 'pos']):
        confidence += 0.1
    
    if confidence < 0.4:
        return None
    
    # Parse amounts
    debit_val, credit_val, balance_val = None, None, None
    
    if len(amounts) == 1:
        if '-' in amounts[0] or 'withdrawal' in line_lower or 'debit' in line_lower or 'payment' in line_lower:
            debit_val = amounts[0].replace('-', '').strip()
        elif 'deposit' in line_lower or 'credit' in line_lower:
            credit_val = amounts[0]
        else:
            debit_val = amounts[0]
    elif len(amounts) == 2:
        # Usually: amount + balance
        if '-' in amounts[0]:
            debit_val = amounts[0].replace('-', '').strip()
        else:
            debit_val = amounts[0]
        balance_val = amounts[1]
    elif len(amounts) >= 3:
        # Usually: debit, credit, balance
        debit_val = amounts[0] if amounts[0] != '-' else None
        credit_val = amounts[1] if amounts[1] != '-' else None
        balance_val = amounts[2]
    
    return DetectedTransaction(
        row_number=row_number,
        date=date_match,
        description=remaining_text[:200] if remaining_text else line[:200],
        debit=debit_val,
        credit=credit_val,
        balance=balance_val,
        confidence=min(round(confidence, 2), 1.0),
        raw_text=line,
        is_confirmed=False
    )


def _remove_duplicate_detections(detected: List[DetectedTransaction]) -> List[DetectedTransaction]:
    """Remove duplicate or very similar detections."""
    if not detected:
        return []
    
    unique = []
    seen_texts = set()
    
    for txn in detected:
        # Create a normalized key for comparison
        key = f"{txn.date or ''}_{txn.description or ''}_{txn.debit or ''}_{txn.credit or ''}"
        key_normalized = key.lower().replace(' ', '')
        
        if key_normalized not in seen_texts:
            seen_texts.add(key_normalized)
            unique.append(txn)
    
    return unique



    low = sum(1 for d in detected if d.confidence < 0.5)
    
    return DetectionResult(
        detected_transactions=detected,
        total_found=len(detected),
        confidence_summary={"high": high, "medium": medium, "low": low}
    )


def extract_pdf_html_pages(file_bytes: bytes) -> List[PdfPage]:
    """
    Convert PDF pages to HTML with preserved layout, fonts, and positioning.
    Uses pdfminer.six to extract text elements with coordinates and styling.
    """
    buffer = io.BytesIO(file_bytes)
    pages: List[PdfPage] = []
    
    for page_num, page_layout in enumerate(extract_pages(buffer), start=1):
        html_content = _convert_page_to_html(page_layout)
        pages.append(PdfPage(
            page_number=page_num,
            html=html_content,
            width=float(page_layout.width),
            height=float(page_layout.height)
        ))
    
    return pages


def _convert_page_to_html(page_layout: LTPage) -> str:
    """
    Convert a pdfminer LTPage object to HTML with absolute positioning.
    Uses character-level extraction for maximum accuracy.
    """
    from pdfminer.layout import LTTextBox, LTTextLine, LTFigure, LTText, LTChar
    
    page_height = page_layout.height
    page_width = page_layout.width
    text_elements = []
    
    def collect_text_chars(element, depth=0):
        """Collect text at character level for maximum accuracy."""
        # Process individual characters to preserve exact positioning
        if isinstance(element, LTChar):
            char_text = element.get_text()
            if char_text and not char_text.isspace():  # Skip whitespace-only chars
                x0, y0, x1, y1 = element.bbox
                font_size = max(8, int(element.height))
                font_name = getattr(element, 'fontname', '').lower()
                
                # Determine font family
                if 'courier' in font_name or 'mono' in font_name:
                    font_family = "Courier New, monospace"
                elif 'times' in font_name or 'serif' in font_name:
                    font_family = "Georgia, serif"
                elif 'arial' in font_name or 'helvetica' in font_name:
                    font_family = "Arial, sans-serif"
                else:
                    font_family = "system-ui, sans-serif"
                
                # Font weight
                font_weight = "bold" if 'bold' in font_name else "normal"
                
                text_elements.append({
                    'text': char_text,
                    'bbox': (x0, y0, x1, y1),
                    'font_size': font_size,
                    'font_family': font_family,
                    'font_weight': font_weight,
                })
        
        # Recursively process children
        if hasattr(element, '__iter__') and not isinstance(element, (LTFigure, str)):
            for child in element:
                collect_text_chars(child, depth + 1)
    
    # Collect all characters
    collect_text_chars(page_layout)
    
    # Group characters into words/lines based on proximity
    if not text_elements:
        return f'<div style="position: relative; width: {page_width:.2f}px; height: {page_height:.2f}px; background: #ffffff; border: 1px solid #e2e8f0;"></div>'
    
    # Sort by position (top to bottom, left to right)
    text_elements.sort(key=lambda e: (-e['bbox'][3], e['bbox'][0]))
    
    # Group nearby characters into text spans
    grouped_elements = []
    current_group = None
    char_spacing_threshold = 2.0  # pixels
    line_height_threshold = 3.0  # pixels
    
    for elem in text_elements:
        if current_group is None:
            current_group = {
                'text': elem['text'],
                'bbox': elem['bbox'],
                'font_size': elem['font_size'],
                'font_family': elem['font_family'],
                'font_weight': elem['font_weight'],
            }
        else:
            # Check if this character belongs to the same group
            prev_x1 = current_group['bbox'][2]
            prev_y0 = current_group['bbox'][1]
            prev_y1 = current_group['bbox'][3]
            
            curr_x0 = elem['bbox'][0]
            curr_y0 = elem['bbox'][1]
            curr_y1 = elem['bbox'][3]
            
            # Same line and close horizontally?
            same_line = abs(prev_y0 - curr_y0) < line_height_threshold and abs(prev_y1 - curr_y1) < line_height_threshold
            close_horizontal = (curr_x0 - prev_x1) < (current_group['font_size'] * 0.5)  # Less than half font size apart
            
            if same_line and close_horizontal and elem['font_size'] == current_group['font_size']:
                # Extend current group
                current_group['text'] += elem['text']
                current_group['bbox'] = (
                    current_group['bbox'][0],  # Keep original x0
                    min(current_group['bbox'][1], elem['bbox'][1]),  # Min y0
                    elem['bbox'][2],  # Update to new x1
                    max(current_group['bbox'][3], elem['bbox'][3]),  # Max y1
                )
            else:
                # Save current group and start new one
                grouped_elements.append(current_group)
                current_group = {
                    'text': elem['text'],
                    'bbox': elem['bbox'],
                    'font_size': elem['font_size'],
                    'font_family': elem['font_family'],
                    'font_weight': elem['font_weight'],
                }
    
    # Don't forget the last group
    if current_group:
        grouped_elements.append(current_group)
    
    # Generate HTML
    html_parts = []
    color = "#1e293b"  # Dark slate
    
    for elem in grouped_elements:
        x0, y0, x1, y1 = elem['bbox']
        top = page_height - y1  # Convert to top-left origin
        left = x0
        height = y1 - y0
        
        # Escape HTML
        text_content = (elem['text']
                      .replace('&', '&amp;')
                      .replace('<', '&lt;')
                      .replace('>', '&gt;')
                      .replace('"', '&quot;'))
        
        style = (
            f"position: absolute; "
            f"left: {left:.2f}px; "
            f"top: {top:.2f}px; "
            f"font-size: {elem['font_size']}px; "
            f"font-family: {elem['font_family']}; "
            f"font-weight: {elem['font_weight']}; "
            f"color: {color}; "
            f"white-space: nowrap; "
            f"line-height: {height:.2f}px;"
        )
        
        html_parts.append(f'<span style="{style}">{text_content}</span>')
    
    # Wrap in a positioned container with better styling

    container_style = (
        f"position: relative; "
        f"width: {page_layout.width:.2f}px; "
        f"height: {page_layout.height:.2f}px; "
        f"background: #ffffff; "
        f"border: 1px solid #e2e8f0; "
        f"box-shadow: 0 1px 3px rgba(0,0,0,0.1); "
        f"overflow: hidden; "
        f"margin: 0 auto;"
    )
    
    html = f'<div style="{container_style}">{"".join(html_parts)}</div>'
    return html


def extract_text_from_pdf_image(file_bytes: bytes) -> str:
    """
    Extract clean text from PDF by converting to image and using OCR.
    Returns all text content in reading order for transaction detection.
    """
    try:
        # Convert PDF to images
        images = convert_from_bytes(file_bytes, dpi=300, fmt='png')
        all_text = []
        
        for page_num, image in enumerate(images, start=1):
            # Extract text using OCR
            custom_config = r'--oem 3 --psm 6'
            text = pytesseract.image_to_string(image, config=custom_config)
            all_text.append(f"--- Page {page_num} ---\n{text}")
        
        return "\n\n".join(all_text)
    
    except Exception as e:
        print(f"Failed to extract text from images: {e}")
        # Fallback to pdfplumber
        import pdfplumber
        with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
            return "\n\n".join(page.extract_text() or "" for page in pdf.pages)


def extract_pdf_html_pages_from_image(file_bytes: bytes) -> List[PdfPage]:
    """
    Convert PDF pages to HTML by extracting text via OCR with accurate positioning.
    Shows text exactly as it appears in the PDF without the image.
    Each page is converted to image temporarily for OCR, then discarded.
    """
    try:
        # Convert PDF pages to images (300 DPI for excellent OCR accuracy)
        print("Converting PDF to images at 300 DPI for OCR extraction...")
        images = convert_from_bytes(file_bytes, dpi=300, fmt='png')
        pages: List[PdfPage] = []
        
        for page_num, image in enumerate(images, start=1):
            # Get image dimensions
            width, height = image.size
            
            print(f"Page {page_num}: {width}x{height}px - Running OCR...")
            
            # Extract text using OCR with detailed configuration
            try:
                # Use Tesseract with config for better accuracy
                custom_config = r'--oem 3 --psm 6'  # OEM 3 = Default OCR Engine, PSM 6 = Assume uniform block of text
                ocr_data = pytesseract.image_to_data(
                    image, 
                    output_type=pytesseract.Output.DICT,
                    config=custom_config
                )
                
                # Count extracted words
                word_count = sum(1 for text in ocr_data['text'] if text.strip())
                print(f"  ✓ OCR extracted {word_count} words from page {page_num}")
                
                # Create HTML with ONLY text positioned exactly as in PDF (no image)
                html_content = _create_text_only_html_from_ocr(width, height, ocr_data)
                
            except Exception as ocr_error:
                print(f"  ✗ OCR failed for page {page_num}: {ocr_error}")
                # Fallback to empty page with error message
                html_content = f'<div style="padding: 20px; color: red;">OCR failed for page {page_num}: {ocr_error}</div>'
            
            pages.append(PdfPage(
                page_number=page_num,
                html=html_content,
                width=float(width),
                height=float(height)
            ))
        
        print(f"✓ Successfully extracted text from {len(pages)} pages via OCR")
        return pages
    
    except Exception as e:
        print(f"✗ Image-based PDF conversion failed: {e}")
        import traceback
        traceback.print_exc()
        # Fallback to original method
        return extract_pdf_html_pages(file_bytes)


def _create_text_only_html_from_ocr(width: int, height: int, ocr_data: dict) -> str:
    """
    Create HTML with ONLY OCR-extracted text positioned exactly as it appears in the PDF.
    No background image - pure text with accurate positioning.
    OCR data contains: text, conf, left, top, width, height for each word.
    """
    # Build text elements from OCR data
    text_elements = []
    n_boxes = len(ocr_data['text'])
    
    for i in range(n_boxes):
        text = ocr_data['text'][i]
        conf = int(ocr_data['conf'][i]) if ocr_data['conf'][i] != '-1' else 0
        
        # Include text with confidence > 20% (lower threshold for better coverage)
        if text.strip() and conf > 20:
            left = ocr_data['left'][i]
            top = ocr_data['top'][i]
            word_width = ocr_data['width'][i]
            word_height = ocr_data['height'][i]
            
            # Escape HTML special characters
            text_content = (text
                          .replace('&', '&amp;')
                          .replace('<', '&lt;')
                          .replace('>', '&gt;')
                          .replace('"', '&quot;'))
            
            # Calculate font size to match OCR detection
            font_size = max(10, int(word_height * 0.85))
            
            # Determine text color based on confidence
            if conf >= 80:
                color = "#1e293b"  # Dark slate - high confidence
            elif conf >= 60:
                color = "#475569"  # Medium slate - medium confidence
            else:
                color = "#64748b"  # Light slate - lower confidence
            
            # Position text exactly where OCR detected it
            style = (
                f"position: absolute; "
                f"left: {left}px; "
                f"top: {top}px; "
                f"font-size: {font_size}px; "
                f"font-family: 'Courier New', Courier, monospace; "
                f"color: {color}; "
                f"white-space: nowrap; "
                f"user-select: text; "
                f"cursor: text; "
                f"line-height: {word_height}px; "
                f"letter-spacing: 0.5px;"
            )
            
            # Add title attribute to show confidence
            text_elements.append(f'<span style="{style}" title="Confidence: {conf}%">{text_content}</span>')
    
    # Container style - white background to mimic paper with proper scaling
    container_style = (
        f"position: relative; "
        f"width: {width}px; "
        f"height: {height}px; "
        f"background: #ffffff; "
        f"border: 1px solid #e2e8f0; "
        f"box-shadow: 0 1px 3px rgba(0,0,0,0.1); "
        f"overflow: hidden; "
        f"margin: 0 auto; "
        f"transform-origin: top left; "
        f"max-width: 100%;"
    )
    
    # Wrapper with scaling to fit container
    wrapper_style = (
        f"position: relative; "
        f"width: fit-content; "
        f"max-width: 100%; "
        f"margin: 0 auto;"
    )
    
    # Wrapper with scaling to fit container
    wrapper_style = (
        f"position: relative; "
        f"width: fit-content; "
        f"max-width: 100%; "
        f"margin: 0 auto;"
    )
    
    # Create HTML with wrapper and scaled content
    html = (
        f'<div style="{wrapper_style}">'
        f'<div style="{container_style}">'
        f'{"".join(text_elements)}'
        f'</div>'
        f'</div>'
    )
    return html


def _create_image_only_html(img_base64: str, width: int, height: int) -> str:
    """Create HTML with just the PDF page image (no text overlay)."""
    container_style = (
        f"position: relative; "
        f"width: {width}px; "
        f"height: {height}px; "
        f"background: #ffffff; "
        f"border: 1px solid #e2e8f0; "
        f"box-shadow: 0 1px 3px rgba(0,0,0,0.1); "
        f"overflow: hidden; "
        f"margin: 0 auto;"
    )
    
    img_style = (
        f"position: absolute; "
        f"top: 0; "
        f"left: 0; "
        f"width: 100%; "
        f"height: 100%; "
        f"object-fit: contain;"
    )
    
    html = (
        f'<div style="{container_style}">'
        f'<img src="data:image/png;base64,{img_base64}" style="{img_style}" alt="PDF Page" />'
        f'</div>'
    )
    return html


def _create_image_html_with_text(img_base64: str, width: int, height: int, ocr_data: dict) -> str:
    """
    Create HTML with image background and accurately positioned selectable text overlay.
    OCR data contains: text, conf, left, top, width, height for each word.
    Text is positioned to match the image exactly.
    """
    # Build text elements from OCR data
    text_elements = []
    n_boxes = len(ocr_data['text'])
    
    for i in range(n_boxes):
        text = ocr_data['text'][i]
        conf = int(ocr_data['conf'][i]) if ocr_data['conf'][i] != '-1' else 0
        
        # Include text with confidence > 20% (lower threshold for better coverage)
        if text.strip() and conf > 20:
            left = ocr_data['left'][i]
            top = ocr_data['top'][i]
            word_width = ocr_data['width'][i]
            word_height = ocr_data['height'][i]
            
            # Escape HTML special characters
            text_content = (text
                          .replace('&', '&amp;')
                          .replace('<', '&lt;')
                          .replace('>', '&gt;')
                          .replace('"', '&quot;'))
            
            # Calculate font size to match OCR detection (slightly smaller for better fit)
            font_size = max(8, int(word_height * 0.75))
            
            # Position text to overlay exactly on the image
            style = (
                f"position: absolute; "
                f"left: {left}px; "
                f"top: {top}px; "
                f"width: {word_width}px; "
                f"height: {word_height}px; "
                f"font-size: {font_size}px; "
                f"font-family: Arial, Helvetica, sans-serif; "
                f"color: rgba(30, 41, 59, 0.01); "  # Nearly invisible but selectable
                f"white-space: nowrap; "
                f"overflow: hidden; "
                f"user-select: text; "
                f"cursor: text; "
                f"line-height: {word_height}px;"
            )
            
            text_elements.append(f'<span style="{style}" title="{text_content}">{text_content}</span>')
    
    # Container style
    container_style = (
        f"position: relative; "
        f"width: {width}px; "
        f"height: {height}px; "
        f"background: #ffffff; "
        f"border: 1px solid #e2e8f0; "
        f"box-shadow: 0 1px 3px rgba(0,0,0,0.1); "
        f"overflow: hidden; "
        f"margin: 0 auto;"
    )
    
    # Image style (background layer)
    img_style = (
        f"position: absolute; "
        f"top: 0; "
        f"left: 0; "
        f"width: 100%; "
        f"height: 100%; "
        f"object-fit: contain; "
        f"z-index: 0;"
    )
    
    # Text layer
    text_layer_style = (
        f"position: absolute; "
        f"top: 0; "
        f"left: 0; "
        f"width: 100%; "
        f"height: 100%; "
        f"z-index: 1;"
    )
    
    html = (
        f'<div style="{container_style}">'
        f'<img src="data:image/png;base64,{img_base64}" style="{img_style}" alt="PDF Page" />'
        f'<div style="{text_layer_style}">{"".join(text_elements)}</div>'
        f'</div>'
    )
    return html


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
