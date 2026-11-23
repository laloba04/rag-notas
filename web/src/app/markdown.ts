import { Fragmento } from './api.types';

/**
 * Markdown seguro para las respuestas del LLM. Solo entiende un subconjunto: párrafos, negrita
 * (**así**), código en línea (`así`), bloques de código (```) y listas (- … / 1. …). Devuelve una
 * estructura de datos que la plantilla pinta con interpolación de Angular (escapada); nunca HTML.
 *
 * Imágenes, enlaces, HTML y todo lo demás se quedan como texto literal: la respuesta sale de
 * documentos no confiables, y una imagen o un enlace podrían sacar datos a otro servidor.
 * Solo es cita un [n] que corresponde a un fragmento que ha mandado la API.
 */

export type EnLinea =
  | { t: 'texto'; v: string }
  | { t: 'codigo'; v: string }
  | { t: 'cita'; cita: Fragmento }
  | { t: 'negrita'; hijos: EnLinea[] };

export type Bloque =
  | { t: 'parrafo'; partes: EnLinea[] }
  | { t: 'codigo'; v: string }
  | { t: 'lista'; ordenada: boolean; inicio: number; elementos: EnLinea[][] };

// Solo es valla una línea con ``` y, como mucho, el lenguaje: un ```x``` dentro de una frase no
// abre un bloque (si no, se tragaría el resto de la respuesta, citas incluidas).
const VALLA = /^\s*```[\w+-]*\s*$/;
const SEPARADOR = /^\s*([-*_])(\s*\1){2,}\s*$/; // --- o * * *: se trata como fin de párrafo
// Las listas anidadas se aplanan: todas las viñetas quedan al mismo nivel.
const VINETA = /^\s*[-*+]\s+(.*)$/;
const NUMERO = /^\s*(\d{1,3})[.)]\s+(.*)$/;

export function analizar(texto: string, citas: Fragmento[]): Bloque[] {
  const porNumero = new Map(citas.map((c) => [c.numero, c]));
  const lineas = texto.replace(/\r\n?/g, '\n').split('\n');
  const bloques: Bloque[] = [];
  let parrafo: string[] = [];

  const cerrarParrafo = () => {
    if (parrafo.length) bloques.push({ t: 'parrafo', partes: enLinea(parrafo.join('\n'), porNumero) });
    parrafo = [];
  };

  for (let i = 0; i < lineas.length; i++) {
    const linea = lineas[i];

    if (VALLA.test(linea)) {
      // Bloque de código hasta la valla de cierre (o hasta el final, si el modelo no la cierra).
      cerrarParrafo();
      const codigo: string[] = [];
      while (++i < lineas.length && !VALLA.test(lineas[i])) codigo.push(lineas[i]);
      bloques.push({ t: 'codigo', v: codigo.join('\n') });
      continue;
    }

    if (SEPARADOR.test(linea)) {
      cerrarParrafo();
      continue;
    }

    const vineta = VINETA.exec(linea);
    const numero = vineta ? null : NUMERO.exec(linea);
    if (vineta || numero) {
      cerrarParrafo();
      const ordenada = !!numero;
      const ultimo = bloques.at(-1);
      const lista: Extract<Bloque, { t: 'lista' }> =
        ultimo?.t === 'lista' && ultimo.ordenada === ordenada && lineas[i - 1]?.trim() !== ''
          ? ultimo
          // Una lista numerada cortada (por una línea en blanco o un bloque de código) sigue con el
          // número que escribió el modelo, no vuelve a empezar en 1.
          : { t: 'lista', ordenada, inicio: numero ? Number(numero[1]) : 1, elementos: [] };
      if (lista !== ultimo) bloques.push(lista);
      lista.elementos.push(enLinea(vineta ? vineta[1] : numero![2], porNumero));
      continue;
    }

    if (linea.trim() === '') {
      cerrarParrafo();
      continue;
    }

    // Una línea suelta justo después de un elemento de lista lo continúa.
    const ultimo = bloques.at(-1);
    if (!parrafo.length && ultimo?.t === 'lista' && lineas[i - 1]?.trim() !== '' && /^\s/.test(linea)) {
      ultimo.elementos[ultimo.elementos.length - 1].push({ t: 'texto', v: ' ' + linea.trim() });
      continue;
    }
    parrafo.push(linea);
  }
  cerrarParrafo();
  return bloques;
}

const EN_LINEA = /`([^`\n]+)`|\*\*(?=\S)([^*]+?)(?<=\S)\*\*|\[(\d{1,3})\]/g;

function enLinea(texto: string, porNumero: Map<number, Fragmento>, dentroDeNegrita = false): EnLinea[] {
  const partes: EnLinea[] = [];
  let ultimo = 0;
  const texto_ = (v: string) => {
    if (!v) return;
    const previa = partes.at(-1);
    if (previa?.t === 'texto') previa.v += v;
    else partes.push({ t: 'texto', v });
  };
  for (const m of texto.matchAll(EN_LINEA)) {
    const [entero, codigo, negrita, numero] = m;
    let parte: EnLinea | null = null;
    if (codigo !== undefined) parte = { t: 'codigo', v: codigo };
    else if (negrita !== undefined && !dentroDeNegrita)
      parte = { t: 'negrita', hijos: enLinea(negrita, porNumero, true) };
    else if (numero !== undefined) {
      const cita = porNumero.get(Number(numero));
      if (cita) parte = { t: 'cita', cita };
    }
    if (!parte) continue; // se queda como texto (p. ej., un [7] que no es ninguna cita)
    texto_(texto.slice(ultimo, m.index));
    partes.push(parte);
    ultimo = m.index + entero.length;
  }
  texto_(texto.slice(ultimo));
  return partes;
}

/** Las citas de un bloque, en orden y sin repetir. */
export function citasDe(bloque: Bloque): Fragmento[] {
  const vistas = new Map<number, Fragmento>();
  const recorrer = (partes: EnLinea[]) => {
    for (const p of partes) {
      if (p.t === 'cita') vistas.set(p.cita.numero, p.cita);
      else if (p.t === 'negrita') recorrer(p.hijos);
    }
  };
  if (bloque.t === 'parrafo') recorrer(bloque.partes);
  else if (bloque.t === 'lista') bloque.elementos.forEach(recorrer);
  return [...vistas.values()];
}

/** Un párrafo que solo tiene citas (p. ej., un "[1]" suelto al final de la respuesta). */
export function soloCitas(bloque: Bloque): boolean {
  return (
    bloque.t === 'parrafo' &&
    bloque.partes.some((p) => p.t === 'cita') &&
    bloque.partes.every((p) => p.t === 'cita' || (p.t === 'texto' && /^[\s.,;:]*$/.test(p.v)))
  );
}

export interface Fila {
  bloque: Bloque;
  notas: Fragmento[]; // notas al margen a la altura de este bloque (cada cita, una sola vez)
  hasta: number; // la nota ocupa desde esta fila hasta antes de la fila `hasta` (rejilla CSS)
}

/**
 * Coloca las notas al margen. Cada cita se anota junto al primer bloque que la menciona. Las de un
 * párrafo que solo tiene citas se aplican a toda la respuesta, así que suben a la primera fila.
 * Las citas de la API que no aparecen en el texto, también a la primera fila.
 * Cada grupo de notas ocupa las filas hasta el siguiente grupo, para no estirar su párrafo.
 */
export function maquetar(bloques: Bloque[], citas: Fragmento[] = []): Fila[] {
  const filas: Fila[] = bloques.map((bloque) => ({ bloque, notas: [], hasta: 0 }));
  const anotadas = new Set<number>();
  const anotar = (fila: Fila, citas: Fragmento[]) => {
    for (const c of citas) if (!anotadas.has(c.numero)) {
      anotadas.add(c.numero);
      fila.notas.push(c);
    }
  };
  filas.forEach((f) => !soloCitas(f.bloque) && anotar(f, citasDe(f.bloque)));
  filas.forEach((f) => soloCitas(f.bloque) && anotar(filas[0], citasDe(f.bloque)));
  // Una cita que la API manda pero que no se ve en el texto (p. ej., escrita dentro de un bloque
  // de código) también tiene su nota: la respuesta siempre enseña sus fuentes.
  if (filas.length) anotar(filas[0], citas);
  let siguiente = filas.length + 1;
  for (let i = filas.length - 1; i >= 0; i--) {
    if (!filas[i].notas.length) continue;
    filas[i].hasta = siguiente;
    siguiente = i + 1;
  }
  return filas;
}
