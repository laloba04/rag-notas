# Imagen del frontend. Etapa 1: compila Angular con Node. Etapa 2: solo los ficheros estáticos
# servidos por nginx (sin Node, sin node_modules, sin código fuente).
# Imágenes base fijadas por digest.
FROM node:22.23.4-bookworm-slim@sha256:5ec23b7c622b00547753bc6ffd78085dd868a68aeb41c8e0a1418f033e13d96d AS compilacion
WORKDIR /web
# Primero solo los ficheros de dependencias: si no cambian, Docker reutiliza la capa de npm ci.
COPY package.json package-lock.json .npmrc ./
# npm ci instala exactamente lo de package-lock.json (y falla si no cuadra con package.json).
RUN npm ci --no-audit --no-fund
COPY . .
RUN npx ng build

# nginx-unprivileged: corre como usuario sin privilegios (uid 101) y escucha en el 8080.
FROM nginxinc/nginx-unprivileged:1.30.6-alpine@sha256:f5116ed4606f3eb015dd4a8e28cb9b351476dc0ad4bc6e9f93b19cec130cdf03
# Parches de seguridad de Alpine publicados después de la imagen base (el CI falla con los HIGH o
# CRITICAL que tengan arreglo). Hace falta root para instalarlos; después se vuelve al usuario de nginx.
USER root
RUN apk upgrade --no-cache
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY cabeceras-seguridad.conf /etc/nginx/cabeceras-seguridad.conf
COPY --from=compilacion /web/dist/web/browser /usr/share/nginx/html
# Explícito, aunque la imagen base ya usa este usuario: así lo ve cualquiera que lea el Dockerfile
# (y trivy config).
USER 101
EXPOSE 8080
