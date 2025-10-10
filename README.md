# Bank Statement Converter

A full-stack application that ingests bank statement PDFs, extracts structured transaction data, and lets users explore totals and download the results as Excel or CSV files. The project combines a FastAPI backend for PDF parsing with an Angular + Tailwind frontend for a polished, responsive experience.

## Features

- **PDF parsing** powered by `pdfplumber`, `pandas`, and custom heuristics that normalize dates and amounts.
- **Session-aware workflow** that stores parsed transactions in-memory for quick refreshes and downloads.
- **Rich UI** built with Angular 17, Tailwind CSS, and server-side rendering support for fast loading.
- **Multiple export options** (CSV and XLSX) with instant downloads.
- **Resilient error handling** including warnings surfaced to the frontend when parsing is incomplete.

## Architecture

```
.
├── backend/      # FastAPI application (PDF parsing, REST API, session store)
├── frontend/     # Angular SPA + SSR shell consuming the API
├── docs/         # Additional documentation and assets (if any)
├── .gitignore    # Repo-wide ignore rules
├── README.md
└── .venv/        # Optional local Python virtual environment (ignored)
```

## Prerequisites

- **Python** 3.11 or newer
- **Node.js** 18.x (Angular CLI 17 requires Node 18+)
- **npm** 9+ (bundled with Node 18)

> The repository assumes a Unix-like environment (macOS/Linux). Windows users can adapt commands to PowerShell or WSL.

## Backend setup (FastAPI)

```bash
cd backend
python -m venv ../.venv
source ../.venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
```

Run the API locally:

```bash
cd backend
source ../.venv/bin/activate
uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

Key endpoints (prefixed with `/api`):

- `POST /api/session` – ensure a session cookie exists
- `POST /api/upload` – upload a PDF and receive parsed transactions, summary metrics, and warnings
- `GET /api/transactions` – fetch transactions for the active session
- `GET /api/download?format=csv|xlsx` – download parsed data as CSV or Excel
- `DELETE /api/session` – clear the current session

### Environment configuration

Backend settings are managed via `pydantic-settings`. Override defaults by adding a `.env` file in `backend/` (referenced automatically). Useful keys:

- `CORS_ORIGINS` – comma-separated list of allowed origins
- `SECRET_KEY` – **always change in production**
- `SESSION_TTL_SECONDS`, `SESSION_COOKIE_MAX_AGE` – session lifetime controls
- `MAX_UPLOAD_SIZE_BYTES` – upload size cap (default 10 MB)

## Frontend setup (Angular 17 + Tailwind)

```bash
cd frontend
npm install
```

Run the development server:

```bash
cd frontend
npm start
```

The app is served at <http://localhost:4200> and proxies requests to the backend at `http://localhost:8000/api` (configured in `src/environments/`).

### Production / SSR build

```bash
cd frontend
npm run build
npm run serve:ssr:frontend
```

This builds the Angular application and serves the server-rendered bundle from `dist/frontend/server/server.mjs`.

## Running the stack together

1. Start the backend API (`uvicorn app.main:app ...`) from the project root or `backend/` directory.
2. In a new terminal, start the Angular dev server with `npm start` from `frontend/`.
3. Open the frontend at <http://localhost:4200>, upload a statement PDF, and download CSV/XLSX outputs.

## Testing

Backend tests (run from `backend/` with the virtual environment activated):

```bash
cd backend
python -m pytest
```

Frontend unit tests:

```bash
cd frontend
npm test
```

> Additional end-to-end coverage can be added using Cypress or Playwright; the current project focuses on unit and integration tests for the backend parser.

## Deployment notes

- Use a production-ready ASGI server (e.g., `uvicorn` behind `gunicorn` or `hypercorn`) for the FastAPI app.
- Ensure the backend `SECRET_KEY` and CORS origins are set via environment variables in production.
- Build the frontend (`npm run build`) and serve the contents of `frontend/dist/frontend/browser` via a CDN or static host. The SSR bundle (`dist/frontend/server`) can be deployed alongside Node if server-rendering is required.
- Configure HTTPS and secure cookies when deploying beyond localhost.

## Troubleshooting

- **`422 Unprocessable Entity` on upload** – The parser could not extract transactions. Warnings in the response detail missing tables or unsupported layouts.
- **CORS errors** – Update the backend `CORS_ORIGINS` setting to include your frontend host.
- **Large PDFs failing** – Increase `MAX_UPLOAD_SIZE_BYTES` in the backend settings or compress the PDF before upload.

## Contributing

1. Fork the repository and create a feature branch.
2. Ensure tests pass (`python -m pytest`, `npm test`).
3. Follow existing formatting conventions (Black/Flake8 or Ruff for Python, Angular CLI defaults for TypeScript).
4. Open a pull request describing your change and testing strategy.

## License

Specify your license of choice here (e.g., MIT, Apache 2.0). Update this section before publishing the repository.
