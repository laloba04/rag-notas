import { TestBed } from '@angular/core/testing';
import { ApiService } from '../api.service';
import { ErrorApi, Fragmento, Respuesta, Salud } from '../api.types';
import { Chat, partirRespuesta } from './chat';

const CITA: Fragmento = {
  numero: 1,
  documento: '14-git-stash-y-clean.md',
  seccion: 'Guardado rápido',
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
  async preguntar(_p: string, _m: string, alPasar: (n: string) => void) {
    alPasar('decidir');
    return this.resultado();
  }
}

async function montar(api = new ApiFalsa()) {
  TestBed.configureTestingModule({ imports: [Chat], providers: [{ provide: ApiService, useValue: api }] });
  const fixture = TestBed.createComponent(Chat);
  await fixture.whenStable();
  const raiz = fixture.nativeElement as HTMLElement;
  async function preguntar(texto: string) {
    const area = raiz.querySelector('textarea')!;
    area.value = texto;
    area.dispatchEvent(new Event('input'));
    await fixture.whenStable();
    raiz.querySelector<HTMLButtonElement>('button[type=submit]')!.click();
    await fixture.whenStable();
  }
  return { fixture, raiz, preguntar, api };
}

describe('partirRespuesta', () => {
  it('solo convierte en cita los [n] que ha mandado la API', () => {
    const partes = partirRespuesta(RESPUESTA);
    expect(partes).toEqual([{ texto: 'Usa git stash ' }, { cita: CITA }, { texto: '. Otro [7] no existe.' }]);
  });
});

describe('Chat', () => {
  it('avisa de que la primera pregunta puede tardar si los modelos no están cargados', async () => {
    const { raiz } = await montar();
    expect(raiz.textContent).toContain('La primera pregunta puede tardar');
  });

  it('enseña la respuesta con citas que abren el fragmento y su documento', async () => {
    const { raiz, preguntar, fixture } = await montar();
    await preguntar('¿Cómo guardo cambios?');
    expect(raiz.querySelector('.turno__respuesta')!.textContent).toContain('Usa git stash');
    const cita = raiz.querySelector<HTMLButtonElement>('button.cita')!;
    expect(raiz.querySelectorAll('button.cita').length).toBe(1);
    cita.click();
    await fixture.whenStable();
    const fragmento = raiz.querySelector('.fragmento')!;
    expect(fragmento.textContent).toContain('14-git-stash-y-clean.md');
    expect(fragmento.querySelector('a')!.getAttribute('href')).toBe(CITA.url);
  });

  it('pinta el fragmento como texto: el HTML que traiga no se interpreta', async () => {
    const { raiz, preguntar, fixture } = await montar();
    await preguntar('x');
    raiz.querySelector<HTMLButtonElement>('button.cita')!.click();
    await fixture.whenStable();
    expect(raiz.querySelector('.fragmento img')).toBeNull();
    expect(raiz.querySelector('.fragmento__texto')!.textContent).toContain('<img src=');
  });

  it('no enlaza URLs que no sean https', async () => {
    const api = new ApiFalsa();
    api.resultado = async () => ({ ...RESPUESTA, citas: [{ ...CITA, url: 'javascript:alert(1)' }] });
    const { raiz, preguntar, fixture } = await montar(api);
    await preguntar('x');
    raiz.querySelector<HTMLButtonElement>('button.cita')!.click();
    await fixture.whenStable();
    expect(raiz.querySelector('.fragmento a')).toBeNull();
  });

  it('"No lo tengo" se enseña como un resultado válido, no como un error', async () => {
    const api = new ApiFalsa();
    api.resultado = async () => ({ ...RESPUESTA, respuesta: 'No lo tengo en la documentación.',
      no_lo_tengo: true, citas: [] });
    const { raiz, preguntar } = await montar(api);
    await preguntar('¿Cuál es la última versión estable de Kubernetes?');
    const caja = raiz.querySelector('.turno__no-lo-tengo')!.textContent!;
    expect(caja).toContain('No lo tengo');
    expect(caja).toContain('no ha encontrado la');
    expect(caja).not.toContain('descartado');
    expect(raiz.querySelector('.turno__error')).toBeNull();
    expect(raiz.querySelector('[role=alert]')).toBeNull();
  });

  it('un error de la API se enseña con su mensaje', async () => {
    const api = new ApiFalsa();
    api.resultado = async () => {
      throw new ErrorApi('No se puede conectar con el LLM.', 'no_disponible', 503);
    };
    const { raiz, preguntar } = await montar(api);
    await preguntar('x');
    expect(raiz.querySelector('.turno__error')!.textContent).toContain('No se puede conectar con el LLM.');
  });

  it('el panel de la traza lista los nodos del grafo con su tiempo', async () => {
    const { raiz, preguntar } = await montar();
    await preguntar('x');
    const pasos = [...raiz.querySelectorAll('.traza li')].map((li) => li.querySelector('code')!.textContent);
    expect(pasos).toEqual(['decidir', 'recuperar', 'evaluar', 'generar']);
    expect(raiz.querySelector('.traza summary')!.textContent).toContain('4 pasos');
    expect(raiz.querySelector('.traza')!.textContent).toContain('9.9 s');
  });

  it('mientras piensa enseña el último nodo y permite cancelar', async () => {
    const api = new ApiFalsa();
    let terminar!: (r: Respuesta) => void;
    api.resultado = () => new Promise((r) => (terminar = r));
    const { raiz, preguntar, fixture } = await montar(api);
    await preguntar('x');
    expect(raiz.querySelector('.turno__pensando')!.textContent).toContain('¿Hace falta buscar?');
    expect(raiz.querySelector('button.boton--secundario')!.textContent).toContain('Cancelar');
    terminar(RESPUESTA);
    await new Promise((r) => setTimeout(r)); // deja que enviar() siga tras la promesa
    await fixture.whenStable();
    expect(raiz.querySelector('.turno__pensando')).toBeNull();
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
    expect(raiz.querySelector('.turno__no-lo-tengo')!.textContent).toContain('descartado');
  });

  it('una respuesta que solo menciona la frase se enseña entera', async () => {
    const api = new ApiFalsa();
    const texto = 'Cache-Control controla la caché [1]. Sobre HTTP/3: No lo tengo en la documentación.';
    api.resultado = async () => ({ ...RESPUESTA, respuesta: texto, no_lo_tengo: false });
    const { raiz, preguntar } = await montar(api);
    await preguntar('x');
    expect(raiz.querySelector('.turno__no-lo-tengo')).toBeNull();
    expect(raiz.querySelector('.turno__respuesta')!.textContent).toContain('Cache-Control controla la caché');
  });

  it('las citas se copian con corchetes', async () => {
    const { raiz, preguntar } = await montar();
    await preguntar('x');
    expect(raiz.querySelector('button.cita')!.textContent).toBe('[1]');
  });
});
