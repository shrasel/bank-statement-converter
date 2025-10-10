from __future__ import annotations

import io
from typing import List, Optional

from fastapi import APIRouter, BackgroundTasks, Body, Depends, File, HTTPException, Query, Request, Response, UploadFile, status
from fastapi.responses import StreamingResponse, Response as FastAPIResponse

from app.core.config import get_settings
from app.core.session import schedule_cleanup, session_manager
from app.models.schemas import (
    ConfirmTransactionsRequest, DownloadFormat, TransactionsResponse, Transaction, 
    UploadResponse, PdfTextResponse, PdfPageContent, DetectedTransactionsResponse,
    DetectedTransactionItem, ConfirmDetectedTransactionsRequest
)
from app.services.exporter import export_to_csv, export_to_excel
from app.services.parser import parse_pdf, extract_pdf_html_pages, extract_pdf_html_pages_from_image, detect_transactions_smart, detect_transactions_from_ocr
from app.services.summary import compute_summary

router = APIRouter()


async def ensure_session(request: Request, response: Response) -> str:
    # Try header first, then fall back to cookie
    session_id = request.headers.get("X-Session-ID")
    if session_id and session_manager.store.exists(session_id):
        response.headers["X-Session-ID"] = session_id
        return session_id
    
    # Fall back to cookie-based session
    session_id = session_manager.ensure_session(request, response)
    # Also send in header for client to store
    response.headers["X-Session-ID"] = session_id
    return session_id


def _store_session_data(session_id: str, transactions: List[Transaction], warnings: List[str], pdf_bytes: bytes | None = None) -> None:
    data = {
        "transactions": [txn.model_dump() for txn in transactions],
        "warnings": warnings,
    }
    if pdf_bytes:
        data["pdf_bytes"] = pdf_bytes
    session_manager.store.set(session_id, data)


def _load_session_transactions(session_id: str) -> tuple[List[Transaction], List[str]]:
    raw = session_manager.store.get(session_id)
    if not raw:
        return [], []
    transactions = [Transaction.model_validate(item) for item in raw.get("transactions", [])]
    warnings = raw.get("warnings", [])
    return transactions, warnings


def _load_session_pdf(session_id: str) -> bytes | None:
    raw = session_manager.store.get(session_id)
    if not raw:
        return None
    return raw.get("pdf_bytes")


@router.post("/session", status_code=status.HTTP_204_NO_CONTENT)
async def create_session(request: Request) -> FastAPIResponse:
    response = FastAPIResponse(status_code=status.HTTP_204_NO_CONTENT)
    session_id = session_manager.ensure_session(request, response)
    response.headers["X-Session-ID"] = session_id
    return response


@router.delete("/session", status_code=status.HTTP_204_NO_CONTENT)
async def delete_session(request: Request, response: Response) -> Response:
    session_id = request.cookies.get(get_settings().session_cookie_name)
    session_manager.clear_session(response, session_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/upload", response_model=UploadResponse)
async def upload_statement(
    request: Request,
    response: Response,
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    session_id: str = Depends(ensure_session),
) -> UploadResponse:
    settings = get_settings()
    if file.content_type not in {"application/pdf", "application/x-pdf"}:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Only PDF files are supported.")

    contents = await file.read()
    if len(contents) == 0:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Uploaded file is empty.")
    if len(contents) > settings.max_upload_size_bytes:
        raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail="File exceeds size limit (10 MB).")

    parse_result = parse_pdf(contents)
    if not parse_result.transactions:
        detail = "Could not extract transactions from the provided PDF."
        if parse_result.warnings:
            detail = f"{detail} Warnings: {'; '.join(parse_result.warnings)}"
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=detail)

    summary = compute_summary(parse_result.transactions)
    
    # Store the parsed data AND the PDF bytes for inspection mode
    _store_session_data(session_id, parse_result.transactions, parse_result.warnings, pdf_bytes=contents)
    
    background_tasks.add_task(schedule_cleanup)

    return UploadResponse(transactions=parse_result.transactions, summary=summary, warnings=parse_result.warnings)


@router.post("/confirm-transactions", response_model=TransactionsResponse)
async def confirm_transactions(
    request: Request,
    response: Response,
    payload: ConfirmTransactionsRequest = Body(...),
    session_id: str = Depends(ensure_session),
) -> TransactionsResponse:
    """
    Store selected transactions (by ID) in the session.
    Used after inspection mode where user selects which transactions to keep.
    """
    # Load the current (temporary) parsed data from session
    all_transactions, warnings = _load_session_transactions(session_id)
    
    if not all_transactions:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No parsed transactions found. Please upload again in inspection mode."
        )
    
    # Filter to only selected transaction IDs
    selected_ids = set(payload.transaction_ids)
    filtered_transactions = [txn for txn in all_transactions if txn.id in selected_ids]
    
    if not filtered_transactions:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="None of the provided transaction IDs were found."
        )
    
    # Store filtered transactions in session
    _store_session_data(session_id, filtered_transactions, warnings)
    summary = compute_summary(filtered_transactions)
    
    return TransactionsResponse(transactions=filtered_transactions, summary=summary, warnings=warnings)


