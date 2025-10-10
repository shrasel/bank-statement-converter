# Bank Statement Converter – Product Requirements Document

## 1. Overview
- **Product name:** Bank Statement Converter
- **Prepared by:** GitHub Copilot (acting as senior full-stack developer)
- **Date:** 2025-10-09
- **Purpose:** Deliver a web application that converts bank statement PDFs into structured transaction data, allowing review in-browser and export to Excel or CSV.

## 2. Problem Statement
Financial professionals and individuals regularly receive bank statements in PDF format, making reconciliation and analysis tedious. Manual transcription is error-prone and slow. A self-service converter should quickly ingest a PDF, present clean transaction data in-app, and generate spreadsheet exports.

## 3. Goals & Success Metrics
- Parse uploaded bank statement PDFs into normalized transaction rows with high accuracy (>95% correct field extraction for supported statement formats).
- Provide a step-by-step guided interface that clearly explains the process before upload and after parsing.
- Enable data export to `.xlsx` (primary) and `.csv` (secondary) with a single click.
- Achieve end-to-end processing under 10 seconds for statements with ≤ 1,000 transactions on commodity hardware.

## 4. Scope
### In Scope
- Single bank statement PDF upload per session.
- Extraction of transaction date, description, debit/credit amounts, and running balance (when available).
- On-screen preview of parsed transactions with basic sorting and pagination.
- Export to Excel (with typed columns and formatting) and CSV.
- Session-based state retention for the duration of the browser session.
- Support for English-language bank statements using tabular layouts.

### Out of Scope (Phase 1)
- Multi-file uploads or batch processing.
- OCR for scanned image PDFs (assume selectable text PDFs).
- Advanced data cleaning (e.g., categorization, deduplication).
- User authentication, persistence beyond session, or multi-tenant accounts.
- Mobile-native apps (responsive web only).

## 5. Target Users & Personas
- **Accountants/bookkeepers:** Need quick exports to import into accounting software.
- **Small business owners:** Require simplified transaction tracking in spreadsheets.
- **Financial analysts:** Need clean data for deeper analysis.

## 6. User Journey Summary
1. User lands on the homepage and reads a concise process overview (Upload → Review → Download).
2. User selects or drags a bank statement PDF into the uploader.
3. Backend parses the PDF; UI shows parsing progress, then renders a transaction table.
4. User reviews data, filtering/sorting as needed.
5. User clicks “Download as Excel” (primary CTA) or “Download as CSV” and receives the file.
6. Option to start over (clear session) and upload another statement.

## 7. Functional Requirements
1. **Landing Guidance**
   - Display a three-step explainer outlining upload, review, and download actions.
   - Provide tooltips or an FAQ accordion covering privacy, supported formats, and troubleshooting.
2. **File Upload**
   - Allow drag-and-drop or button-based selection of `.pdf` files up to 10 MB.
   - Validate file type and size client-side; show descriptive errors.
   - Send file to backend via authenticated session (cookie-based) with progress indication.
3. **Parsing & Validation**
   - Backend reads PDF using structured parsing; fallback to heuristics when table extraction fails.
   - Detect headers and data rows; ignore summary/footer sections.
   - Standardize fields: `transaction_id`, `date` (ISO 8601), `description`, `debit`, `credit`, `balance`.
   - Return parsing warnings (e.g., rows skipped) to surface in UI.
4. **Transaction Review UI**
   - Present tabular results with sortable columns and pagination (50 rows per page default).
   - Highlight rows with parsing warnings.
   - Provide summary statistics (total credits/debits, row counts).
5. **Download Export**
   - Generate `.xlsx` file with typed columns and currency/date formatting.
   - Provide optional `.csv` download.
   - Trigger download via REST endpoint; maintain session association.
6. **Session Handling**
   - Assign unique session ID on first interaction; store parsed data server-side keyed to session.
   - Session expires after 30 minutes of inactivity.
   - Provide endpoint to clear/reset session data.
7. **Error Handling**
   - User-friendly error messages for parsing failures, unsupported statements, or server errors.
   - Log errors server-side with correlation to session ID.

## 8. Non-Functional Requirements
- **Performance:** Process statements ≤1,000 rows in <10 seconds on baseline hardware.
- **Scalability:** Support concurrent sessions with minimal state (in-memory cache with size limit, e.g., Redis optional future enhancement).
- **Security:**
  - Enforce file size limits and validate PDFs to prevent malicious payloads.
  - Use HTTPS (assumed via deployment) and HTTP-only cookies for sessions.
  - No data persisted beyond session lifetime.
- **Accessibility:** WCAG 2.1 AA compliance target (keyboard navigation, ARIA labels, color contrast).
- **Maintainability:** Modular service and component structure with tests.
- **Observability:** Basic request/response logging + structured error logs.

