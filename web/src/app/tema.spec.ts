import { aplicarTema, cambiarTema, leerTema } from './tema';

class AlmacenFalso {
  datos = new Map<string, string>();
  getItem = (k: string) => this.datos.get(k) ?? null;
  setItem = (k: string, v: string) => void this.datos.set(k, v);
  removeItem = (k: string) => void this.datos.delete(k);
}

describe('tema', () => {
  afterEach(() => aplicarTema('sistema'));

  it('por defecto, y con valores raros, sigue el sistema', () => {
    const a = new AlmacenFalso();
    expect(leerTema(a)).toBe('sistema');
    a.setItem('rag-notas.tema', '"><script>');
    expect(leerTema(a)).toBe('sistema');
  });

  it('alterna sistema → claro → oscuro → sistema, lo aplica en <html> y lo guarda', () => {
    const a = new AlmacenFalso();
    const raiz = document.documentElement;
    expect(cambiarTema('sistema', a)).toBe('claro');
    expect(raiz.dataset['theme']).toBe('light');
    expect(leerTema(a)).toBe('claro');
    expect(cambiarTema('claro', a)).toBe('oscuro');
    expect(raiz.dataset['theme']).toBe('dark');
    expect(cambiarTema('oscuro', a)).toBe('sistema');
    expect(raiz.dataset['theme']).toBeUndefined();
    expect(a.datos.size).toBe(0);
  });

  it('si el almacenamiento falla, cambia igual para esta visita', () => {
    const roto = {
      getItem: () => { throw new Error('bloqueado'); },
      setItem: () => { throw new Error('bloqueado'); },
      removeItem: () => { throw new Error('bloqueado'); },
    };
    expect(leerTema(roto)).toBe('sistema');
    expect(cambiarTema('sistema', roto)).toBe('claro');
    expect(document.documentElement.dataset['theme']).toBe('light');
  });
});
