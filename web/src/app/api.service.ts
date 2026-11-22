import { Injectable, InjectionToken, inject } from '@angular/core';
import { ErrorApi, Modo, Respuesta, Salud } from './api.types';

/** Dirección de la API. Por defecto /api, en el mismo origen: en desarrollo lo reenvía el proxy
 * de ng serve (proxy.conf.json) y en Docker, nginx. Así no hace falta CORS. */
export const API_URL = new InjectionToken<string>('API_URL', { factory: () => '/api' });

/** fetch inyectable, para poder sustituirlo en los tests. */
export const FETCH = new InjectionToken<typeof fetch>('FETCH', {
  factory: () => fetch.bind(globalThis),
});

@Injectable({ providedIn: 'root' })
export class ApiService {
  private readonly base = inject(API_URL);
  private readonly fetch = inject(FETCH);

  async salud(): Promise<Salud> {
    const r = await this.fetch(`${this.base}/salud`);
    // 503 (sin colección) también trae el cuerpo de /salud.
    if (!r.ok && r.status !== 503) throw await errorDe(r);
    return r.json();
  }

  /**
   * Pregunta en streaming (SSE). La API manda el nombre de cada nodo del grafo según termina
   * (alPasar) y, al final, la respuesta ya validada. Se usa fetch y no EventSource porque
   * EventSource solo admite GET. Con `senal` se cancela: la API corta el grafo en el siguiente nodo.
   */
  async preguntar(
    pregunta: string,
    modo: Modo,
    alPasar: (nodo: string) => void,
    senal?: AbortSignal,
  ): Promise<Respuesta> {
    const r = await this.fetch(`${this.base}/preguntar`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', Accept: 'text/event-stream' },
      body: JSON.stringify({ pregunta, modo }),
      signal: senal,
    });
    if (!r.ok || !r.body) throw await errorDe(r);

    const lector = r.body.pipeThrough(new TextDecoderStream()).getReader();
    let pendiente = '';
    for (;;) {
      const { value, done } = await lector.read();
      if (done) break;
      pendiente += value;
      // Cada evento SSE termina con una línea en blanco.
      let fin: number;
      while ((fin = pendiente.indexOf('\n\n')) >= 0) {
        const evento = leerEvento(pendiente.slice(0, fin));
        pendiente = pendiente.slice(fin + 2);
        if (!evento) continue;
        if (evento.tipo === 'nodo') alPasar(evento.datos.nodo);
        else if (evento.tipo === 'respuesta') return evento.datos as Respuesta;
        else if (evento.tipo === 'error')
          throw new ErrorApi(evento.datos.error, evento.datos.tipo, r.status);
      }
    }
    throw new ErrorApi('La conexión se cortó antes de recibir la respuesta.', 'interno', r.status);
  }
}

function leerEvento(bloque: string): { tipo: string; datos: any } | null {
  let tipo = 'message';
  let datos = '';
  for (const linea of bloque.split('\n')) {
    if (linea.startsWith('event:')) tipo = linea.slice(6).trim();
    else if (linea.startsWith('data:')) datos += linea.slice(5).trim();
  }
  if (!datos) return null;
  try {
    return { tipo, datos: JSON.parse(datos) };
  } catch {
    return null;
  }
}

async function errorDe(r: Response): Promise<ErrorApi> {
  try {
    const cuerpo = await r.json();
    if (typeof cuerpo?.error === 'string') return new ErrorApi(cuerpo.error, cuerpo.tipo, r.status);
  } catch {
    // sin cuerpo JSON (p. ej., un proxy que responde con HTML)
  }
  return new ErrorApi(`El servidor ha respondido con un error (${r.status}).`, 'interno', r.status);
}
