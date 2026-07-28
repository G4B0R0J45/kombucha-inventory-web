# Guía: integrar y entender el frontend de El Fermentario

Esta guía te lleva de cero a tener la pantalla de "registrar movimiento"
funcionando, como si lo hicieras sin ayuda. No es solo una lista de comandos:
cada paso explica **por qué** se hace así, porque esa es la parte que te
llevas a cualquier proyecto futuro.

---

## Parte 0 — La idea general (léela antes de tocar nada)

Hasta ahora tu proyecto tiene tres capas: `core` (reglas puras), `service`
(operaciones + consistencia memoria/disco) y `storage` (archivos), con dos
interfaces encima: la terminal y la API HTTP. El frontend es **una tercera
interfaz**, con una regla de oro idéntica a las otras dos:

> El frontend solo **presenta y captura**. Toda decisión de negocio (FIFO,
> validación, stock) vive en el servidor. Si al escribir JavaScript sientes
> que estás programando una regla de negocio, detente: esa regla pertenece a
> `fermentario/`, no al navegador.

¿Por qué importa tanto? Porque el navegador es territorio del usuario:
cualquiera puede abrir las herramientas de desarrollo y saltarse tu
JavaScript. Una validación en el cliente es **cortesía** (feedback rápido);
la validación en el servidor es **la ley**. Por eso `app.js` valida cosas
obvias antes de enviar (cantidad > 0, sabor elegido) pero el servidor
re-valida todo. Es el mismo "defensa en profundidad" que ya usas entre
Pydantic y el service.

La decisión arquitectónica clave de esta etapa: **el propio FastAPI sirve el
frontend**. Nada de Node, npm ni build. Un solo proceso, una sola origen.

---

## Parte 1 — Colocar los archivos

**Paso 1.** Descomprime `frontend.zip` en la raíz del repo. Debe quedar así:

```
kombucha-inventory-web/
├── app/
├── fermentario/
├── frontend/              ← nueva carpeta
│   ├── index.html         la página (estructura)
│   ├── styles.css         la apariencia
│   ├── app.js             el comportamiento (habla con tu API)
│   ├── manifest.webmanifest   metadatos de la PWA
│   ├── service-worker.js  caché del "shell" para abrir rápido
│   └── icons/             íconos de la PWA (192 y 512 px)
├── inventory_cli.py
└── ...
```

La separación HTML/CSS/JS no es decorativa: es la misma disciplina de capas
que ya practicas en Python. `index.html` = estructura, `styles.css` =
presentación, `app.js` = comportamiento. Cambiar un color jamás debería
tocar la lógica, y viceversa.

**Paso 2.** Reemplaza `app/main.py` con el nuevo. Si prefieres editar el
tuyo a mano (recomendado para entenderlo), son exactamente dos cambios:

Primero, agrega `import os` junto a los imports de arriba.

Segundo, pega esto **al final del archivo, después de todos los endpoints**:

```python
from fastapi.staticfiles import StaticFiles

_FRONTEND_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "frontend")
if os.path.isdir(_FRONTEND_DIR):
    app.mount("/", StaticFiles(directory=_FRONTEND_DIR, html=True), name="frontend")
```

**Por qué al final, y no arriba con los demás imports:** Starlette (el motor
debajo de FastAPI) resuelve las rutas **en orden de registro**. Al montar
`"/"` al último, cada endpoint definido antes (`/health`, `/movements`,
`/docs`…) gana primero, y el mount solo atrapa lo que sobra: `index.html`,
el CSS, el JS, los íconos. Si lo montaras primero, `"/"` se tragaría *todo*
y tu API dejaría de responder. El orden **es** la configuración.

**Qué hace `html=True`:** le dice a StaticFiles que cuando pidan un
directorio (como `"/"`) responda con su `index.html`. Sin eso, visitar la
raíz daría 404.

**Qué ganas con servir el frontend desde FastAPI:** página y API comparten
origen (`localhost:8000`), así que el navegador no aplica CORS. CORS es la
política que bloquea a una página de `origen-A` para llamar a una API de
`origen-B` sin permiso explícito; al vivir juntos, el problema desaparece
por diseño. El middleware `allow_origins=["*"]` que ya tenías queda solo
como red de seguridad para desarrollo.

---

## Parte 2 — Arrancar y verificar

**Paso 3.** Levanta el servidor como siempre:

```bash
source .venv/bin/activate
uvicorn app.main:app --reload
```

