import { ChangeDetectionStrategy, Component, OnInit, inject, signal } from '@angular/core';
import { ApiService } from '../api.service';
import { ErrorApi, Fragmento, Modo, Respuesta, Salud } from '../api.types';

/** Un trozo de la respuesta: texto normal o una cita [n] que se puede pulsar. */
export type Parte = { texto: string } | { cita: Fragmento };

export interface Turno {
  pregunta: string;
  modo: Modo;
  estado: 'pensando' | 'hecho' | 'error' | 'cancelado';
  nodos: string[]; // progreso del grafo, según llega por SSE
  respuesta?: Respuesta;
  partes?: Parte[];
  error?: string;
  citaAbierta?: number;
}

/** Qué hace cada nodo del grafo, para el panel de la traza. */
export const NODOS: Record<string, string> = {
  decidir: '¿Hace falta buscar?',
  responder_directo: 'Responde sin buscar (saludo o pregunta sobre la conversación)',
  recuperar: 'Busca en la documentación (vectorial + reranker)',
  evaluar: 'Comprueba si lo encontrado sirve',
  reescribir: 'No sirve: reformula la pregunta',
  generar: 'Redacta la respuesta con citas',
  sin_respuesta: 'No hay nada útil en la documentación',
  interrumpido: 'Interrumpido',
};

const LONGITUD_MAX = 500; // la misma que config.LONGITUD_MAX_PREGUNTA en la API

/**
 * Parte la respuesta en texto y citas. Solo es cita un [n] que corresponde a un fragmento que
 * ha mandado la API; cualquier otro [n] se queda como texto. Todo se pinta con interpolación de
 * Angular (escapado): ni innerHTML ni Markdown, porque la respuesta sale de documentos no
 * confiables y una imagen o un enlace podrían filtrar datos a otro servidor.
 */
export function partirRespuesta(respuesta: Respuesta): Parte[] {
  const porNumero = new Map(respuesta.citas.map((c) => [c.numero, c]));
  const partes: Parte[] = [];
  let ultimo = 0;
  for (const m of respuesta.respuesta.matchAll(/\[(\d+)\]/g)) {
    const cita = porNumero.get(Number(m[1]));
    if (!cita) continue;
    if (m.index > ultimo) partes.push({ texto: respuesta.respuesta.slice(ultimo, m.index) });
    partes.push({ cita });
    ultimo = m.index + m[0].length;
  }
  if (ultimo < respuesta.respuesta.length) partes.push({ texto: respuesta.respuesta.slice(ultimo) });
  return partes;
}

@Component({
  selector: 'app-chat',
  templateUrl: './chat.html',
  styleUrl: './chat.css',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class Chat implements OnInit {
  private readonly api = inject(ApiService);

  protected readonly turnos = signal<Turno[]>([]);
  protected readonly pregunta = signal('');
  protected readonly modo = signal<Modo>('grafo');
  protected readonly ocupado = signal(false);
  protected readonly salud = signal<Salud | null>(null);
  protected readonly sinConexion = signal(false);
  protected readonly NODOS = NODOS;
  protected readonly LONGITUD_MAX = LONGITUD_MAX;
  private cancelar?: AbortController;

  async ngOnInit() {
    try {
      this.salud.set(await this.api.salud());
    } catch {
      this.sinConexion.set(true);
    }
  }

  protected esCita(p: Parte): p is { cita: Fragmento } {
    return 'cita' in p;
  }

  protected urlSegura(url: string): string | null {
    // Solo enlaces https a la documentación original; cualquier otra cosa no se enlaza.
    return /^https:\/\//.test(url) ? url : null;
  }

  protected escribir(evento: Event) {
    this.pregunta.set((evento.target as HTMLTextAreaElement).value);
  }

  protected teclear(evento: KeyboardEvent) {
    if (evento.key === 'Enter' && !evento.shiftKey) {
      evento.preventDefault();
      this.enviar();
    }
  }

  protected alternarCita(indice: number, numero: number) {
    this.actualizar(indice, (t) => ({ citaAbierta: t.citaAbierta === numero ? undefined : numero }));
  }

  protected citaAbierta(turno: Turno): Fragmento | undefined {
    return turno.respuesta?.citas.find((c) => c.numero === turno.citaAbierta);
  }

  protected latencia(turno: Turno, posicion: number): number | undefined {
    return turno.respuesta?.traza?.latencias[posicion]?.s;
  }

  async enviar() {
    const pregunta = this.pregunta().trim();
    if (!pregunta || this.ocupado()) return;
    const modo = this.modo();
    this.turnos.update((t) => [...t, { pregunta, modo, estado: 'pensando', nodos: [] }]);
    const indice = this.turnos().length - 1;
    this.pregunta.set('');
    this.ocupado.set(true);
    this.cancelar = new AbortController();
    try {
      const respuesta = await this.api.preguntar(
        pregunta,
        modo,
        (nodo) => this.actualizar(indice, (t) => ({ nodos: [...t.nodos, nodo] })),
        this.cancelar.signal,
      );
      this.actualizar(indice, () => ({ estado: 'hecho', respuesta, partes: partirRespuesta(respuesta) }));
      this.salud.update((s) => (s ? { ...s, modelos_cargados: true } : s));
    } catch (e) {
      if (e instanceof DOMException && e.name === 'AbortError') {
        this.actualizar(indice, () => ({ estado: 'cancelado' }));
      } else {
        const mensaje =
          e instanceof ErrorApi ? e.message : 'No se puede conectar con la API. ¿Está arrancada?';
        this.actualizar(indice, () => ({ estado: 'error', error: mensaje }));
      }
    } finally {
      this.ocupado.set(false);
      this.cancelar = undefined;
    }
  }

  protected detener() {
    this.cancelar?.abort();
  }

  private actualizar(indice: number, cambio: (t: Turno) => Partial<Turno>) {
    this.turnos.update((lista) => lista.map((t, i) => (i === indice ? { ...t, ...cambio(t) } : t)));
  }
}
