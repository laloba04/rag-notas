import {
  ChangeDetectionStrategy, Component, DestroyRef, OnInit, computed, inject, signal,
} from '@angular/core';
import { NgTemplateOutlet } from '@angular/common';
import { ApiService } from '../api.service';
import { ErrorApi, Modo, Respuesta, Salud } from '../api.types';
import { Fila, analizar, maquetar } from '../markdown';
import { Tema, cambiarTema, leerTema } from '../tema';

export interface Turno {
  pregunta: string;
  modo: Modo;
  estado: 'pensando' | 'hecho' | 'error' | 'cancelado';
  inicio: number; // ms (Date.now) al enviar
  nodos: string[]; // progreso del grafo, según llega por SSE
  marcas: number[]; // ms desde el inicio en que llegó cada nodo (medido en el navegador)
  respuesta?: Respuesta;
  filas?: Fila[];
  error?: string;
}

/** Qué hace cada nodo del grafo. */
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

/** Preguntas de ejemplo de la portada. Probadas contra el RAG local (qwen2.5:14b, 22 y 23-11-2026):
 * las tres respondieron con cita en 2 de 2 intentos. «¿Cómo creo una rama nueva y me cambio a
 * ella?» se descartó: el 23-11 la validación de salida la bloqueó 3 de 3 veces (el modelo no citaba). */
export const SUGERENCIAS = [
  // color: clase de Tailwind escrita entera, para que Tailwind la encuentre al compilar.
  { color: 'text-git', nombre: 'Pro Git', pregunta: '¿Para qué sirve git stash?' },
  { color: 'text-http', nombre: 'MDN HTTP', pregunta: '¿Para qué sirve la cabecera Cache-Control?' },
  { color: 'text-k8s', nombre: 'Kubernetes', pregunta: '¿Qué es un ReplicaSet?' },
] as const;

const LONGITUD_MAX = 500; // la misma que config.LONGITUD_MAX_PREGUNTA en la API

export interface Barra {
  nodo: string;
  inicio: number; // % del total
  ancho: number; // % del total
  s: number | null; // segundos; null si el paso sigue en curso
  tipo: 'normal' | 'reintento' | 'final' | 'activa';
}

/**
 * Las barras de la traza en cascada. Con la respuesta, salen de las latencias que mide la API.
 * Mientras piensa, de la hora a la que llega cada nodo por SSE (medida en el navegador), más una
 * barra activa desde el último nodo hasta ahora: no se sabe cuál será el siguiente nodo.
 */
export function cascada(turno: Turno, ahora: number): Barra[] {
  const tipoDe = (nodo: string, ultimo: boolean): Barra['tipo'] =>
    nodo === 'reescribir' || nodo === 'sin_respuesta' ? 'reintento' : ultimo ? 'final' : 'normal';

  if (turno.estado === 'pensando') {
    const total = Math.max(ahora - turno.inicio, turno.marcas.at(-1) ?? 0, 1);
    const barras: Barra[] = [];
    let previa = 0;
    turno.nodos.forEach((nodo, i) => {
      const fin = turno.marcas[i];
      barras.push({ nodo, inicio: (previa / total) * 100, ancho: ((fin - previa) / total) * 100,
        s: (fin - previa) / 1000, tipo: tipoDe(nodo, false) });
      previa = fin;
    });
    barras.push({ nodo: '…', inicio: (previa / total) * 100, ancho: ((total - previa) / total) * 100,
      s: null, tipo: 'activa' });
    return barras;
  }

  const latencias = turno.respuesta?.traza?.latencias ?? [];
  const total = latencias.reduce((suma, l) => suma + l.s, 0) || 1;
  let previa = 0;
  return latencias.map((l, i) => {
    const barra: Barra = { nodo: l.nodo, inicio: (previa / total) * 100, ancho: (l.s / total) * 100,
      s: l.s, tipo: tipoDe(l.nodo, i === latencias.length - 1) };
    previa += l.s;
    return barra;
  });
}

/** De qué documentación es un fragmento, por el nombre del fichero (p. ej., 12-git-ramas.md). */
export function corpus(documento: string): { nombre: string; clase: string } {
  if (/^\d+-k8s-/.test(documento)) return { nombre: 'Kubernetes', clase: 'k8s' };
  if (/^\d+-git-/.test(documento)) return { nombre: 'Pro Git', clase: 'git' };
  if (/^\d+-http-/.test(documento)) return { nombre: 'MDN HTTP', clase: 'http' };
  return { nombre: 'Documentación', clase: 'otro' };
}

/** Segundos con coma decimal: 9.9 → "9,9". */
export function segundos(s: number, decimales = 1): string {
  return s.toFixed(decimales).replace('.', ',');
}

