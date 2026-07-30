# El Fermentario — Correr el demo y grabar el video

## 1. Correr la app

### En la laptop (desarrollo, sin cámara del cel)

```bash
cd ~/Documents/kombucha-inventory-web
source .venv/bin/activate
uvicorn app.main:app --reload --port 8001
```

Abrir `http://localhost:8001`. La cámara de la **laptop** funciona aquí
(localhost es contexto seguro). Detener con Ctrl+C.

> Puerto 8001 siempre: el 8000 lo ocupa el servicio de la impresora HP.

### En el celular (escaneo QR con la cámara del teléfono)

Requisitos ya hechos una sola vez (NO se repiten): `mkcert -install`,
certificado `10.118.134.61+3.pem` (cubre también la red de casa
`192.168.0.120`), y la CA `rootCA.pem` instalada en el teléfono.

Cada sesión:

1. **Hotspot del teléfono encendido**; la laptop conectada a ese hotspot.
   (La WiFi de la UPY NO sirve: aísla los dispositivos entre sí.)
2. Verificar la IP de la laptop: `hostname -I` → debe ser `10.118.134.61`.
   Si cambió, regenerar: `mkcert <IP_nueva> 192.168.0.120 localhost 127.0.0.1`
   y usar los `.pem` nuevos en el comando.
3. Levantar el servidor:

```bash
cd ~/Documents/kombucha-inventory-web
source .venv/bin/activate
uvicorn app.main:app --host 0.0.0.0 --port 8001 \
  --ssl-keyfile ./10.118.134.61+3-key.pem \
  --ssl-certfile ./10.118.134.61+3.pem
```

4. En el teléfono: `https://10.118.134.61:8001` (escribir el `https://`).
5. Probar "Escanear etiqueta QR": si abre el visor, todo listo.

**No reiniciar el hotspot a mitad de sesión** (la IP puede cambiar).
**VPN apagada** en la laptop mientras se trabaja: puede bloquear
`unpkg.com`, de donde carga la librería de escaneo.

## 2. Preparar los datos del video (NO filmar sobre los reales)

Correr el servidor apuntando a un directorio de demo:

```bash
FERMENTARIO_DATA_DIR=~/demo_data uvicorn app.main:app --host 0.0.0.0 --port 8001 \
  --ssl-keyfile ./10.118.134.61+3-key.pem --ssl-certfile ./10.118.134.61+3.pem
```

Sembrar **desde la propia UI** (sirve de ensayo general):

1. Entrada: Jamaica, lote `L-001`, 100 botellas (crea sabor con mínimo 20).
2. Entrada: Jamaica, lote `L-002`, 50 botellas.
3. Entrada: Mango, lote `M-001`, 25 botellas (mínimo 20 → casi en alerta).
4. (Durante la grabación) Salida: Jamaica, 120 → cruza los dos lotes:
   "Tomar de: L-001 (100), L-002 (20)". **Esta es la toma clave.**
5. (Durante la grabación) Salida: Mango, 10 → deja 15 < 20 → LOW STOCK
   visible en Inventario.

Tener a la mano las etiquetas impresas (`generate_lot_label.py`): la de
sabor Jamaica, la de sabor Mango, y las de lotes L-001, L-002, M-001.

## 3. Grabar (guion del punto 9 de la ficha, 30–60 s)

Tres fuentes de video, cada una para lo que hace bien:

- **Segundo teléfono** (pídelo prestado): las tomas físicas — mano
  sosteniendo tu cel apuntando al QR de una botella. Es la toma que
  demuestra que el proyecto es real. Luz de frente, QR sin reflejos.
- **Grabación de pantalla del cel** (tile "Grabar pantalla" en el Moto):
  primeros planos de la UI — la tarjeta verde de confirmación, el
  desglose FIFO, la pestaña Inventario con LOW STOCK.
- **Grabación de pantalla de la laptop** (Ctrl+Alt+Shift+R en Ubuntu;
  queda en `~/Videos/Screencasts`): los 2–3 segundos del CLI mostrando
  el mismo movimiento ("tres interfaces, una sola verdad").

Graba cada escena por separado, varias tomas; es más fácil que una toma
perfecta continua. Une y recorta con **Kdenlive** (`sudo apt install
kdenlive`) o cualquier editor del teléfono (CapCut). Agrega los textos en
pantalla del guion. Exporta a MP4 1080p.

## 4. Subir y enlazar

**YouTube en modo "No listado"** (unlisted): no aparece en búsquedas pero
cualquiera con el enlace lo ve, sin iniciar sesión, y reproduce bien desde
cualquier cel que escanee el QR del póster. (Drive funciona como plan B,
pero hay que poner "cualquier persona con el enlace" y reproduce peor.)

1. Subir el MP4 → Visibilidad: **No listado**.
2. Título: `El Fermentario — inventario sin desperdicio (demo)`.
3. Copiar el enlace → pegarlo en el **punto 10 de la ficha** del documento
   compartido del equipo.
4. Quien arme el póster genera el QR desde ese enlace (Canva lo hace).
   **Prueba de fuego:** imprimir el módulo a tamaño real y escanear el QR
   desde 1.5 m con un teléfono. Si no lee, agrandar el QR.

## 5. Después de grabar

- Quitar la CA de mkcert del teléfono si molesta el aviso "la red puede
  estar supervisada" (Ajustes → Seguridad → Credenciales de confianza).
- Volver a correr con datos reales = simplemente omitir el
  `FERMENTARIO_DATA_DIR` (el directorio `~/demo_data` puede borrarse).