@router.get("/transactions", response_model=TransactionsResponse)
async def get_transactions(session_id: str = Depends(ensure_session)) -> TransactionsResponse:
    transactions, warnings = _load_session_transactions(session_id)
    if not transactions:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No transactions found for session.")
    summary = compute_summary(transactions)
    return TransactionsResponse(transactions=transactions, summary=summary, warnings=warnings)


@router.get("/download")
async def download_transactions(
    format: DownloadFormat = Query(default=DownloadFormat.xlsx),
    session_id: str = Depends(ensure_session),
) -> StreamingResponse:
    transactions, _ = _load_session_transactions(session_id)
    if not transactions:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No transactions available for download.")

    if format == DownloadFormat.xlsx:
        data = export_to_excel(transactions)
        media_type = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        filename = "transactions.xlsx"
    else:
        data = export_to_csv(transactions)
        media_type = "text/csv"
        filename = "transactions.csv"

    return StreamingResponse(
        content=io.BytesIO(data),
        media_type=media_type,
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


@router.get("/pdf")
async def get_pdf(session_id: str = Depends(ensure_session)) -> StreamingResponse:
    """
    Retrieve the stored PDF file for inspection/preview.
    """
    pdf_bytes = _load_session_pdf(session_id)
    if not pdf_bytes:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No PDF file found in session.")
    
    return StreamingResponse(
        content=io.BytesIO(pdf_bytes),
        media_type="application/pdf",
        headers={"Content-Disposition": "inline; filename=statement.pdf"},
    )


@router.get("/pdf-text", response_model=PdfTextResponse)
async def get_pdf_text(session_id: str = Depends(ensure_session)) -> PdfTextResponse:
    """
    Extract and return HTML content from the stored PDF file, page by page.
    Uses image-based rendering with OCR for maximum accuracy and visual fidelity.
    """
    pdf_bytes = _load_session_pdf(session_id)
    if not pdf_bytes:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No PDF file found in session.")
    
    # Extract HTML from each page using image-based rendering
    # This provides pixel-perfect accuracy by rendering the actual PDF
    pages = extract_pdf_html_pages_from_image(pdf_bytes)
    
    # Convert to response model
    page_contents = [
        PdfPageContent(
            page_number=page.page_number,
            text=page.html,  # Now contains HTML with embedded image + text overlay
            width=page.width,
            height=page.height
        )
        for page in pages
    ]
    
    return PdfTextResponse(pages=page_contents, total_pages=len(page_contents))


@router.get("/detect-transactions", response_model=DetectedTransactionsResponse)
async def detect_transactions(session_id: str = Depends(ensure_session)) -> DetectedTransactionsResponse:
    """
    Use smart OCR-based detection algorithm to find potential transactions in the PDF.
    Returns all candidates with confidence scores for user review and editing.
    """
    pdf_bytes = _load_session_pdf(session_id)
    if not pdf_bytes:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No PDF file found in session.")
    
    # Run OCR-based smart detection (converts PDF to image, runs OCR, analyzes patterns)
    detection_result = detect_transactions_from_ocr(pdf_bytes)
    
    # Convert to response model
    items = [
        DetectedTransactionItem(
            row_number=dt.row_number,
            date=dt.date,
            description=dt.description,
            debit=dt.debit,
            credit=dt.credit,
            balance=dt.balance,
            confidence=dt.confidence,
            raw_text=dt.raw_text,
            is_confirmed=dt.is_confirmed
        )
        for dt in detection_result.detected_transactions
    ]
    
    return DetectedTransactionsResponse(
        detected_transactions=items,
        total_found=detection_result.total_found,
        confidence_summary=detection_result.confidence_summary
    )


@router.post("/confirm-detected-transactions", response_model=TransactionsResponse)
async def confirm_detected_transactions(
    request: Request,
    response: Response,
    payload: ConfirmDetectedTransactionsRequest = Body(...),
    session_id: str = Depends(ensure_session),
) -> TransactionsResponse:
    """
    Convert user-confirmed and edited detected transactions into final Transaction objects.
    Stores them in session for download.
    """
    from datetime import date
    import uuid
    from dateutil import parser as dateutil_parser
    
    if not payload.confirmed_transactions:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No transactions confirmed."
        )
    
    transactions: List[Transaction] = []
    warnings: List[str] = []
    
    for idx, item in enumerate(payload.confirmed_transactions):
        # Parse date
        parsed_date = None
        if item.date:
            try:
                parsed_date = dateutil_parser.parse(item.date).date()
            except Exception:
                warnings.append(f"Row {item.row_number}: Could not parse date '{item.date}'")
        
        # Parse amounts
        def parse_amount(value: Optional[str]) -> Optional[float]:
            if not value:
                return None
            try:
                cleaned = value.replace('$', '').replace('£', '').replace('€', '').replace(',', '').strip()
                return float(cleaned)
            except:
                return None
        
        debit = parse_amount(item.debit)
        credit = parse_amount(item.credit)
        balance = parse_amount(item.balance)
        
        transactions.append(Transaction(
            id=f"detected-{idx+1}-{uuid.uuid4().hex[:8]}",
            date=parsed_date or date.today(),
            description=item.description or item.raw_text or "Unknown",
            debit=debit,
            credit=credit,
            balance=balance,
            warnings=[]
        ))
    
    # Store in session
    _store_session_data(session_id, transactions, warnings)
    
    # Return summary
    summary = compute_summary(transactions)
    return TransactionsResponse(transactions=transactions, summary=summary, warnings=warnings)
