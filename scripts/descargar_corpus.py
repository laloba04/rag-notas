"""
Descarga y limpia 20 documentos técnicos públicos en español para el proyecto RAG.

Fuentes (todas con licencia libre, ver LICENCIAS.md):
  - Documentación de Kubernetes en español  (CC BY 4.0)
  - Pro Git, 2.ª edición en español         (CC BY-NC-SA 3.0)
  - MDN Web Docs en español, guías HTTP     (CC BY-SA 2.5)

Las versiones están fijadas a un commit concreto para que el corpus (y el golden set)
sean reproducibles. Solo usa la biblioteca estándar de Python.

Uso (desde la raíz del repo):  python scripts/descargar_corpus.py
"""
import re
import urllib.request
from datetime import date
from pathlib import Path

COMMITS = {
    "kubernetes": "256a1f49419171f6c7fbdb1857146da1c63eb533",
    "progit": "057c05738acd27b87232983c01dc53dc1388ce64",
    "mdn": "d986dd31d314c87ff0a3330cc3c404b99b915174",
}
REPOS = {
    "kubernetes": "kubernetes/website",
    "progit": "progit/progit2-es",
    "mdn": "mdn/translated-content",
}
LICENCIAS = {
    "kubernetes": "CC BY 4.0",
    "progit": "CC BY-NC-SA 3.0",
    "mdn": "CC BY-SA 2.5",
}
PROYECTO = {
    "kubernetes": "Documentación de Kubernetes",
    "progit": "Pro Git (Scott Chacon y Ben Straub)",
    "mdn": "MDN Web Docs",
}

K = "content/es/docs/concepts/"
P = "book/"
M = "files/es/web/http/guides/"

# (nº, slug, fuente, ruta en el repo, confidencialidad)
DOCUMENTOS = [
    (1,  "k8s-que-es-kubernetes",      "kubernetes", K + "overview/what-is-kubernetes.md", "publico"),
    (2,  "k8s-componentes",            "kubernetes", K + "overview/components.md", "publico"),
    (3,  "k8s-pods",                   "kubernetes", K + "workloads/pods/pod.md", "publico"),
    (4,  "k8s-deployments",            "kubernetes", K + "workloads/controllers/deployment.md", "publico"),
    (5,  "k8s-configmaps",             "kubernetes", K + "configuration/configmap.md", "publico"),
    # Marcado como restringido A PROPÓSITO para probar el filtro por permisos
    (6,  "k8s-secrets",                "kubernetes", K + "configuration/secret.md", "restringido"),
    (7,  "k8s-etiquetas-selectores",   "kubernetes", K + "overview/working-with-objects/labels.md", "publico"),
    (8,  "k8s-network-policies",       "kubernetes", K + "services-networking/network-policies.md", "publico"),
    (9,  "k8s-seguridad-cloud-native", "kubernetes", K + "security/overview.md", "publico"),
    (10, "git-guardar-cambios",        "progit",     P + "02-git-basics/sections/recording-changes.asc", "publico"),
    (11, "git-deshacer-cambios",       "progit",     P + "02-git-basics/sections/undoing.asc", "publico"),
    (12, "git-ramas-y-fusiones",       "progit",     P + "03-git-branching/sections/basic-branching-and-merging.asc", "publico"),
    (13, "git-rebase",                 "progit",     P + "03-git-branching/sections/rebasing.asc", "publico"),
    (14, "git-stash-y-clean",          "progit",     P + "07-git-tools/sections/stashing-cleaning.asc", "publico"),
    (15, "git-hooks",                  "progit",     P + "08-customizing-git/sections/hooks.asc", "publico"),
    (16, "http-vision-general",        "mdn",        M + "overview/index.md", "publico"),
    (17, "http-cors",                  "mdn",        M + "cors/index.md", "publico"),
    (18, "http-cookies",               "mdn",        M + "cookies/index.md", "publico"),
    (19, "http-cache",                 "mdn",        M + "caching/index.md", "publico"),
    (20, "http-autenticacion",         "mdn",        M + "authentication/index.md", "publico"),
]


