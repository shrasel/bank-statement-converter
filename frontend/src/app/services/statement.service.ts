import { Injectable } from '@angular/core';
import { HttpClient, HttpHeaders, HttpParams } from '@angular/common/http';
import { map, Observable } from 'rxjs';

import { 
  StatementPayload, StatementSummary, TransactionRow, PdfTextResponse,
  DetectedTransactionsResponse, DetectedTransaction
} from '../models/statement.models';
import { environment } from '../../environments/environment';

interface ApiTransaction {
  id: string;
  date: string;
  description: string;
  debit: string | number | null;
  credit: string | number | null;
  balance: string | number | null;
  warnings: string[];
}

interface ApiSummary {
  row_count: number;
  total_debit: string | number;
  total_credit: string | number;
}

interface ApiResponse {
  transactions: ApiTransaction[];
  summary: ApiSummary;
  warnings?: string[];
}

@Injectable({ providedIn: 'root' })
export class StatementService {
  private readonly baseUrl = environment.apiBaseUrl.replace(/\/$/, '');
  private sessionId: string | null = null;

  constructor(private readonly http: HttpClient) {}

  private getHeaders(): HttpHeaders {
    let headers = new HttpHeaders();
    if (this.sessionId) {
      headers = headers.set('X-Session-ID', this.sessionId);
    }
    return headers;
  }

  private extractSessionId(response: any): void {
    if (response && response.headers) {
      const sessionId = response.headers.get('X-Session-ID');
      if (sessionId) {
        this.sessionId = sessionId;
      }
    }
  }

  initializeSession(): Observable<void> {
    return this.http
      .post(`${this.baseUrl}/statements/session`, null, {
        withCredentials: true,
        observe: 'response',
        responseType: 'text'
      })
      .pipe(
        map((response) => {
          this.extractSessionId(response);
          return void 0;
        })
      );
  }

  uploadStatement(file: File): Observable<StatementPayload> {
    const formData = new FormData();
    formData.append('file', file);

    return this.http
      .post<ApiResponse>(`${this.baseUrl}/statements/upload`, formData, {
        withCredentials: true,
        observe: 'response',
        headers: this.getHeaders()
      })
      .pipe(
        map((response) => {
          this.extractSessionId(response);
          return this.toStatementPayload(response.body!);
        })
      );
  }

  fetchTransactions(): Observable<StatementPayload> {
    return this.http
      .get<ApiResponse>(`${this.baseUrl}/statements/transactions`, {
        withCredentials: true,
        observe: 'response',
        headers: this.getHeaders()
      })
      .pipe(
        map((response) => {
          this.extractSessionId(response);
          return this.toStatementPayload(response.body!);
        })
      );
  }

  async download(format: 'xlsx' | 'csv'): Promise<Blob> {
    const params = new HttpParams().set('format', format);
    const url = `${this.baseUrl}/statements/download?${params.toString()}`;

    const headers: HeadersInit = {
      ...(this.sessionId && { 'X-Session-ID': this.sessionId })
    };

    const response = await fetch(url, {
      credentials: 'include',
      headers
    });

    if (!response.ok) {
      const errorText = await response.text();
      throw new Error(errorText || `Download failed with status ${response.status}`);
    }

    const sessionId = response.headers.get('X-Session-ID');
    if (sessionId) {
      this.sessionId = sessionId;
    }

    return response.blob();
  }

  clearSession(): Observable<void> {
    return this.http
      .delete(`${this.baseUrl}/statements/session`, {
        withCredentials: true,
        responseType: 'text',
        headers: this.getHeaders()
      })
      .pipe(
        map(() => {
          this.sessionId = null;
          return void 0;
        })
      );
  }

  confirmSelectedTransactions(transactionIds: string[]): Observable<StatementPayload> {
    return this.http
      .post<ApiResponse>(`${this.baseUrl}/statements/confirm-transactions`, {
        transaction_ids: transactionIds
      }, {
        withCredentials: true,
        observe: 'response',
        headers: this.getHeaders()
      })
      .pipe(
        map((response) => {
          this.extractSessionId(response);
          return this.toStatementPayload(response.body!);
        })
      );
  }

  getPdfUrl(): string {
    const headers = this.getHeaders();
    const sessionIdParam = this.sessionId ? `?session_id=${this.sessionId}` : '';
    return `${this.baseUrl}/statements/pdf${sessionIdParam}`;
  }

  async getPdfBlob(): Promise<Blob> {
    const url = `${this.baseUrl}/statements/pdf`;
    const headers: HeadersInit = {
      ...(this.sessionId && { 'X-Session-ID': this.sessionId })
    };

    const response = await fetch(url, {
      credentials: 'include',
      headers
    });

    if (!response.ok) {
      const errorText = await response.text();
      throw new Error(errorText || `Failed to load PDF with status ${response.status}`);
    }

    return response.blob();
  }

  getPdfText(): Observable<PdfTextResponse> {
    return this.http
      .get<PdfTextResponse>(`${this.baseUrl}/statements/pdf-text`, {
        withCredentials: true,
        observe: 'response',
        headers: this.getHeaders()
      })
      .pipe(
        map((response) => {
          this.extractSessionId(response);
          return response.body!;
        })
      );
  }

  getDetectedTransactions(): Observable<DetectedTransactionsResponse> {
    return this.http
      .get<DetectedTransactionsResponse>(`${this.baseUrl}/statements/detect-transactions`, {
        withCredentials: true,
        observe: 'response',
        headers: this.getHeaders()
      })
      .pipe(
        map((response) => {
          this.extractSessionId(response);
          return response.body!;
        })
      );
  }

  confirmDetectedTransactions(transactions: DetectedTransaction[]): Observable<StatementPayload> {
    return this.http
      .post<ApiResponse>(`${this.baseUrl}/statements/confirm-detected-transactions`, {
        confirmed_transactions: transactions
      }, {
        withCredentials: true,
        observe: 'response',
        headers: this.getHeaders()
      })
      .pipe(
        map((response) => {
          this.extractSessionId(response);
          return this.toStatementPayload(response.body!);
        })
      );
  }

  private toStatementPayload(response: ApiResponse): StatementPayload {
    const transactions: TransactionRow[] = response.transactions.map((txn) => ({
      id: txn.id,
      date: txn.date,
      description: txn.description,
      debit: this.parseAmount(txn.debit),
      credit: this.parseAmount(txn.credit),
      balance: this.parseAmount(txn.balance),
      warnings: txn.warnings ?? []
    }));

    const summary: StatementSummary = {
      rowCount: response.summary.row_count,
      totalDebit: this.parseAmount(response.summary.total_debit) ?? 0,
      totalCredit: this.parseAmount(response.summary.total_credit) ?? 0
    };

    return {
      transactions,
      summary,
      warnings: response.warnings ?? []
    };
  }

  private parseAmount(value: string | number | null | undefined): number | null {
    if (value === null || value === undefined || value === '') {
      return null;
    }
    const normalized = typeof value === 'number' ? value : Number(String(value));
    return Number.isFinite(normalized) ? normalized : null;
  }
}
