import { TestBed } from '@angular/core/testing';
import { ApiService, FETCH } from './api.service';
import { ErrorApi, Respuesta } from './api.types';

const RESPUESTA: Respuesta = {
  respuesta: 'Usa git stash [1].',
  no_lo_tengo: false,
  salida_bloqueada: false,
  modo: 'grafo',
  citas: [],
  traza: null,
  segundos: 1,
};

/** Respuesta con un cuerpo que llega en estos trozos (como por la red, partidos donde caiga). */
function enTrozos(trozos: string[], estado = 200): Response {
  const cuerpo = new ReadableStream<Uint8Array>({
    start(c) {
      for (const t of trozos) c.enqueue(new TextEncoder().encode(t));
      c.close();
    },
  });
  return new Response(cuerpo, { status: estado, headers: { 'Content-Type': 'text/event-stream' } });
}

function servicioCon(fetchFalso: (...a: any[]) => Promise<Response>) {
  TestBed.configureTestingModule({ providers: [{ provide: FETCH, useValue: fetchFalso }] });
  return TestBed.inject(ApiService);
}

describe('ApiService.preguntar', () => {
  it('avisa de cada nodo y devuelve la respuesta, aunque un evento llegue partido', async () => {
    const sse =
      'event: nodo\ndata: {"nodo": "decidir"}\n\n' +
      'event: nodo\ndata: {"nodo": "recuperar"}\n\n' +
      `event: respuesta\ndata: ${JSON.stringify(RESPUESTA)}\n\n`;
    const corte = sse.indexOf('recu'); // parte el segundo evento por la mitad
    const llamadas: any[] = [];
    const api = servicioCon(async (...a) => {
      llamadas.push(a);
      return enTrozos([sse.slice(0, corte), sse.slice(corte)]);
    });
    const nodos: string[] = [];
    const r = await api.preguntar('¿Cómo guardo cambios?', 'grafo', (n) => nodos.push(n));

    expect(nodos).toEqual(['decidir', 'recuperar']);
    expect(r).toEqual(RESPUESTA);
    const [url, opciones] = llamadas[0];
    expect(url).toBe('/api/preguntar');
    expect(opciones.headers.Accept).toBe('text/event-stream');
    expect(JSON.parse(opciones.body)).toEqual({ pregunta: '¿Cómo guardo cambios?', modo: 'grafo' });
  });

  it('un evento de error se convierte en ErrorApi con el mensaje de la API', async () => {
    const api = servicioCon(async () =>
      enTrozos(['event: nodo\ndata: {"nodo": "decidir"}\n\n',
        'event: error\ndata: {"error": "Bloqueado: no se devuelve nada.", "tipo": "bloqueado"}\n\n']),
    );
    const error = await api.preguntar('x', 'grafo', () => {}).catch((e) => e);
    expect(error).toBeInstanceOf(ErrorApi);
    expect(error.message).toBe('Bloqueado: no se devuelve nada.');
    expect(error.tipo).toBe('bloqueado');
  });

  it('una respuesta HTTP de error usa el mensaje del cuerpo', async () => {
    const api = servicioCon(async () =>
      new Response(JSON.stringify({ error: 'Demasiadas peticiones.', tipo: 'limite' }), { status: 429 }),
    );
    const error = await api.preguntar('x', 'grafo', () => {}).catch((e) => e);
    expect(error).toMatchObject({ message: 'Demasiadas peticiones.', tipo: 'limite', estado: 429 });
  });

  it('un error sin cuerpo JSON da un mensaje genérico, no el HTML del proxy', async () => {
    const api = servicioCon(async () => new Response('<html>502 Bad Gateway</html>', { status: 502 }));
    const error = await api.preguntar('x', 'grafo', () => {}).catch((e) => e);
    expect(error.message).toBe('El servidor ha respondido con un error (502).');
    expect(error.message).not.toContain('<html>');
  });

  it('si la conexión se corta sin respuesta, es un error', async () => {
    const api = servicioCon(async () => enTrozos(['event: nodo\ndata: {"nodo": "decidir"}\n\n']));
    const error = await api.preguntar('x', 'grafo', () => {}).catch((e) => e);
    expect(error.message).toContain('se cortó');
  });

  it('pasa la señal de cancelación a fetch', async () => {
    let senal: AbortSignal | undefined;
    const api = servicioCon(async (_u, opciones) => {
      senal = opciones.signal;
      return enTrozos([`event: respuesta\ndata: ${JSON.stringify(RESPUESTA)}\n\n`]);
    });
    const control = new AbortController();
    await api.preguntar('x', 'directo', () => {}, control.signal);
    expect(senal).toBe(control.signal);
  });
});

describe('ApiService.salud', () => {
  it('con 503 (sin colección) devuelve el cuerpo igualmente', async () => {
    const cuerpo = { estado: 'degradado', coleccion: { ok: false, fragmentos: 0 } };
    const api = servicioCon(async () => new Response(JSON.stringify(cuerpo), { status: 503 }));
    expect(await api.salud()).toEqual(cuerpo);
  });

  it('con el 503 de nginx (la API no responde) lanza ErrorApi de tipo "arrancando"', async () => {
    const cuerpo = { error: 'La API todavía no responde.', tipo: 'arrancando' };
    const api = servicioCon(async () => new Response(JSON.stringify(cuerpo), { status: 503 }));
    const error = await api.salud().catch((e) => e);
    expect(error).toBeInstanceOf(ErrorApi);
    expect(error.tipo).toBe('arrancando');
    expect(error.message).toBe('La API todavía no responde.');
  });

  it('un 502 en HTML de un proxy es un error genérico', async () => {
    const api = servicioCon(async () => new Response('<html>502 Bad Gateway</html>', { status: 502 }));
    const error = await api.salud().catch((e) => e);
    expect(error).toBeInstanceOf(ErrorApi);
    expect(error.tipo).toBe('interno');
  });
});
