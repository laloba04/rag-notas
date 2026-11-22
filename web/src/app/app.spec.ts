import { TestBed } from '@angular/core/testing';
import { App } from './app';
import { ApiService } from './api.service';

describe('App', () => {
  it('monta el chat', async () => {
    TestBed.configureTestingModule({
      imports: [App],
      providers: [{ provide: ApiService, useValue: { salud: async () => { throw new Error(); } } }],
    });
    const fixture = TestBed.createComponent(App);
    await fixture.whenStable();
    expect((fixture.nativeElement as HTMLElement).querySelector('app-chat h1')?.textContent).toBe('rag-notas');
  });
});
