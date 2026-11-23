import { bootstrapApplication } from '@angular/platform-browser';
import { appConfig } from './app/app.config';
import { App } from './app/app';
import { aplicarTema, leerTema } from './app/tema';

// El tema elegido se aplica lo antes posible, antes de arrancar Angular.
// Va aquí y no en un <script> en línea porque la CSP (script-src 'self') no lo dejaría.
aplicarTema(leerTema());

bootstrapApplication(App, appConfig)
  .catch((err) => console.error(err));
