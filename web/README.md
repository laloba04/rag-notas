# Frontend de rag-notas

Chat en Angular 22 (componentes standalone, signals, sin zone.js y sin librerías de interfaz) sobre la API de `api.py`.

```bash
nvm use                        # Node 22.23.4 (.nvmrc); Angular 22 necesita 22.22.3 o más
npm ci                         # instala exactamente lo de package-lock.json
npm start                      # http://localhost:4200, con /api reenviado a http://127.0.0.1:8000
npm test -- --watch=false      # tests con Vitest y jsdom (sin navegador)
npm run build                  # dist/web, para servir con nginx
```

La API tiene que estar arrancada aparte (`uvicorn api:app` desde la raíz del repo). El frontend llama siempre a `/api/...` en su mismo origen: en desarrollo lo reenvía el proxy de `ng serve` (`proxy.conf.json`), y en Docker lo hará nginx. Así no hace falta abrir CORS.

Todo lo que llega de la API (respuesta, fragmentos, nombres de documento) se pinta con interpolación de Angular, que lo escapa: ni `innerHTML` ni Markdown. Los fragmentos salen de documentos no confiables, y una imagen o un enlace incrustados podrían enviar datos a otro servidor.