def descargar(fuente: str, ruta: str) -> str:
    url = f"https://raw.githubusercontent.com/{REPOS[fuente]}/{COMMITS[fuente]}/{ruta}"
    with urllib.request.urlopen(url, timeout=30) as r:
        return r.read().decode("utf-8-sig")


def separar_frontmatter(texto: str):
    """Devuelve (título, cuerpo) de un Markdown con frontmatter YAML."""
    titulo = ""
    m = re.match(r"^---\n(.*?)\n---\n", texto, re.S)
    if m:
        t = re.search(r"^title:\s*(.+)$", m.group(1), re.M)
        if t:
            titulo = t.group(1).strip().strip("'\"")
        texto = texto[m.end():]
    return titulo, texto


def limpiar_markdown_comun(t: str) -> str:
    t = re.sub(r"<!--.*?-->", "", t, flags=re.S)             # comentarios HTML
    t = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", t)                # imágenes
    t = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", t)            # enlaces -> texto
    t = re.sub(r"<[^>\n]+>", "", t)                           # etiquetas HTML sueltas
    t = re.sub(r"\n{3,}", "\n\n", t)
    return t.strip() + "\n"


def tooltip(m) -> str:
    """{{< glossary_tooltip text="Pod" term_id="pod" >}} -> Pod (o el term_id si no hay text)."""
    params = dict(re.findall(r'(\w+)="([^"]*)"', m.group(1)))
    return params.get("text") or params.get("term_id", "")


TOOLTIP = r"\{\{<\s*glossary_tooltip([^>]*)>\}\}"


_cache_glosario = {}


def definicion_glosario(m) -> str:
    """Sustituye el shortcode glossary_definition por el texto del glosario de Kubernetes."""
    params = dict(re.findall(r'(\w+)="([^"]*)"', m.group(1)))
    termino = params.get("term_id", "")
    if termino not in _cache_glosario:
        try:
            crudo = descargar("kubernetes", f"content/es/docs/reference/glossary/{termino}.md")
            _cache_glosario[termino] = separar_frontmatter(crudo)[1]
        except Exception:
            _cache_glosario[termino] = ""      # término sin traducir: se omite
    cuerpo = _cache_glosario[termino]
    if params.get("length") != "all":
        cuerpo = cuerpo.split("<!--more-->")[0]
    cuerpo = cuerpo.replace("<!--more-->", "")
    cuerpo = re.sub(TOOLTIP, tooltip, cuerpo)
    cuerpo = re.sub(r"\{\{[<%].*?[>%]\}\}", "", cuerpo, flags=re.S)
    cuerpo = cuerpo.strip()
    if params.get("prepend") and cuerpo:
        cuerpo = cuerpo[0].lower() + cuerpo[1:]           # "Un configmap es un objeto..."
    return (params.get("prepend", "") + cuerpo).strip() + "\n"


def limpiar_kubernetes(texto: str):
    titulo, t = separar_frontmatter(texto)
    # {{< glossary_tooltip text="Pod" term_id="pod" >}} -> Pod
    t = re.sub(TOOLTIP, tooltip, t)
    # {{< glossary_definition term_id="etcd" length="all" >}} -> definición del glosario oficial
    t = re.sub(r"\{\{<\s*glossary_definition([^>]*)>\}\}", definicion_glosario, t)
    t = re.sub(r'\{\{<\s*feature-state[^>]*>\}\}', "", t)
    t = re.sub(r"\{\{<\s*(note|caution|warning)\s*>\}\}", "> **Nota:** ", t)
    t = re.sub(r"\{\{[<%].*?[>%]\}\}", "", t, flags=re.S)       # resto de shortcodes
    return titulo, limpiar_markdown_comun(t)