**Paso 4.** Abre `http://localhost:8000/` en el navegador. Checklist de
verificación, en orden (si algo falla, el orden te dice dónde buscar):

1. Se ve la pantalla con el título "El Fermentario" → el mount funciona.
2. El selector de sabores tiene tus sabores reales → `app.js` alcanzó
   `GET /flavors`; frontend y API se hablan.
3. `http://localhost:8000/docs` sigue abriendo → la API no fue opacada por
   el mount (el orden de registro está bien).
4. Al elegir un sabor aparece "Stock actual: N botellas" → los datos vienen
   de tu `movements.csv` real, replayados por `compute_stock`.

**Paso 5.** Registra un movimiento de prueba y **audita el resultado en las
tres interfaces**: la respuesta en pantalla, la última línea de
`movements.csv`, y el CLI (`python3 inventory_cli.py`). Ver el mismo
movimiento en los tres lugares es la demostración física de tu arquitectura:
tres interfaces, un solo service, una sola verdad.

Prueba también los errores a propósito, porque la mitad de una buena UI es
cómo falla: una salida mayor al stock debe mostrar "Conflicto" (el 409 de tu
API), y una entrada sin lote debe pedírtelo antes siquiera de enviar.

---

## Parte 3 — Entender cada pieza (para poder modificarla)

### index.html — la estructura

Es un formulario sin `<form>`. Suena raro, pero tiene motivo: un `<form>`
nativo, al enviarse, **recarga la página** — perderías el estado del scanner
y la respuesta se mostraría como página nueva. En una app que habla JSON con
`fetch()`, los botones disparan funciones y la página nunca navega. (Esto es
lo que la gente llama SPA, *single-page application*, en su forma mínima.)

Fíjate en el atributo `data-movement-type="entry"` del `<body>`: es un dato
plantado en el HTML para que **CSS y JS lo compartan**. Cambiarlo a `"exit"`
recolorea toda la pantalla sin tocar un solo estilo desde JavaScript.

### styles.css — la apariencia

La técnica central son las **variables CSS**. Arriba se define
`--accent: var(--entrada)` (verde) y una sola regla lo cambia:

```css
body[data-movement-type="exit"] { --accent: var(--salida); }
```

Todo lo que usa `var(--accent)` — el botón activo, el foco, el botón de
enviar, la tarjeta de resultado — cambia en cascada. Una variable, feedback
de pantalla completa. Es el mismo principio que centralizar `BASE_URL` en
`api_client.py`: define una vez, usa en todas partes, cambia en un lugar.

El diseño es *mobile-first*: una columna de máximo 480 px, botones de más de
48 px de alto (el mínimo táctil recomendado), porque el usuario real es
Carolina con el teléfono en una mano frente a los estantes.

### app.js — el comportamiento

Léelo de abajo hacia arriba: la sección "Wire-up" conecta cada botón con su
función; el resto son las funciones. Tres patrones que conviene entender:

**Cargar el catálogo en paralelo.** `Promise.all([get_json("/staff"),
get_json("/flavors")])` lanza las dos peticiones a la vez. En una red móvil
lo que domina es la latencia de cada viaje, no el tamaño de los datos;
peticiones independientes deben viajar juntas.

**Rutear el escaneo por el prefijo.** Un solo botón de escanear, y el
*payload* decide el destino: si empieza con `FLAVOR:` va al campo de sabor
(validado contra el catálogo, ignorando mayúsculas); si no, al campo de
lote — y solo en modo Entrada, porque las salidas no llevan lote (FIFO
decide). Esta es exactamente la convención que definiste en `core.py` y
`generate_lot_label.py`; el frontend solo la respeta.

**Bifurcar por código de estado, nunca por el texto.** Tras el `POST`, el
código HTTP es el contrato que los programas leen: `201` = creado (pinta el
resultado), `409` = conflicto de negocio (stock insuficiente), `422` =
petición inválida. El texto del mensaje es para humanos y puede cambiar; el
código no. Es la misma regla que ya seguiste en `api_client.py`.

### service-worker.js y manifest.webmanifest — la PWA

El **manifest** son los metadatos de instalación: nombre, ícono, colores, y
`display: standalone` (sin barra del navegador). Es lo que hace aparecer
"Agregar a pantalla de inicio".

El **service worker** es un script que se instala entre tu página y la red.
El nuestro tiene una política deliberada y asimétrica:

- Los archivos del *shell* (HTML, CSS, JS, íconos) se sirven **desde caché**
  → la app abre instantánea.
- Las llamadas a la API **jamás se cachean** → los números de stock siempre
  viajan por red.

¿Por qué la asimetría? Un `/inventory` cacheado mostraría stock viejo con
cara de verdad — el equivalente web de romper tu invariante "memoria ==
disco". Sin conexión, la app abre (shell cacheado) y dice honestamente "sin
conexión", en lugar de mentir con datos rancios. **Regla operativa:** cuando
cambies un archivo del shell, sube la versión en `CACHE_VERSION`
(`...-v1` → `...-v2`); al activarse, el worker borra la caché vieja y así
llegan las actualizaciones a los teléfonos que ya instalaron la app.

---

## Parte 4 — Probarlo en el teléfono (el detalle que atrapa a todos)

Los navegadores solo permiten usar la cámara en **contextos seguros**:
`localhost` o HTTPS. Es una regla de seguridad del navegador, no de tu
código: nadie quiere que una página cualquiera en HTTP encienda cámaras.

Consecuencia práctica: en tu laptop, `http://localhost:8000` escanea sin
problema (localhost está exento). Pero si abres `http://192.168.x.x:8000`
desde el celular, la página carga y **la cámara será bloqueada**.

La solución limpia es un certificado local con `mkcert`:

```bash
# una sola vez, en la laptop
sudo apt install mkcert libnss3-tools
mkcert -install                      # crea tu autoridad certificadora local
mkcert 192.168.x.x localhost         # usa TU IP local (ip addr | grep inet)

# arrancar con TLS
uvicorn app.main:app --host 0.0.0.0 \
  --ssl-keyfile ./192.168.x.x+1-key.pem \
  --ssl-certfile ./192.168.x.x+1.pem
```

Dos cosas que explican esos flags: `--host 0.0.0.0` hace que uvicorn escuche
en todas las interfaces de red (por defecto solo escucha en localhost, y el
teléfono no podría entrar); y el teléfono mostrará una advertencia de
certificado la primera vez, porque tu autoridad `mkcert` vive solo en la
laptop — acéptala, o instala el certificado raíz de mkcert en el teléfono.

Nota WSL: WSL2 reenvía `localhost` hacia Windows automáticamente, pero para
que **otro dispositivo** de la red entre, quizá necesites reenviar el puerto
en Windows (`netsh interface portproxy`) o usar el modo de red *mirrored* de
WSL. Si el teléfono no conecta, prueba primero desde el navegador de Windows
con la IP: eso te dice si el problema es uvicorn o el reenvío.

Mientras tanto, puedes probar el escaneo en la laptop: genera una etiqueta
con `generate_lot_label.py`, ábrela en el teléfono o imprímela, y muéstrala
a la webcam desde `localhost`.

---

## Parte 5 — Problemas típicos y cómo leerlos

**"No se pudo cargar el catálogo".** `app.js` no alcanzó la API. ¿Está
corriendo uvicorn? ¿Abriste la página desde `localhost:8000` y no como
archivo suelto (`file://...`)? Servida por FastAPI, la página llama a rutas
relativas del mismo origen; abierta como archivo, no hay origen que valga.

**Cambié el CSS/JS y no veo el cambio.** Sospecha del service worker
sirviendo la versión cacheada. Sube `CACHE_VERSION`, o durante el desarrollo
usa DevTools → Application → Service Workers → "Update on reload".

**`/docs` da 404.** El mount quedó antes de los endpoints. Muévelo al final
de `main.py` — el orden de registro decide quién atiende cada ruta.

**La cámara no abre en el teléfono.** Contexto no seguro (HTTP con IP).
Vuelve a la Parte 4.

**Escaneo una etiqueta de sabor y dice "sabor desconocido".** El nombre del
QR no coincide con el catálogo actual (¿renombraste el sabor después de
imprimir la etiqueta?). Reimprime con `generate_lot_label.py` o renombra al
valor de la etiqueta.

---

## Parte 6 — Qué sigue

Con esta base, cada pantalla nueva es el mismo patrón en miniatura: un
`fetch` a un endpoint que ya existe + funciones que pintan la respuesta. La
de inventario (`GET /inventory` → tabla con alertas) es la candidata natural
y notablemente más simple que esta, porque solo lee. Después, historial
(`GET /movements`) y el editor de catálogo. El backend no necesita cambios:
esa es la recompensa de haber separado las capas desde el principio.
