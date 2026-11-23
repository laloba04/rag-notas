/**
 * Tema de color: el del sistema (por defecto), claro u oscuro. Se guarda en localStorage del
 * navegador, solo como comodidad: si no se puede leer o escribir (modo privado, almacenamiento
 * bloqueado), se sigue el del sistema. Se aplica con data-theme en <html>; styles.css hace el resto.
 */
export type Tema = 'sistema' | 'claro' | 'oscuro';

const CLAVE = 'rag-notas.tema';
const SIGUIENTE: Record<Tema, Tema> = { sistema: 'claro', claro: 'oscuro', oscuro: 'sistema' };

export function leerTema(almacen: Pick<Storage, 'getItem'> | null = almacenLocal()): Tema {
  try {
    const t = almacen?.getItem(CLAVE);
    return t === 'claro' || t === 'oscuro' ? t : 'sistema';
  } catch {
    return 'sistema';
  }
}

export function aplicarTema(tema: Tema, raiz: HTMLElement = document.documentElement) {
  if (tema === 'sistema') delete raiz.dataset['theme'];
  else raiz.dataset['theme'] = tema === 'claro' ? 'light' : 'dark';
}

/** Pasa al siguiente tema, lo aplica y lo guarda. Devuelve el nuevo. */
export function cambiarTema(actual: Tema, almacen: Pick<Storage, 'setItem' | 'removeItem'> | null = almacenLocal()): Tema {
  const tema = SIGUIENTE[actual];
  aplicarTema(tema);
  try {
    if (tema === 'sistema') almacen?.removeItem(CLAVE);
    else almacen?.setItem(CLAVE, tema);
  } catch {
    // sin almacenamiento: vale para esta visita
  }
  return tema;
}

function almacenLocal(): Storage | null {
  try {
    return globalThis.localStorage ?? null;
  } catch {
    return null;
  }
}
