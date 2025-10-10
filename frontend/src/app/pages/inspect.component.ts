import { CommonModule } from '@angular/common';
import { HttpErrorResponse } from '@angular/common/http';
import { ChangeDetectionStrategy, ChangeDetectorRef, Component, OnInit } from '@angular/core';
import { DatePipe } from '@angular/common';
import { DomSanitizer, SafeHtml } from '@angular/platform-browser';
import { FormsModule } from '@angular/forms';
import { Router } from '@angular/router';
import { firstValueFrom } from 'rxjs';

import { StatementService } from '../services/statement.service';
import { StatementPayload, TransactionRow, PdfPage, DetectedTransaction } from '../models/statement.models';

@Component({
  selector: 'app-inspect',
  standalone: true,
  imports: [CommonModule, DatePipe, FormsModule],
  templateUrl: './inspect.component.html',
  styleUrls: ['./inspect.component.css'],
  changeDetection: ChangeDetectionStrategy.OnPush
})
export class InspectComponent implements OnInit {
  transactions: TransactionRow[] = [];
  detectedTransactions: DetectedTransaction[] = [];
  selectedIds = new Set<string>();
  warnings: string[] = [];
  errorMessage: string | null = null;
  isSubmitting = false;
  selectAll = true;
  pdfPages: PdfPage[] = [];
  currentPage = 0;
  showDetectedTable = true; // Show detected transactions by default
  isLoadingDetected = false;

  private readonly currencyFormatter = new Intl.NumberFormat('en-US', {
    style: 'currency',
    currency: 'USD',
    maximumFractionDigits: 2
  });

  constructor(
    private readonly statementService: StatementService,
    private readonly cdr: ChangeDetectorRef,
    private readonly router: Router,
    private readonly sanitizer: DomSanitizer
  ) {}

  async ngOnInit(): Promise<void> {
    try {
      // Load PDF text pages
      const pdfTextResponse = await firstValueFrom(this.statementService.getPdfText());
      this.pdfPages = pdfTextResponse.pages;
      
      // Load detected transactions with smart algorithm
      this.isLoadingDetected = true;
      const detectedResponse = await firstValueFrom(this.statementService.getDetectedTransactions());
      this.detectedTransactions = detectedResponse.detected_transactions;
      this.isLoadingDetected = false;
      
      // If no detected transactions, try loading regular transactions as fallback
      if (this.detectedTransactions.length === 0) {
        const payload = await firstValueFrom(this.statementService.fetchTransactions());
        this.transactions = payload.transactions;
        this.warnings = payload.warnings;
        this.transactions.forEach(txn => this.selectedIds.add(txn.id));
        this.showDetectedTable = false;
      }
      
      this.cdr.markForCheck();
    } catch (error) {
      this.handleError(error);
    }
  }

  toggleDetectedTransaction(index: number): void {
    this.detectedTransactions[index].is_confirmed = !this.detectedTransactions[index].is_confirmed;
    this.cdr.markForCheck();
  }

  toggleAllDetected(): void {
    const allConfirmed = this.detectedTransactions.every(t => t.is_confirmed);
    this.detectedTransactions.forEach(t => t.is_confirmed = !allConfirmed);
    this.cdr.markForCheck();
  }

  get confirmedCount(): number {
    return this.detectedTransactions.filter(t => t.is_confirmed).length;
  }

  getConfidenceColor(confidence: number): string {
    if (confidence >= 0.7) return 'text-emerald-400';
    if (confidence >= 0.5) return 'text-amber-400';
    return 'text-rose-400';
  }

  getConfidenceLabel(confidence: number): string {
    if (confidence >= 0.7) return 'High';
    if (confidence >= 0.5) return 'Medium';
    return 'Low';
  }

  async confirmDetectedTransactions(): Promise<void> {
    const confirmed = this.detectedTransactions.filter(t => t.is_confirmed);
    
    if (confirmed.length === 0) {
      this.errorMessage = 'Please select at least one transaction to confirm.';
      this.cdr.markForCheck();
      return;
    }

    this.isSubmitting = true;
    this.errorMessage = null;
    this.cdr.markForCheck();

    try {
      const payload = await firstValueFrom(
        this.statementService.confirmDetectedTransactions(confirmed)
      );
      // Navigate back to main page with confirmed transactions
      await this.router.navigate(['/']);
    } catch (error) {
      this.handleError(error);
      this.isSubmitting = false;
      this.cdr.markForCheck();
    }
  }

  toggleTransaction(id: string): void {
    if (this.selectedIds.has(id)) {
      this.selectedIds.delete(id);
    } else {
      this.selectedIds.add(id);
    }
    this.updateSelectAllState();
    this.cdr.markForCheck();
  }

  toggleSelectAll(): void {
    if (this.selectAll) {
      // Deselect all
      this.selectedIds.clear();
      this.selectAll = false;
    } else {
      // Select all
      this.transactions.forEach(txn => this.selectedIds.add(txn.id));
      this.selectAll = true;
    }
    this.cdr.markForCheck();
  }

  private updateSelectAllState(): void {
    this.selectAll = this.selectedIds.size === this.transactions.length;
  }

  isSelected(id: string): boolean {
    return this.selectedIds.has(id);
  }

  get selectedCount(): number {
    return this.selectedIds.size;
  }

  async confirmSelection(): Promise<void> {
    if (this.selectedIds.size === 0) {
      this.errorMessage = 'Please select at least one transaction.';
      this.cdr.markForCheck();
      return;
    }

    this.isSubmitting = true;
    this.errorMessage = null;
    this.cdr.markForCheck();

    try {
      await firstValueFrom(
        this.statementService.confirmSelectedTransactions(Array.from(this.selectedIds))
      );
      // Navigate back to main page
      await this.router.navigate(['/']);
    } catch (error) {
      this.handleError(error);
      this.isSubmitting = false;
      this.cdr.markForCheck();
    }
  }

  async cancel(): Promise<void> {
    await this.router.navigate(['/']);
  }

  nextPage(): void {
    if (this.currentPage < this.pdfPages.length - 1) {
      this.currentPage++;
      this.cdr.markForCheck();
    }
  }

  previousPage(): void {
    if (this.currentPage > 0) {
      this.currentPage--;
      this.cdr.markForCheck();
    }
  }

  get hasPreviousPage(): boolean {
    return this.currentPage > 0;
  }

  get hasNextPage(): boolean {
    return this.currentPage < this.pdfPages.length - 1;
  }

  get currentPageText(): string {
    return this.pdfPages[this.currentPage]?.text || '';
  }

  get currentPageHtml(): SafeHtml {
    const htmlContent = this.pdfPages[this.currentPage]?.text || '';
    return this.sanitizer.bypassSecurityTrustHtml(htmlContent);
  }

  get totalPages(): number {
    return this.pdfPages.length;
  }

  formatAmount(value: number | null): string {
    if (value === null) {
      return '—';
    }
    return this.currencyFormatter.format(value);
  }

  private handleError(error: unknown): void {
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
    this.cdr.markForCheck();
  }
}
