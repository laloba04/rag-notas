import { TestBed } from '@angular/core/testing';
import { ApiService } from '../api.service';
import { ErrorApi, Fragmento, Respuesta, Salud } from '../api.types';
import { Chat, ESPERA_ARRANQUE_MS, Turno, cascada, corpus } from './chat';

const CITA: Fragmento = {
  numero: 1,
  documento: '14-git-stash-y-clean.md',
  seccion: 'Stash > Guardado rápido',
  url: 'https://github.com/progit/progit2-es',
  puntuacion: 0.9,
  texto: 'git stash guarda los cambios <img src="https://atacante.example/x">',
};

const RESPUESTA: Respuesta = {
  respuesta: 'Usa git stash [1]. Otro [7] no existe.',
  no_lo_tengo: false,
  salida_bloqueada: false,
  modo: 'grafo',
  citas: [CITA],
  traza: {
    ruta: ['decidir', 'recuperar', 'evaluar', 'generar'],
    reintentos: 0,
    pregunta_reescrita: null,
    latencias: [{ nodo: 'decidir', s: 0.5 }, { nodo: 'recuperar', s: 9.9 },
      { nodo: 'evaluar', s: 0 }, { nodo: 'generar', s: 1.8 }],
    error: null,
  },
  segundos: 12.2,
};

const SALUD: Salud = {
  estado: 'ok',
  version: '1.0.0',
  coleccion: { ok: true, fragmentos: 610 },
  llm: { proveedor: 'ollama', modelo: 'qwen2.5:14b', ok: true, comprobado: true },
  modelos_cargados: false,
};

class ApiFalsa {
  salud = async () => SALUD;
  resultado: () => Promise<Respuesta> = async () => RESPUESTA;
  preguntas: string[] = [];
  async preguntar(p: string, _m: string, alPasar: (n: string) => void) {
    this.preguntas.push(p);
    alPasar('decidir');
    return this.resultado();
  }
}

async function montar(api = new ApiFalsa()) {
  TestBed.configureTestingModule({ imports: [Chat], providers: [
    { provide: ApiService, useValue: api },
    { provide: ESPERA_ARRANQUE_MS, useValue: 20 },
  ] });
  const fixture = TestBed.createComponent(Chat);
  await fixture.whenStable();
  const raiz = fixture.nativeElement as HTMLElement;
  async function preguntar(texto: string) {
    const campo = raiz.querySelector<HTMLInputElement>('input#pregunta')!;
    campo.value = texto;
    campo.dispatchEvent(new Event('input'));
    await fixture.whenStable();
    raiz.querySelector<HTMLButtonElement>('button[type=submit]')!.click();
    await fixture.whenStable();
  }
  return { fixture, raiz, preguntar, api };
}