## 9. System Architecture
- **Frontend:** Angular 17 SPA, Tailwind CSS for styling, communicates with backend via REST.
- **Backend:** FastAPI app exposing endpoints for upload, transaction retrieval, download, and session management.
- **Session Store:** Server-side in-memory dictionary keyed by signed session ID (with optional future swap to Redis).
- **PDF Parsing:** `pdfplumber` (primary) with fallback heuristics using regex/string parsing.
- **Data Processing:** `pandas` DataFrame to normalize and export to Excel/CSV.

```
[Browser w/ Angular SPA]
        |
 HTTPS REST (JSON/Multipart)
        |
[FastAPI Backend] -- pdfplumber/pandas --> In-memory session store
```

## 10. API Design
| Endpoint | Method | Payload | Response | Notes |
| --- | --- | --- | --- | --- |
| `/api/session` | POST | — | `{ sessionId }` via HTTP-only cookie | Auto-invoked on first request. |
| `/api/upload` | POST | Multipart form with `file` | `{ transactions: Transaction[]; warnings?: string[] }` | Parses PDF, stores in session. |
| `/api/transactions` | GET | — | `{ transactions: Transaction[]; summary: Totals }` | Returns current session data. |
| `/api/download` | GET | Query `format=xlsx|csv` | Binary file stream | Requires existing parsed data. |
| `/api/session` | DELETE | — | `204 No Content` | Clears session data. |

### Data Models
```json
Transaction {
  "id": "string",           // generated UUID per row
  "date": "YYYY-MM-DD",
  "description": "string",
  "debit": 123.45,           // null when credit present
  "credit": 123.45,          // null when debit present
  "balance": 456.78 | null,
  "warnings": ["string"]     // optional markers
}

Totals {
  "rowCount": 0,
  "totalDebit": 0.0,
  "totalCredit": 0.0
}
```

## 11. UX & UI Requirements
- **Layout:** Single-page app with hero section, upload panel, and post-upload results view.
- **Guidance:** Display numbered steps with icons, plus inline copy describing what happens during upload.
- **Upload Component:** Drag/drop zone with file preview and validation messaging. Disable submit button while parsing.
- **Results Table:** Responsive table within card layout, sticky header, alternating row colors. Provide loading skeleton while fetching data.
- **Call-to-Action Buttons:** Primary button for Excel download, secondary for CSV, tertiary for resetting session.
- **Notifications:** Toast or inline alerts for success, warnings, and errors.
- **Responsive Design:** Works on desktop and tablet viewports; mobile support is best-effort but not primary.

## 12. Session & State Management
- Use FastAPI `SessionMiddleware` with signed cookies; store session data in server-side dictionary.
- Frontend uses Angular service to persist state from API responses; rely on cookie for backend correlation.
- Implement TTL eviction task to purge idle sessions.

## 13. Dependencies & Tooling
- **Backend:** Python 3.11, FastAPI, Uvicorn, pdfplumber, pandas, openpyxl (for Excel export), python-multipart.
- **Frontend:** Angular CLI 17, Tailwind CSS 3.x, Axios-equivalent (`HttpClient`), Angular CDK table.
- **Testing:** Pytest, FastAPI TestClient, Karma/Jasmine (or Jest) for Angular components, Cypress (future e2e).
- **Build:** Poetry or pip-tools (choose pip + requirements.txt for speed), npm for frontend.

## 14. Acceptance Criteria
- Given a valid PDF with tabular transactions, uploading it displays the parsed table within 10 seconds.
- Downloading as Excel provides a file with matching row counts and numeric formatting.
- Validation prevents non-PDF uploads and shows a meaningful error.
- Clearing the session removes stored data and resets the UI.
- Automated tests cover PDF parsing helper, API happy path, and Angular upload component basic rendering.

## 15. Risks & Mitigations
- **Parsing Variability:** Different bank statement formats may break parsing → start with specific structure, log anomalies, plan for format profiles.
- **PDF Quality:** Scanned PDFs not supported → communicate limitation prominently.
- **Memory Footprint:** Large statements may stress in-memory storage → enforce limits and provide informative errors.
- **Session Loss:** If server restarts, sessions vanish → acceptable for MVP; document behavior.

## 16. Milestones
1. **M1 – Backend MVP (Week 1):** Upload endpoint with parsing stub returning mock data.
2. **M2 – Frontend UI (Week 2):** Upload flow, table render with mocked API.
3. **M3 – Integration (Week 3):** Real parsing, end-to-end download, session handling.
4. **M4 – Stabilization (Week 4):** Tests, docs, polish, deploy checklist.

## 17. Open Questions
- Do we need localization or multi-currency formatting? (Default to USD formatting for MVP.)
- Should downloads include metadata (e.g., bank name, statement period)? (Nice-to-have.)
- Long-term storage or analytics? (Out of scope for MVP.)
