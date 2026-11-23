import { Injectable, InjectionToken, inject } from '@angular/core';
import { FETCH } from './api.service';
import { ErrorApi, Modo, Respuesta, Salud } from './api.types';

/** true en el build "demo": la web no tiene API y reproduce respuestas grabadas. */
export const MODO_DEMO = new InjectionToken<boolean>('MODO_DEMO', { factory: () => false });

/** Dónde está el fichero que genera scripts/grabar_demo.py (relativo al <base href>). */
export const URL_GRABACIONES = new InjectionToken<string>('URL_GRABACIONES', {
  factory: () => 'demo/grabaciones.json',
});

export interface Grabacion {
  pregunta: string;
  modo: Modo;
  nodos: { nodo: string; ms: number }[]; // cuándo llegó cada nodo, en ms desde que se envió
  ms_total: number; // cuándo llegó la respuesta
  respuesta: Respuesta;
}

export interface Grabaciones {
  grabado: string; // fecha ISO de la grabación
  llm: { proveedor: string; modelo: string | null };
  fragmentos: number;
  grabaciones: Grabacion[];
}

/**
 * Sustituye a ApiService en el build "demo". Misma interfaz: salud() y preguntar(). preguntar()
 * no inventa nada: devuelve la respuesta grabada de esa pregunta y modo, y manda cada nodo con el
 * mismo retraso con el que llegó al grabarla (velocidad real).
 */
@Injectable({ providedIn: 'root' })
export class ReproductorDemo {
  private readonly fetch = inject(FETCH);
  private readonly url = inject(URL_GRABACIONES);
  private datos?: Promise<Grabaciones>;

  grabaciones(): Promise<Grabaciones> {
    this.datos ??= this.fetch(this.url).then((r) => {
      if (!r.ok) throw new ErrorApi('No se han podido cargar las respuestas grabadas.', 'demo', r.status);
      return r.json();
    });
    // Si falla, que se pueda reintentar.
    this.datos.catch(() => (this.datos = undefined));
    return this.datos;
  }

  /** Los datos de la grabación con la forma de /salud. comprobado: false, porque no hay LLM en vivo. */
  async salud(): Promise<Salud> {
    const g = await this.grabaciones();
    return {
      estado: 'ok',
      version: 'demo',
      coleccion: { ok: true, fragmentos: g.fragmentos },
      llm: { proveedor: g.llm.proveedor, modelo: g.llm.modelo, ok: true, comprobado: false },
      modelos_cargados: true,
    };
  }

  async preguntar(
    pregunta: string,
    modo: Modo,
    alPasar: (nodo: string) => void,
    senal?: AbortSignal,
  ): Promise<Respuesta> {
    const g = (await this.grabaciones()).grabaciones.find(
      (x) => x.pregunta === pregunta.trim() && x.modo === modo,
    );
    if (!g) throw new ErrorApi('En la demo solo se pueden hacer las preguntas grabadas.', 'demo', 0);

    return new Promise<Respuesta>((resolver, rechazar) => {
      const relojes = g.nodos.map((n) => setTimeout(() => alPasar(n.nodo), n.ms));
      relojes.push(setTimeout(() => resolver(g.respuesta), g.ms_total));
      senal?.addEventListener('abort', () => {
        relojes.forEach(clearTimeout);
        rechazar(new DOMException('Cancelada', 'AbortError'));
      }, { once: true });
    });
  }
}
