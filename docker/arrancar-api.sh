#!/bin/sh
# Arranque de la API en el contenedor. Si el volumen de Chroma está vacío (primer arranque),
# indexa data/ antes de servir: sin colección, /buscar y /preguntar no funcionarían.
set -eu

if python -c "import sys, servicio; sys.exit(0 if servicio.contar_fragmentos() else 1)"; then
    echo "Colección encontrada en chroma/: no hace falta ingesta."
else
    echo "No hay colección en chroma/: ingesta de data/ (la primera vez también descarga el modelo de embeddings)."
    python ingest.py
fi

# --no-proxy-headers: uvicorn no reescribe la IP del cliente con X-Forwarded-For; de eso se encarga
# api.ip_cliente, que solo la cree si viene de PROXIES_DE_CONFIANZA.
exec uvicorn api:app --host 0.0.0.0 --port 8000 --no-proxy-headers