@Component({
  selector: 'app-chat',
  imports: [NgTemplateOutlet],
  templateUrl: './chat.html',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class Chat implements OnInit {
  private readonly api = inject(ApiService);

  protected readonly turnos = signal<Turno[]>([]);
  /** El más reciente arriba, con su número de consulta. */
  protected readonly informes = computed(() =>
    this.turnos().map((turno, i) => ({ turno, i })).reverse(),
  );
  /** Frase corta para la región de estado (lectores de pantalla) sobre la última consulta. */
  protected readonly anuncio = computed(() => {
    const t = this.turnos().at(-1);
    if (!t) return '';
    const n = `Consulta ${this.turnos().length}`;
    switch (t.estado) {
      case 'pensando': return `${n} en curso.`;
      case 'cancelado': return `${n} cancelada.`;
      case 'error': return `${n}: error.`;
      case 'hecho':
      {
        const r = t.respuesta!;
        if (r.salida_bloqueada) return `${n}: respuesta descartada por la validación de salida.`;
        if (r.no_lo_tengo) return `${n}: no lo tiene en la documentación.`;
        const k = r.citas.length;
        return `${n} respondida en ${segundos(r.segundos)} segundos, con ${k} ${k === 1 ? 'fuente' : 'fuentes'}.`;
      }
    }
  });
  protected readonly tema = signal<Tema>(leerTema());
  protected readonly pregunta = signal('');
  protected readonly modo = signal<Modo>('grafo');
  protected readonly ocupado = signal(false);
  protected readonly salud = signal<Salud | null>(null);
  protected readonly sinConexion = signal(false);
  protected readonly ahora = signal(Date.now());
  protected readonly NODOS = NODOS;
  protected readonly SUGERENCIAS = SUGERENCIAS;
  protected readonly LONGITUD_MAX = LONGITUD_MAX;
  protected readonly cascada = cascada;
  protected readonly corpus = corpus;
  protected readonly segundos = segundos;
  private cancelar?: AbortController;
  private reloj?: ReturnType<typeof setInterval>;

  constructor() {
    inject(DestroyRef).onDestroy(() => this.pararReloj());
  }

  async ngOnInit() {
    try {
      this.salud.set(await this.api.salud());
    } catch {
      this.sinConexion.set(true);
    }
  }

  protected urlSegura(url: string): string | null {
    // Solo enlaces https a la documentación original; cualquier otra cosa no se enlaza.
    return /^https:\/\//.test(url) ? url : null;
  }

  protected idNota(turno: number, cita: number): string {
    return `nota-${turno + 1}-${cita}`;
  }

  protected numero(i: number): string {
    return String(i + 1).padStart(2, '0');
  }

  protected seccionCorta(seccion: string): string {
    return seccion.split('>').at(-1)!.trim();
  }

  protected extracto(texto: string): string {
    const plano = texto.replace(/\s+/g, ' ').trim();
    return plano.length > 160 ? plano.slice(0, 160).trimEnd() + '…' : plano;
  }

  protected escribir(evento: Event) {
    this.pregunta.set((evento.target as HTMLInputElement).value);
  }

  protected sugerir(pregunta: string) {
    this.pregunta.set(pregunta);
    this.enviar();
  }

  async enviar() {
    const pregunta = this.pregunta().trim();
    if (!pregunta || this.ocupado()) return;
    const modo = this.modo();
    const inicio = Date.now();
    this.turnos.update((t) => [...t, { pregunta, modo, estado: 'pensando', inicio, nodos: [], marcas: [] }]);
    const indice = this.turnos().length - 1;
    this.pregunta.set('');
    this.ocupado.set(true);
    this.arrancarReloj();
    this.cancelar = new AbortController();
    try {
      const respuesta = await this.api.preguntar(
        pregunta,
        modo,
        (nodo) => this.actualizar(indice, (t) => ({
          nodos: [...t.nodos, nodo], marcas: [...t.marcas, Date.now() - t.inicio],
        })),
        this.cancelar.signal,
      );
      const filas = respuesta.no_lo_tengo ? [] : maquetar(analizar(respuesta.respuesta, respuesta.citas), respuesta.citas);
      this.actualizar(indice, () => ({ estado: 'hecho', respuesta, filas }));
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
      this.pararReloj();
      this.cancelar = undefined;
    }
  }

  protected alternarTema() {
    this.tema.set(cambiarTema(this.tema()));
  }

  protected detener() {
    this.cancelar?.abort();
  }

  private arrancarReloj() {
    this.ahora.set(Date.now());
    this.reloj = setInterval(() => this.ahora.set(Date.now()), 250);
  }

  private pararReloj() {
    clearInterval(this.reloj);
    this.reloj = undefined;
  }

  private actualizar(indice: number, cambio: (t: Turno) => Partial<Turno>) {
    this.turnos.update((lista) => lista.map((t, i) => (i === indice ? { ...t, ...cambio(t) } : t)));
  }
}
