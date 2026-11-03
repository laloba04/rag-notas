"""
Generación: busca los trozos relevantes y le pide a un LLM (Ollama) que responda
usando SOLO esos trozos, citando cada afirmación con [n].

Uso (desde la raíz del repo, después de python ingest.py y con .env configurado):
    python generate.py "¿Cómo deshago el último commit?"
    python generate.py --acceso=publico,restringido "¿Qué tamaño máximo puede tener un Secret?"
"""
import re
import sys
import time

import requests

from config import (LLM_MODELO, NO_LO_TENGO, OLLAMA_URL, TEMPERATURA, TIMEOUT_LLM_S,
                    comprobar_llm)
from retrieve import buscar, separar_acceso

# Instrucciones fijas para el modelo (sección 8 de la guía).
SYSTEM = f"""Eres un asistente que responde preguntas sobre documentación técnica.
Responde ÚNICAMENTE con la información del contexto.
Si el contexto no contiene la respuesta, di exactamente: "{NO_LO_TENGO}"
No uses conocimiento propio.
Cita la fuente de cada afirmación con [n], donde n es el número del fragmento.
Si las fuentes se contradicen, dilo y cita ambas.
Responde siempre en español."""


def construir_prompt(pregunta, resultados):
    """Numera los trozos con su fuente y sección, y añade la pregunta al final."""
    contexto = "\n\n".join(
        f"[{n}] ({meta['fuente']}, sección: {meta['seccion']})\n{texto}"
        for n, (texto, meta, _) in enumerate(resultados, start=1)
    )
    return f"CONTEXTO:\n{contexto}\n\nPREGUNTA: {pregunta}"


def llamar_llm(prompt):
    """Envía el prompt a Ollama y devuelve la respuesta y los tokens usados."""
    comprobar_llm()
    respuesta = requests.post(
        f"{OLLAMA_URL}/api/chat",
        json={
            "model": LLM_MODELO,
            "messages": [
                {"role": "system", "content": SYSTEM},
                {"role": "user", "content": prompt},
            ],
            "stream": False,
            # Temperatura baja: queremos fidelidad al contexto, no creatividad.
            "options": {"temperature": TEMPERATURA},
        },
        timeout=TIMEOUT_LLM_S,   # la primera vez el servidor tiene que cargar el modelo en la GPU
    )
    respuesta.raise_for_status()
    datos = respuesta.json()
    tokens = {"entrada": datos.get("prompt_eval_count"), "salida": datos.get("eval_count")}
    return datos["message"]["content"].strip(), tokens


def citas_invalidas(texto, n_fragmentos):
    """Devuelve las citas [n] que no corresponden a ningún fragmento del contexto."""
    citadas = {int(n) for n in re.findall(r"\[(\d+)\]", texto)}
    return sorted(n for n in citadas if not 1 <= n <= n_fragmentos)


def responder(pregunta, k=5, rerank=True, acceso=None):
    """Pipeline completo: búsqueda -> prompt -> LLM -> comprobación de citas.
    acceso: niveles que puede ver quien pregunta (None = solo lo público, ver retrieve.buscar)."""
    t0 = time.perf_counter()
    resultados = buscar(pregunta, k=k, rerank=rerank, acceso=acceso)
    t1 = time.perf_counter()
    texto, tokens = llamar_llm(construir_prompt(pregunta, resultados))
    t2 = time.perf_counter()
    return {
        "pregunta": pregunta,
        "respuesta": texto,
        "fuentes": [meta for _, meta, _ in resultados],
        "docs": resultados,
        "citas_invalidas": citas_invalidas(texto, len(resultados)),
        "tokens": tokens,
        "segundos": {"busqueda": round(t1 - t0, 2), "generacion": round(t2 - t1, 2)},
    }


if __name__ == "__main__":
    acceso, args = separar_acceso(sys.argv[1:])
    pregunta = " ".join(args) or "¿Cómo deshago el último commit?"
    r = responder(pregunta, acceso=acceso)
    print(f"Pregunta: {r['pregunta']}\n")
    print(r["respuesta"])
    print("\nFuentes:")
    for n, meta in enumerate(r["fuentes"], start=1):
        print(f"  [{n}] {meta['fuente']} > {meta['seccion']}\n      {meta['url']}")
    if r["citas_invalidas"]:
        print(f"\n⚠️  El modelo cita fragmentos que no existen: {r['citas_invalidas']}")
    print(f"\nBúsqueda: {r['segundos']['busqueda']} s · Generación: {r['segundos']['generacion']} s"
          f" · Tokens: {r['tokens']['entrada']} de entrada, {r['tokens']['salida']} de salida")
