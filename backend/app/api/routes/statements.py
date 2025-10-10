from __future__ import annotations

import io
from typing import List

from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, Query, Request, Response, UploadFile, status
from fastapi.responses import StreamingResponse

from app.core.config import get_settings
from app.core.session import schedule_cleanup, session_manager
from app.models.schemas import DownloadFormat, TransactionsResponse, Transaction, UploadResponse
from app.services.exporter import export_to_csv, export_to_excel
from app.services.parser import parse_pdf
from app.services.summary import compute_summary

router = APIRouter()


async def ensure_session(request: Request, response: Response) -> str:
    return session_manager.ensure_session(request, response)


def _store_session_data(session_id: str, transactions: List[Transaction], warnings: List[str]) -> None:
    session_manager.store.set(
        session_id,
        {
            "transactions": [txn.model_dump() for txn in transactions],
            "warnings": warnings,
        },
    )


def _load_session_transactions(session_id: str) -> tuple[List[Transaction], List[str]]:
    raw = session_manager.store.get(session_id)
    if not raw:
        return [], []
    transactions = [Transaction.model_validate(item) for item in raw.get("transactions", [])]
    warnings = raw.get("warnings", [])
    return transactions, warnings


@router.post("/session", status_code=status.HTTP_204_NO_CONTENT)
async def create_session(session_id: str = Depends(ensure_session)) -> Response:
    return Response(status_code=status.HTTP_204_NO_CONTENT)


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
    _store_session_data(session_id, parse_result.transactions, parse_result.warnings)
    background_tasks.add_task(schedule_cleanup)

    return UploadResponse(transactions=parse_result.transactions, summary=summary, warnings=parse_result.warnings)


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