describe('Chat', () => {
  it('avisa de que la primera pregunta puede tardar si los modelos no están cargados', async () => {
    const { raiz } = await montar();
    expect(raiz.textContent).toContain('La primera pregunta puede tardar');
    expect(raiz.querySelector('header')!.textContent).toContain('qwen2.5:14b · 610 fragmentos');
  });

  it('enseña la respuesta con llamadas [n] que llevan a su nota al margen', async () => {
    const { raiz, preguntar } = await montar();
    await preguntar('¿Cómo guardo cambios?');
    expect(raiz.querySelector('.respuesta')!.textContent).toContain('Usa git stash');
    const llamadas = raiz.querySelectorAll<HTMLAnchorElement>('a.llamada');
    expect(llamadas.length).toBe(1); // el [7] no es ninguna cita
    expect(llamadas[0].textContent).toBe('[1]'); // se copia con corchetes
    const nota = raiz.querySelector(llamadas[0].getAttribute('href')!)!;
    expect(nota.textContent).toContain('14-git-stash-y-clean.md');
    expect(nota.textContent).toContain('Pro Git');
    expect(nota.textContent).toContain('Guardado rápido');
    expect(nota.querySelector('a')!.getAttribute('href')).toBe(CITA.url);
  });

  it('pinta el fragmento como texto: el HTML que traiga no se interpreta', async () => {
    const { raiz, preguntar } = await montar();
    await preguntar('x');
    expect(raiz.querySelector('.nota img')).toBeNull();
    expect(raiz.querySelector('.fragmento')!.textContent).toContain('<img src=');
  });

  it('pinta el Markdown seguro y deja como texto imágenes, enlaces y HTML', async () => {
    const api = new ApiFalsa();
    api.resultado = async () => ({ ...RESPUESTA, respuesta: [
      'Usa **git stash** con `-u` [1].',
      '```', '$ git stash pop', '```',
      '- uno', '- dos',
      '![x](https://atacante.example/fuga) [aquí](https://atacante.example) <img src=x onerror=alert(1)>',
    ].join('\n') });
    const { raiz, preguntar } = await montar(api);
    await preguntar('x');
    const respuesta = raiz.querySelector('.respuesta')!;
    expect(respuesta.querySelector('strong')!.textContent).toBe('git stash');
    expect(respuesta.querySelector('p code')!.textContent).toBe('-u');
    expect(respuesta.querySelector('pre code')!.textContent).toBe('$ git stash pop');
    expect(respuesta.querySelectorAll('ul li').length).toBe(2);
    // Control negativo: ninguna imagen, y los únicos enlaces son la llamada y el original de la nota.
    expect(respuesta.querySelector('img')).toBeNull();
    const enlaces = [...respuesta.querySelectorAll('a')].map((a) => a.getAttribute('href'));
    expect(enlaces).toEqual(['#nota-1-1', CITA.url]);
    expect(respuesta.textContent).toContain('![x](https://atacante.example/fuga)');
    expect(respuesta.textContent).toContain('<img src=x onerror=alert(1)>');
  });

  it('un [1] suelto al final se anota arriba, ocupando las filas de toda la respuesta', async () => {
    const api = new ApiFalsa();
    api.resultado = async () => ({ ...RESPUESTA, respuesta: 'Uno.\n\n```\ncódigo\n```\n\n[1]' });
    const { raiz, preguntar } = await montar(api);
    await preguntar('x');
    const margen = raiz.querySelector<HTMLElement>('.respuesta .margen')!;
    expect(margen.previousElementSibling!.textContent).toContain('Uno.');
    expect(margen.style.getPropertyValue('--filas')).toBe('1 / 4');
  });

  it('no enlaza URLs que no sean https', async () => {
    const api = new ApiFalsa();
    api.resultado = async () => ({ ...RESPUESTA, citas: [{ ...CITA, url: 'javascript:alert(1)' }] });
    const { raiz, preguntar } = await montar(api);
    await preguntar('x');
    expect(raiz.querySelector('.nota a')).toBeNull();
  });

  it('"No lo tengo" se enseña como un resultado válido, no como un error', async () => {
    const api = new ApiFalsa();
    api.resultado = async () => ({ ...RESPUESTA, respuesta: 'No lo tengo en la documentación.',
      no_lo_tengo: true, citas: [] });
    const { raiz, preguntar } = await montar(api);
    await preguntar('¿Cuál es la última versión estable de Kubernetes?');
    const caja = raiz.querySelector('.no-lo-tengo')!.textContent!;
    expect(caja).toContain('No lo tengo');
    expect(caja).toContain('no ha encontrado la');
    expect(caja).not.toContain('descartado');
    expect(raiz.querySelector('.error-turno')).toBeNull();
    expect(raiz.querySelector('[role=alert]')).toBeNull();
  });

  it('si el grafo reformuló la pregunta, lo cuenta', async () => {
    const api = new ApiFalsa();
    api.resultado = async () => ({ ...RESPUESTA, respuesta: 'No lo tengo en la documentación.',
      no_lo_tengo: true, citas: [], traza: { ...RESPUESTA.traza!, reintentos: 1,
        pregunta_reescrita: '¿Qué es el nodo maestro?' } });
    const { raiz, preguntar } = await montar(api);
    await preguntar('x');
    expect(raiz.querySelector('.no-lo-tengo .nota')!.textContent).toContain('¿Qué es el nodo maestro?');
    expect(raiz.querySelector('.traza')!.textContent).toContain('1 reintento');
  });

  it('un error de la API se enseña con su mensaje', async () => {
    const api = new ApiFalsa();
    api.resultado = async () => {
      throw new ErrorApi('No se puede conectar con el LLM.', 'no_disponible', 503);
    };
    const { raiz, preguntar } = await montar(api);
    await preguntar('x');
    expect(raiz.querySelector('.error-turno')!.textContent).toContain('No se puede conectar con el LLM.');
  });

  it('la traza en cascada lista los nodos del grafo con su tiempo', async () => {
    const { raiz, preguntar } = await montar();
    await preguntar('x');
    const pasos = [...raiz.querySelectorAll('.traza .paso')].map((p) => p.textContent!.trim());
    expect(pasos).toEqual(['decidir', 'recuperar', 'evaluar', 'generar']);
    expect(raiz.querySelector('.traza')!.textContent).toContain('4 pasos');
    expect(raiz.querySelector('.traza')!.textContent).toContain('9,9 s');
    expect(raiz.querySelector('.meta')!.textContent).toContain('12,20 s');
  });

  it('mientras piensa enseña el último nodo y permite cancelar', async () => {
    const api = new ApiFalsa();
    let terminar!: (r: Respuesta) => void;
    api.resultado = () => new Promise((r) => (terminar = r));
    const { raiz, preguntar, fixture } = await montar(api);
    await preguntar('x');
    expect(raiz.querySelector('.pensando')!.textContent).toContain('¿Hace falta buscar?');
    expect(raiz.querySelector('.pensando .cascada__barra--activa')).not.toBeNull();
    expect(raiz.querySelector('button.cancelar')!.textContent).toContain('Cancelar');
    terminar(RESPUESTA);
    await new Promise((r) => setTimeout(r)); // deja que enviar() siga tras la promesa
    await fixture.whenStable();
    expect(raiz.querySelector('.pensando')).toBeNull();
  });

  it('anuncia a los lectores de pantalla solo una frase corta por consulta', async () => {
    const { raiz, preguntar } = await montar();
    expect(raiz.querySelector('main[aria-live]')).toBeNull();
    await preguntar('x');
    expect(raiz.querySelector('[role=status][aria-live=polite]')!.textContent!.trim())
      .toBe('Consulta 1 respondida en 12,2 segundos, con 1 fuente.');
  });

  it('la consulta más reciente va arriba', async () => {
    const { raiz, preguntar } = await montar();
    await preguntar('primera');
    await preguntar('segunda');
    const titulares = [...raiz.querySelectorAll('h2.titular')].map((h) => h.textContent);
    expect(titulares).toEqual(['segunda', 'primera']);
    expect(raiz.querySelector('.meta')!.textContent).toContain('Consulta 02');
  });

  it('la portada propone preguntas y al pulsar una la envía', async () => {
    const { raiz, api, fixture } = await montar();
    const sugerencias = raiz.querySelectorAll<HTMLButtonElement>('.sugerencia');
    expect(sugerencias.length).toBe(3);
    sugerencias[0].click();
    await fixture.whenStable();
    expect(api.preguntas).toEqual(['¿Para qué sirve git stash?']);
    expect(raiz.querySelector('.inicio')).toBeNull();
  });

  it('si la API está arrancando, lo avisa, vuelve a comprobar sola y quita el aviso al responder', async () => {
    const api = new ApiFalsa();
    let intentos = 0;
    api.salud = async () => {
      if (++intentos < 3) throw new ErrorApi('La API todavía no responde.', 'arrancando', 503);
      return SALUD;
    };
    const { raiz, fixture } = await montar(api);
    expect(raiz.querySelector('.aviso--arranque')!.textContent).toContain('La API todavía no responde');
    expect(raiz.querySelector('[role=alert]')).toBeNull(); // no es "sin conexión"
    expect(raiz.querySelector<HTMLButtonElement>('button[type=submit]')!.disabled).toBe(true);
    expect(raiz.querySelector<HTMLButtonElement>('.sugerencia')!.disabled).toBe(true);
    await new Promise((r) => setTimeout(r, 120));
    await fixture.whenStable();
    expect(intentos).toBe(3);
    expect(raiz.querySelector('.aviso--arranque')).toBeNull();
    expect(raiz.querySelector('header')!.textContent).toContain('610 fragmentos');
  });

  it('sin conexión con la API lo avisa al cargar', async () => {
    const api = new ApiFalsa();
    api.salud = async () => {
      throw new TypeError('Failed to fetch');
    };
    const { raiz } = await montar(api);
    expect(raiz.querySelector('[role=alert]')!.textContent).toContain('No se puede conectar con la API');
  });
});

