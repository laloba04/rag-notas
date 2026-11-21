"""
Prueba real del servidor MCP: lo arranca por stdio como lo haría un asistente, lista las
herramientas y les hace unas preguntas. Usa los modelos de verdad y, para `preguntar`, el LLM.

Lo arranca desde otra carpeta (/) a propósito: un cliente MCP no tiene por qué lanzarlo desde
la raíz del repo, y chroma/ y logs/ son rutas relativas.

Uso (desde la raíz del repo, con el LLM accesible):
    python scripts/probar_mcp.py
"""
import sys
import time
from pathlib import Path

import anyio
from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

RAIZ = Path(__file__).resolve().parent.parent

LLAMADAS = [
    ("buscar_documentacion", {"pregunta": "¿Cómo deshago el último commit?", "k": 3}),
    ("preguntar", {"pregunta": "¿Cómo guardo cambios sin hacer commit para cambiar de rama?"}),
    # Sin respuesta en el corpus (la "pregunta trampa" del golden set).
    ("preguntar", {"pregunta": "¿Cuál es la última versión estable de Kubernetes?"}),
    # Su respuesta solo está en el documento restringido: por MCP, el acceso es solo público.
    ("preguntar", {"pregunta": "¿Qué tamaño máximo puede tener un Secret?"}),
]


async def main():
    servidor = StdioServerParameters(command=sys.executable, args=[str(RAIZ / "mcp_server.py")],
                                     cwd="/")
    t0 = time.perf_counter()
    async with stdio_client(servidor) as (lectura, escritura):
        async with ClientSession(lectura, escritura) as sesion:
            await sesion.initialize()
            herramientas = (await sesion.list_tools()).tools
            print(f"Servidor listo en {time.perf_counter() - t0:.1f} s. Herramientas: "
                  f"{', '.join(h.name for h in herramientas)}\n")
            for nombre, argumentos in LLAMADAS:
                t = time.perf_counter()
                r = await sesion.call_tool(nombre, argumentos)
                print(f"=== {nombre}({argumentos}) -> {time.perf_counter() - t:.1f} s"
                      f"{' [ERROR]' if r.is_error else ''}")
                print(r.content[0].text, "\n")


if __name__ == "__main__":
    anyio.run(main)
