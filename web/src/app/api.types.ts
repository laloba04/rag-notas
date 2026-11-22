// Tipos de la API (api.py). Se mantienen a mano: son pocos y así se ve qué espera el frontend.

export type Modo = 'grafo' | 'directo';

export interface Fragmento {
  numero: number; // el [n] con el que lo cita la respuesta
  documento: string;
  seccion: string;
  url: string;
  puntuacion: number;
  texto: string; // contenido NO confiable: se pinta siempre como texto
}

export interface Traza {
  ruta: string[];
  reintentos: number;
  pregunta_reescrita: string | null;
  latencias: { nodo: string; s: number }[];
  error: string | null;
}

export interface Respuesta {
  respuesta: string;
  no_lo_tengo: boolean; // la respuesta es exactamente "No lo tengo en la documentación."
  salida_bloqueada: boolean; // la validación de salida la cambió por esa frase
  modo: Modo;
  citas: Fragmento[];
  traza: Traza | null;
  segundos: number;
}

export interface Salud {
  estado: 'ok' | 'degradado';
  version: string;
  coleccion: { ok: boolean; fragmentos: number };
  llm: { proveedor: string; modelo: string | null; ok: boolean; comprobado: boolean };
  modelos_cargados: boolean;
}

/** Error con el mensaje que manda la API ({"error", "tipo"}), listo para enseñarlo. */
export class ErrorApi extends Error {
  constructor(
    message: string,
    readonly tipo: string,
    readonly estado: number,
  ) {
    super(message);
  }
}
