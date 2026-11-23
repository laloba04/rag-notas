import { ApplicationConfig, provideBrowserGlobalErrorListeners } from '@angular/core';
import { ApiService } from './api.service';
import { MODO_DEMO, ReproductorDemo } from './demo';
import { DEMO } from './entorno';

export const appConfig: ApplicationConfig = {
  providers: [
    provideBrowserGlobalErrorListeners(),
    // Build "demo": la web es la misma, pero en vez de la API reproduce las respuestas grabadas.
    ...(DEMO ? [{ provide: ApiService, useExisting: ReproductorDemo }, { provide: MODO_DEMO, useValue: true }] : []),
  ],
};
