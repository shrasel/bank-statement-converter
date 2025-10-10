export interface TransactionRow {
  id: string;
  date: string;
  description: string;
  debit: number | null;
  credit: number | null;
  balance: number | null;
  warnings: string[];
}

export interface StatementSummary {
  rowCount: number;
  totalDebit: number;
  totalCredit: number;
}

export interface StatementPayload {
  transactions: TransactionRow[];
  summary: StatementSummary;
  warnings: string[];
}

export interface PdfPage {
  page_number: number;
  text: string;
  width: number;
  height: number;
}

export interface PdfTextResponse {
  pages: PdfPage[];
  total_pages: number;
}

export interface DetectedTransaction {
  row_number: number;
  date: string | null;
  description: string | null;
  debit: string | null;
  credit: string | null;
  balance: string | null;
  confidence: number;
  raw_text: string;
  is_confirmed: boolean;
}

export interface DetectedTransactionsResponse {
  detected_transactions: DetectedTransaction[];
  total_found: number;
  confidence_summary: {
    high: number;
    medium: number;
    low: number;
  };
}
