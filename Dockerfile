# Imagen de la API (api.py). Dos etapas: en la primera se instalan las dependencias en un
# entorno virtual; en la segunda solo se copia ese entorno y el código, sin pip ni cachés.
# Imagen base fijada por digest: la misma etiqueta puede apuntar a otra imagen mañana.
# Debian 13 (trixie): con bookworm, Trivy daba 101 vulnerabilidades HIGH/CRITICAL sin arreglo en el
# sistema; con trixie, 51 (2026-11-22).
ARG PYTHON_IMAGEN=python:3.12.16-slim-trixie@sha256:5e2bdb1b0d542d5039cffb8181c65930093949194a65c365dfb1b0fe13a4fa5b

# --- Etapa 1: dependencias ---
FROM ${PYTHON_IMAGEN} AS dependencias
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
RUN python -m venv /opt/venv
COPY requirements.txt .
# requirements.txt empieza por el índice de torch de CPU: sin él se bajaría torch con CUDA (GB de más).
RUN /opt/venv/bin/pip install -r requirements.txt

# --- Etapa 2: ejecución ---
FROM ${PYTHON_IMAGEN}
# HF_HOME: los modelos (embeddings y reranker) van a un volumen, no a la imagen (ver docker-compose.yml).
ENV PATH=/opt/venv/bin:$PATH \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HF_HOME=/modelos \
    HF_HUB_DISABLE_TELEMETRY=1 \
    HOME=/tmp

# Parches de seguridad del sistema publicados después de la imagen base (el CI falla con los HIGH
# o CRITICAL que tengan arreglo). Usuario sin privilegios: si alguien consiguiera ejecutar código
# en la API, no sería root.
RUN apt-get update && apt-get upgrade -y --no-install-recommends && rm -rf /var/lib/apt/lists/* \
 && useradd --uid 10001 --no-create-home --shell /usr/sbin/nologin app \
 && mkdir -p /app/chroma /app/logs /modelos \
 && chown app:app /app/chroma /app/logs /modelos

COPY --from=dependencias /opt/venv /opt/venv
WORKDIR /app
# Solo lo que necesita la API. Nada de .env: la configuración llega por variables de entorno
# al arrancar el contenedor, y la clave de Groq nunca queda dentro de la imagen.
COPY config.py servicio.py retrieve.py generate.py grafo.py ingest.py api.py ./
COPY data/ data/
COPY docker/arrancar-api.sh /usr/local/bin/arrancar-api.sh

USER app
EXPOSE 8000
# /salud no carga modelos ni hace cola: sirve para saber si la API está viva.
HEALTHCHECK --interval=30s --timeout=10s --start-period=20m --retries=3 \
  CMD ["python", "-c", "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/salud', timeout=8).status == 200 else 1)"]
CMD ["/usr/local/bin/arrancar-api.sh"]
