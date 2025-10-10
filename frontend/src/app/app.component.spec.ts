import { ComponentFixture, TestBed } from '@angular/core/testing';
import { of } from 'rxjs';

import { AppComponent } from './app.component';
import { StatementService } from './services/statement.service';

class StatementServiceStub {
  initializeSession() {
    return of(void 0);
  }
  uploadStatement() {
    return of();
  }
  fetchTransactions() {
    return of();
  }
  download() {
    return of();
  }
  clearSession() {
    return of(void 0);
  }
}

describe('AppComponent', () => {
  let fixture: ComponentFixture<AppComponent>;
  let component: AppComponent;

  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [AppComponent],
      providers: [{ provide: StatementService, useClass: StatementServiceStub }]
    }).compileComponents();

    fixture = TestBed.createComponent(AppComponent);
    component = fixture.componentInstance;
    fixture.detectChanges();
  });

  it('should create the app', () => {
    expect(component).toBeTruthy();
  });

  it('should render the hero headline', () => {
    const compiled = fixture.nativeElement as HTMLElement;
    const heading = compiled.querySelector('h1');
    expect(heading?.textContent).toContain('Turn PDF statements into clean spreadsheets');
  });
});