def limpiar_mdn(texto: str):
    titulo, t = separar_frontmatter(texto)
    # Macros con argumentos: {{Glossary("origin", "origen")}} -> origen ; {{HTTPHeader("Origin")}} -> Origin
    def macro(m):
        args = re.findall(r'"([^"]*)"', m.group(1))
        return args[-1] if args else ""
    t = re.sub(r"\{\{\s*\w+\s*\((.*?)\)\s*\}\}", macro, t)
    t = re.sub(r"\{\{\s*[\w-]+\s*\}\}", "", t)                  # macros sin argumentos
    return titulo, limpiar_markdown_comun(t)


def limpiar_asciidoc(texto: str):
    lineas_salida, en_codigo, titulo = [], False, ""
    for linea in texto.splitlines():
        l = linea.rstrip()
        if l in ("----", "...."):                              # delimitador de bloque de código
            lineas_salida.append("```")
            en_codigo = not en_codigo
            continue
        if en_codigo:
            lineas_salida.append(l)
            continue
        if re.fullmatch(r"\[\[.*\]\]", l):                     # anclas [[r_rebasing]]
            continue
        if l.startswith("image::") or re.fullmatch(r"\[(source|NOTE|TIP|WARNING)[^\]]*\]", l):
            if l.startswith("[NOTE") or l.startswith("[TIP") or l.startswith("[WARNING"):
                lineas_salida.append("> **Nota:**")
            continue
        if l in ("====", "****", "===="):                     # delimitadores de bloques de aviso
            continue
        if re.fullmatch(r"\.[A-ZÁÉÍÓÚÑ¿].*", l):              # títulos de figuras (.Caption)
            continue
        m = re.match(r"^(={2,6})\s+(.*)$", l)                  # cabeceras
        if m:
            nivel = len(m.group(1)) - 2                        # === -> #
            cabecera = m.group(2).strip()
            if not titulo:
                titulo = cabecera
            lineas_salida.append("#" * max(nivel, 1) + " " + cabecera)
            continue
        l = re.sub(r"\(\(\(.*?\)\)\)", "", l)                  # entradas de índice (((rebase)))
        l = re.sub(r"<<[^>]*>>", "", l)                        # referencias cruzadas
        l = re.sub(r"link:\S+\[([^\]]*)\]", r"\1", l)
        lineas_salida.append(l)
    t = "\n".join(lineas_salida)
    t = re.sub(r"\n{3,}", "\n\n", t)
    return titulo, t.strip() + "\n"


LIMPIADORES = {"kubernetes": limpiar_kubernetes, "progit": limpiar_asciidoc, "mdn": limpiar_mdn}


def main():
    destino = Path("data")
    destino.mkdir(exist_ok=True)
    hoy = date.today().isoformat()
    for num, slug, fuente, ruta, confidencialidad in DOCUMENTOS:
        titulo, cuerpo = LIMPIADORES[fuente](descargar(fuente, ruta))
        url = f"https://github.com/{REPOS[fuente]}/blob/{COMMITS[fuente]}/{ruta}"
        cabecera = (
            "---\n"
            f"titulo: \"{titulo.replace('"', "'")}\"\n"
            f"proyecto: {PROYECTO[fuente]}\n"
            f"url: {url}\n"
            f"licencia: {LICENCIAS[fuente]}\n"
            f"descargado: {hoy}\n"
            f"confidencialidad: {confidencialidad}\n"
            "---\n\n"
        )
        salida = destino / f"{num:02d}-{slug}.md"
        if not cuerpo.lstrip().startswith("# "):              # Pro Git ya trae su título
            cuerpo = f"# {titulo}\n\n" + cuerpo
        salida.write_text(cabecera + cuerpo, encoding="utf-8")
        print(f"{salida.name:40s} {len(cuerpo.split()):6d} palabras")


if __name__ == "__main__":
    main()
