import { CommonModule } from '@angular/common';
import { HttpErrorResponse } from '@angular/common/http';
import {
  ChangeDetectionStrategy,
  ChangeDetectorRef,
  Component,
  ElementRef,
  OnInit,
  ViewChild
} from '@angular/core';
import { DatePipe } from '@angular/common';
import { Router } from '@angular/router';
import { firstValueFrom } from 'rxjs';

import { StatementService } from '../services/statement.service';
import { StatementPayload, StatementSummary, TransactionRow } from '../models/statement.models';

type UiState = 'idle' | 'uploaded' | 'uploading' | 'ready' | 'error';

@Component({
  selector: 'app-home',
  standalone: true,
  imports: [CommonModule, DatePipe],
  templateUrl: './home.component.html',
  styleUrls: ['./home.component.css'],
  changeDetection: ChangeDetectionStrategy.OnPush
})
export class HomeComponent implements OnInit {
  @ViewChild('fileInput') private readonly fileInput?: ElementRef<HTMLInputElement>;

  readonly steps = [
    {
      title: 'Upload your statement',
      description: 'Pick a PDF bank statement. We run client-side checks before sending it to the secure parser.'
    },
    {
      title: 'Review transactions',
      description: 'We extract every row, highlight anomalies, and summarize totals so you can validate quickly.'
    },
    {
      title: 'Download clean data',
      description: 'Export instantly to Excel or CSV, ready for spreadsheets or accounting imports.'
    }
  ];

  status: UiState = 'idle';
  currentFileName: string | null = null;
  transactions: TransactionRow[] = [];
  summary: StatementSummary | null = null;
  warnings: string[] = [];
  errorMessage: string | null = null;
  infoMessage: string | null = null;
  isDragging = false;

  private readonly currencyFormatter = new Intl.NumberFormat('en-US', {
    style: 'currency',
    currency: 'USD',
    maximumFractionDigits: 2
  });

  constructor(
    private readonly statementService: StatementService,
    private readonly cdr: ChangeDetectorRef,
    private readonly router: Router
  ) {}

  async ngOnInit(): Promise<void> {
    try {
      await firstValueFrom(this.statementService.initializeSession());
      // Check if we have transactions (came back from inspection)
      try {
        const payload = await firstValueFrom(this.statementService.fetchTransactions());
        this.applyStatementPayload(payload);
        this.status = 'ready';
        this.infoMessage = 'Transactions loaded. Review the results below and download when ready.';
      } catch {
        // No transactions yet, stay in idle state
      }
    } catch (error) {
      this.handleError(error);
    }
    this.cdr.markForCheck();
  }

  onDragOver(event: DragEvent): void {
    event.preventDefault();
    this.isDragging = true;
  }

  onDragLeave(event: DragEvent): void {
    event.preventDefault();
    this.isDragging = false;
  }

  async onDrop(event: DragEvent): Promise<void> {
    event.preventDefault();
    this.isDragging = false;
    if (!event.dataTransfer?.files?.length) {
      return;
    }
    const file = event.dataTransfer.files[0];
    await this.handleFileSelection(file);
  }

  triggerFileDialog(): void {
    this.fileInput?.nativeElement.click();
  }

  async onFileChange(event: Event): Promise<void> {
    const input = event.target as HTMLInputElement;
    if (!input.files || input.files.length === 0) {
      return;
    }
    const file = input.files[0];
    await this.handleFileSelection(file);
  }

  async download(format: 'xlsx' | 'csv'): Promise<void> {
    try {
      const blob = await this.statementService.download(format);
      const url = window.URL.createObjectURL(blob);
      const anchor = document.createElement('a');
      anchor.href = url;
      anchor.download = format === 'xlsx' ? 'transactions.xlsx' : 'transactions.csv';
      anchor.click();
      window.URL.revokeObjectURL(url);
    } catch (error) {
      this.handleError(error, false);
    }
  }

  async reset(): Promise<void> {
    try {
      await firstValueFrom(this.statementService.clearSession());
    } finally {
      this.status = 'idle';
      this.transactions = [];
      this.summary = null;
      this.warnings = [];
      this.currentFileName = null;
      this.errorMessage = null;
      this.infoMessage = null;
      if (this.fileInput) {
        this.fileInput.nativeElement.value = '';
      }
      this.cdr.markForCheck();
    }
  }

  formatAmount(value: number | null): string {
    if (value === null) {
      return '—';
    }
    return this.currencyFormatter.format(value);
  }

  private async handleFileSelection(file: File): Promise<void> {
    this.errorMessage = null;
    this.infoMessage = null;

    if (!file.name.toLowerCase().endsWith('.pdf')) {
      this.errorMessage = 'Only PDF files are supported right now.';
      return;
    }

    if (file.size === 0) {
      this.errorMessage = 'The selected file is empty.';
      return;
    }

    if (file.size > 10 * 1024 * 1024) {
      this.errorMessage = 'Files larger than 10 MB cannot be processed.';
      return;
    }

    this.currentFileName = file.name;
    this.cdr.markForCheck();
    await this.uploadStatement(file);
  }

  private async uploadStatement(file: File): Promise<void> {
    this.status = 'uploading';
    this.cdr.markForCheck();
    try {
      const payload = await firstValueFrom(this.statementService.uploadStatement(file));
      // Store the payload temporarily but don't display it yet
      this.applyStatementPayload(payload);
      this.status = 'uploaded';
      this.infoMessage = 'PDF uploaded successfully! Choose how to proceed:';
      this.cdr.markForCheck();
    } catch (error) {
      this.handleError(error);
    }
  }

  async parseDirectly(): Promise<void> {
    // Transactions are already stored, just show them
    this.status = 'ready';
    this.infoMessage = 'Parsed successfully. Review the results below and download when ready.';
    this.cdr.markForCheck();
  }

  async inspectTransactions(): Promise<void> {
    // Navigate to inspection page
    await this.router.navigate(['/inspect']);
  }

  private applyStatementPayload(payload: StatementPayload): void {
    this.transactions = payload.transactions;
    this.summary = payload.summary;
    this.warnings = payload.warnings;
    this.cdr.markForCheck();
  }

  private handleError(error: unknown, setErrorState = true): void {
    let message = 'Something went wrong. Please try again.';
    if (error instanceof HttpErrorResponse) {
      if (typeof error.error === 'string' && error.error.trim().length > 0) {
        message = error.error;
      } else if (error.error?.detail) {
        message = error.error.detail;
      } else if (error.message) {
        message = error.message;
      }
    }
    this.errorMessage = message;
    if (setErrorState) {
      this.status = 'error';
    }
    this.cdr.markForCheck();
  }
}
