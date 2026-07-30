# El Fermentario — Traspaso de contexto v2 (post-paso 4)

Documento para arrancar la siguiente conversación (despliegue/hosting).
Reemplaza al HANDOFF.md original. Adjuntar junto con `README.md` y
`CLAUDE.md`; si el chat pide código, agregar `app/main.py` y
`fermentario/storage.py`.

## Correcciones a la memoria previa (IMPORTANTE)

- **El entorno es Ubuntu 24.04 NATIVO, no WSL.** Todo lo dicho antes sobre
  WSL/mkcert/redes ya no aplica.
- **El puerto de trabajo es 8001** (el 8000 lo ocupa HPLIP, el servicio de
  la impresora HP).
- **AppSheet sigue siendo el sistema en producción.** `movements.csv` (414
  filas) es un corte de prueba, NO datos vivos; no necesita respaldo. El
  respaldo se vuelve crítico solo a partir del cutover (ver abajo).

## Estado actual

Roadmap: pasos 1–4 ✅. La PWA (vanilla JS servida por FastAPI) corre en
laptop y en teléfono con escaneo QR por cámara. Commit principal:
`Paso 4: PWA frontend with QR scanning, served by FastAPI` en la rama
`paso4-frontend` (mergeada a main) + un commit de fixes posteriores.

Fixes post-integración que ya viven en el repo:
1. `[hidden] { display: none !important; }` en `styles.css` — el atributo
   `hidden` perdía contra reglas `display` de autor (overlay del escáner
   tapaba toda la app).
2. `service-worker.js` en **network-first** para el shell (v3): durante
   desarrollo, cache-first servía CSS viejo y "revivía" bugs ya corregidos.
   Revisar volver a cache-first cuando la app esté hosteada y estable. La
   API nunca se cachea (invariante: el stock siempre viene del servidor).
3. Construcción del escáner dentro del `try` en `app.js`: si el CDN de
   `html5-qrcode` (unpkg.com) está bloqueado (VPN/adblock), se muestra un
   error claro en vez de un overlay negro atorado.

HTTPS/cámara: `mkcert` instalado (CA válida hasta oct 2028). Certificado
`10.118.134.61+3.pem` cubre hotspot del cel (10.118.134.61), casa
(192.168.0.120), localhost y 127.0.0.1. CA instalada en el teléfono. La
WiFi de la UPY aísla clientes: usar siempre el hotspot del cel fuera de
casa. **Pendiente verificar**: que `*.pem` esté en `.gitignore`.

Engineering Week: ficha del proyecto lista (tema Sustainability), guion de
video de 30–60 s definido; falta grabar, subir a YouTube (no listado) y
pegar el link en el punto 10 de la ficha. Guía completa en
`DEMO_Y_VIDEO.md`.

## Siguiente milestone recomendado (decidido en conversación estratégica)

**Hostear el código TAL CUAL con volumen persistente** (Fly.io o Railway):
un worker, CSV en el volumen, HTTPS del proveedor (retira mkcert para
siempre). Cero cambios de código. Esto convierte la app de "programa que
Gabo corre" a "cosa que existe" — lo que Carolina realmente necesita.

Después, el paso 5 se desagrega EN ESTE ORDEN:
1. `storage.py` → Postgres (fácil: la capa existe para esto).
2. Transacciones alrededor de leer-validar-escribir (el diseño real:
   hoy un `threading.Lock` serializa; con BD hay que evitar que dos
   salidas simultáneas sobregiren el stock).
3. Login al final (es el que toca el frontend).

**El cutover es un evento**: el día que El Fermentario reemplace a
AppSheet, migrar con un export FRESCO vía `import_appsheet.py` a un
historial vacío (el corte de julio estará viejo). Desde ese día, los
respaldos son obligatorios.

## División de trabajo entre herramientas

- **Claude Code (terminal)**: todo lo que vive dentro del repo — la clase
  de storage para Postgres, migraciones, tests. Ve el venv y git.
- **Claude Cowork**: alrededor del repo — póster en Canva, documentación.
  Mantenerlo FUERA de `.git` (hubo un incidente con un `index.lock` vía
  su mount; se resolvió, pero los renames de git son frágiles ahí).
  Revocar permisos de borrado de carpetas cuando no se usen.
- **Chat (este Project)**: decisiones con tradeoffs — proveedor de
  hosting, si el auth de Supabase justifica su acoplamiento, cómo migrar
  414+ filas sin ventana de doble verdad.

## Preferencias de trabajo (sin cambios)

Preguntas aclaratorias antes de codear; explicar el porqué (habilidades
transferibles); responder en orden numerado; probar contra datos reales;
inglés en código/comentarios, español en UI; snake_case; el frontend es
solo presentación (toda regla de negocio vive en `fermentario/`).

## Cómo arrancar el chat nuevo

1. Mismo Project (conserva la memoria).
2. Adjuntar: este archivo + `README.md` + `CLAUDE.md`.
3. Abrir con: "Continuemos con el despliegue: hosting con volumen
   persistente (Fly.io vs Railway) para el código tal cual está.
   Empecemos comparando opciones y definiendo el Dockerfile/config."
