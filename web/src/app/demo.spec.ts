import { TestBed } from '@angular/core/testing';
import { FETCH } from './api.service';
import { ErrorApi, Respuesta } from './api.types';
import { Grabaciones, ReproductorDemo } from './demo';
import { SUGERENCIAS_DEMO } from './chat/chat';
import grabadas from '../../demo/grabaciones.json';

const RESPUESTA = { respuesta: 'Usa git stash [1].', citas: [] } as unknown as Respuesta;

const DATOS: Grabaciones = {
  grabado: '2026-11-23T16:23:07+01:00',
  llm: { proveedor: 'ollama', modelo: 'qwen2.5:14b' },
  fragmentos: 610,
  grabaciones: [{
    pregunta: '¿Para qué sirve git stash?',
    modo: 'grafo',
    nodos: [{ nodo: 'decidir', ms: 500 }, { nodo: 'recuperar', ms: 9000 }],
    ms_total: 12000,
    respuesta: RESPUESTA,
  }],
};

function montar(fetchFalso: typeof fetch) {
  TestBed.configureTestingModule({ providers: [{ provide: FETCH, useValue: fetchFalso }] });
  return TestBed.inject(ReproductorDemo);
}

const fetchOk = (async () => new Response(JSON.stringify(DATOS))) as unknown as typeof fetch;

describe('ReproductorDemo', () => {
  afterEach(() => vi.useRealTimers());

  it('salud() da los datos de la grabación y no dice que el LLM esté comprobado', async () => {
    const salud = await montar(fetchOk).salud();
    expect(salud.llm.modelo).toBe('qwen2.5:14b');
    expect(salud.coleccion.fragmentos).toBe(610);
    expect(salud.llm.comprobado).toBe(false);
  });

  it('reproduce los nodos y la respuesta con los mismos retrasos que la grabación', async () => {
    vi.useFakeTimers();
    const reproductor = montar(fetchOk);
    await reproductor.grabaciones();
    const nodos: string[] = [];
    let respuesta: Respuesta | undefined;
    reproductor.preguntar('¿Para qué sirve git stash?', 'grafo', (n) => nodos.push(n)).then((r) => (respuesta = r));
    await vi.advanceTimersByTimeAsync(499);
    expect(nodos).toEqual([]);
    await vi.advanceTimersByTimeAsync(1);
    expect(nodos).toEqual(['decidir']);
    await vi.advanceTimersByTimeAsync(8500);
    expect(nodos).toEqual(['decidir', 'recuperar']);
    expect(respuesta).toBeUndefined();
    await vi.advanceTimersByTimeAsync(3000);
    expect(respuesta).toEqual(RESPUESTA);
  });

  it('una pregunta o un modo que no están grabados dan un error, no una respuesta', async () => {
    const reproductor = montar(fetchOk);
    await expect(reproductor.preguntar('¿Qué es un Pod?', 'grafo', () => {})).rejects.toBeInstanceOf(ErrorApi);
    await expect(reproductor.preguntar('¿Para qué sirve git stash?', 'directo', () => {})).rejects.toThrow(
      'En la demo solo se pueden hacer las preguntas grabadas.',
    );
  });

  it('se puede cancelar: no llegan más nodos y rechaza con AbortError', async () => {
    vi.useFakeTimers();
    const reproductor = montar(fetchOk);
    await reproductor.grabaciones();
    const control = new AbortController();
    const nodos: string[] = [];
    const promesa = reproductor.preguntar('¿Para qué sirve git stash?', 'grafo', (n) => nodos.push(n), control.signal);
    const resultado = promesa.catch((e) => e);
    await vi.advanceTimersByTimeAsync(600);
    control.abort();
    await vi.advanceTimersByTimeAsync(20000);
    expect(nodos).toEqual(['decidir']);
    expect((await resultado).name).toBe('AbortError');
  });

  it('si no carga el fichero, da un error y deja reintentar', async () => {
    let intentos = 0;
    const fetchFalso = (async () => (++intentos === 1 ? new Response('', { status: 404 }) : new Response(JSON.stringify(DATOS)))) as unknown as typeof fetch;
    const reproductor = montar(fetchFalso);
    await expect(reproductor.grabaciones()).rejects.toBeInstanceOf(ErrorApi);
    expect((await reproductor.grabaciones()).fragmentos).toBe(610);
  });
});

describe('demo/grabaciones.json (lo que genera scripts/grabar_demo.py)', () => {
  const datos = grabadas as unknown as Grabaciones;

  it('tiene cada pregunta de la demo en los dos modos', () => {
    for (const s of SUGERENCIAS_DEMO) {
      for (const modo of ['grafo', 'directo']) {
        expect(datos.grabaciones.some((g) => g.pregunta === s.pregunta && g.modo === modo), `${s.pregunta} (${modo})`).toBe(true);
      }
    }
  });

  it('los nodos van en orden y la respuesta llega después del último', () => {
    for (const g of datos.grabaciones) {
      const ms = g.nodos.map((n) => n.ms);
      expect(ms).toEqual([...ms].sort((a, b) => a - b));
      expect(g.ms_total).toBeGreaterThanOrEqual(ms.at(-1) ?? 0);
    }
  });

  it('no cita el documento restringido', () => {
    const citados = datos.grabaciones.flatMap((g) => g.respuesta.citas.map((c) => c.documento));
    expect(citados.length).toBeGreaterThan(0);
    expect(citados).not.toContain('06-k8s-secrets.md');
  });
});