describe('Chat: casos de "No lo tengo"', () => {
  it('si la validación descartó la respuesta, lo dice en vez de culpar al corpus', async () => {
    const api = new ApiFalsa();
    api.resultado = async () => ({ ...RESPUESTA, respuesta: 'No lo tengo en la documentación.',
      no_lo_tengo: true, salida_bloqueada: true, citas: [] });
    const { raiz, preguntar } = await montar(api);
    await preguntar('x');
    expect(raiz.querySelector('.no-lo-tengo')!.textContent).toContain('descartado');
    expect(raiz.querySelector('[role=status][aria-live]')!.textContent).toContain('descartada por la validación de salida');
  });

  it('una respuesta que solo menciona la frase se enseña entera', async () => {
    const api = new ApiFalsa();
    const texto = 'Cache-Control controla la caché [1]. Sobre HTTP/3: No lo tengo en la documentación.';
    api.resultado = async () => ({ ...RESPUESTA, respuesta: texto, no_lo_tengo: false });
    const { raiz, preguntar } = await montar(api);
    await preguntar('x');
    expect(raiz.querySelector('.no-lo-tengo')).toBeNull();
    expect(raiz.querySelector('.respuesta')!.textContent).toContain('Cache-Control controla la caché');
  });
});

describe('cascada', () => {
  const base: Turno = { pregunta: 'x', modo: 'grafo', estado: 'hecho', inicio: 0, nodos: [], marcas: [] };

  it('con la respuesta: barras seguidas, en % de la suma de latencias', () => {
    const barras = cascada({ ...base, respuesta: { ...RESPUESTA, traza: { ...RESPUESTA.traza!, latencias: [
      { nodo: 'decidir', s: 1 }, { nodo: 'recuperar', s: 2 }, { nodo: 'reescribir', s: 1 }] } } }, 0);
    expect(barras.map((b) => [b.nodo, b.inicio, b.ancho, b.tipo])).toEqual([
      ['decidir', 0, 25, 'normal'], ['recuperar', 25, 50, 'normal'], ['reescribir', 75, 25, 'reintento']]);
  });

  it('mientras piensa: los nodos que han llegado y una barra activa hasta ahora', () => {
    const barras = cascada({ ...base, estado: 'pensando', inicio: 1000, nodos: ['decidir'], marcas: [500] }, 3000);
    expect(barras.map((b) => [b.nodo, b.inicio, b.ancho, b.s, b.tipo])).toEqual([
      ['decidir', 0, 25, 0.5, 'normal'], ['…', 25, 75, null, 'activa']]);
  });
});

describe('corpus', () => {
  it('sale del nombre del fichero', () => {
    expect(corpus('03-k8s-pods.md').nombre).toBe('Kubernetes');
    expect(corpus('12-git-ramas-y-fusiones.md').nombre).toBe('Pro Git');
    expect(corpus('19-http-cache.md').nombre).toBe('MDN HTTP');
    expect(corpus('otro.pdf').clase).toBe('otro');
  });
});
