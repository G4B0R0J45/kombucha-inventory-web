# El Fermentario — Traspaso de contexto para el frontend (PWA)

Documento para arrancar un chat nuevo. Resume dónde está el proyecto y qué
sigue, para no re-explicar todo. Adjúntalo (junto con `app/main.py` y
`app/schemas.py`) al iniciar la nueva conversación.

## Qué es

Control de inventario para un negocio artesanal de kombucha (El Fermentario,
de Esteban; Carolina usa la app operativa). Rastrea entradas y salidas de
botellas por sabor y lote, deriva el stock actual por FIFO a partir de un
historial que es la única fuente de verdad, avisa de stock bajo y genera un
reporte de ventas.

Este repositorio (`kombucha-inventory-web`) es la evolución "app real" del
proyecto. **Es un repo separado, aparte de la entrega formal de la materia de
Programación** (rebasa el alcance del curso, por eso vive por su cuenta).

## En qué punto vamos (roadmap)

1. ✅ Separar la lógica de negocio de la interfaz de terminal (paquete `fermentario`).
2. ✅ Consolidar el importador de AppSheet sobre el mismo `core`.
3. ✅ Envolver la capa de servicio en una API local con FastAPI.
4. ⬜ **SIGUIENTE PASO: frontend web (PWA) que consuma la API, con escaneo de QR por la cámara del teléfono.**
5. ⬜ Migrar el almacenamiento de CSV/JSON a base de datos hosteada (Supabase / PostgreSQL) para multiusuario concurrente, con login (que además firma cada movimiento).

## Arquitectura (cada capa solo habla con la de abajo)

```
interfaces
  ├── inventory_cli.py     terminal (input()/print())
  ├── import_appsheet.py   migración one-time desde AppSheet
  └── app/                 API HTTP (FastAPI)  ← el frontend hablará con esto
        ↓
fermentario/
  service.py   operaciones + invariante "memoria == disco" (guarda y si falla revierte)
  core.py      reglas de negocio PURAS, sin I/O (FIFO, validación, stock, catálogo)
  storage.py   los archivos en disco (escrituras atómicas; intercambiable por una BD)
```

Estructura del repo: `app/` (main.py, deps.py, schemas.py), `fermentario/`
(core, storage, service, __init__), `inventory_cli.py`, `import_appsheet.py`,
`generate_lot_label.py`, `test_api.py`, `requirements.txt`,
`requirements-dev.txt`, `.gitignore`, `README.md`, `CLAUDE.md`.

## Stack

- Backend: Python 3.12, FastAPI + uvicorn. Pydantic v2 para validación.
- Datos (por ahora): `movements.csv` (fuente de verdad) + `inventory_data.json`
  (solo catálogo: sabores con `minimum_stock`, y lista de staff).
- Entorno: WSL (Ubuntu) sobre Windows. venv en `.venv`. Se corre con
  `uvicorn app.main:app --reload`; docs interactivas en `/docs`. WSL2 reenvía
  `localhost`, así que el navegador de Windows abre `http://localhost:8000/docs`.
- **Un solo worker**: el servicio guarda el historial en memoria. Varios
  workers divergirían (por eso el paso 5 es una BD hosteada).

## La API (lo que el frontend consumirá)

CORS ya está abierto (`allow_origins=["*"]`) para desarrollo — restringir en prod.

| Método y ruta | Propósito |
|---|---|
| `GET /health` | Liveness. |
| `GET /inventory` | Stock por sabor, con desglose por lote y estado (OK/LOW STOCK). |
| `GET /warnings` | Inconsistencias del historial. |
| `GET /sales` | Resumen de ventas (cada salida cuenta como venta). |
| `GET /movements` | Historial. Query: `?limit=N`, `?flavor=nombre`. |
| `POST /movements` | Registrar entrada/salida. |
| `GET /movements/{id}` | Un movimiento. |
| `PUT /movements/{id}` | Editar (parcial; se revalida replayeando todo el historial). |
| `DELETE /movements/{id}` | Borrar (rechazado si dejaría una salida pasada imposible). |
| `GET /flavors` | Catálogo con totales y estado. |
| `POST /flavors` | Agregar sabor. |
| `PATCH /flavors/{name}` | Cambiar su mínimo. |
| `POST /flavors/{name}/rename` | Renombrar (actualiza todo el historial). |
| `DELETE /flavors/{name}` | Borrar sabor (`?confirm=true` si tiene historial). |
| `GET /staff` | Lista de personal. |
| `POST /staff` | Agregar. |
| `DELETE /staff/{name}` | Quitar. |

**POST /movements** (cuerpo):
```json
{
  "staff_name": "Ana",
  "flavor_name": "Jamaica",
  "movement_type": "entry",      // "entry" o "exit"
  "bottle_quantity": 40,
  "lot_number": "L-001",         // requerido en entry; se ignora en exit (FIFO decide)
  "note": "",
  "new_flavor_minimum": 10        // opcional: crea el sabor si no existe (solo si el movimiento tiene éxito)
}
```
Respuesta (201) incluye `current_stock`, `stock_status`, y `lot_display` (en
salidas, el desglose FIFO, p. ej. `"L-001 (100), L-002 (20)"`).

Códigos: 422 validación (Pydantic/reglas de interfaz), 409 conflicto (stock
insuficiente, nombre duplicado, borrar sabor con historial sin confirm), 404
no encontrado, 400 otras reglas de negocio.

Invariante de staff en la API: en roster vacío, el primer movimiento registra
a la persona; después el `staff_name` debe ser uno ya conocido (el frontend
elige de `GET /staff`). Con login futuro, esto lo resolverá la sesión.

## Esquema de QR (CLAVE para el escaneo del frontend)

Ya existe un generador de etiquetas (`generate_lot_label.py`) y una convención
que el frontend debe respetar al escanear con la cámara:

- **Etiqueta de LOTE**: el QR contiene el código de lote solo (p. ej.
  `LOT202606-K114`). Se escanea en el campo de lote.
- **Etiqueta de SABOR**: el QR contiene `FLAVOR:<nombre>` (p. ej.
  `FLAVOR:Jamaica`). Se escanea en el campo de sabor.
- El prefijo `FLAVOR:` es lo que distingue ambos tipos de forma segura. En
  `core.py`: `looks_like_flavor_label(text)` y `strip_flavor_prefix(text)`.
- La API **rechaza** un `lot_number` que empiece con `FLAVOR:` (422), así que
  el frontend debe enrutar un escaneo con ese prefijo al campo de sabor, no al
  de lote.

Flujo de escaneo esperado (igual que en el CLI): escanear la etiqueta de sabor
llena el sabor; escanear la etiqueta de lote llena el lote. Librería sugerida
para cámara en web: `html5-qrcode` (sin necesidad de pistola USB).

## Objetivos del paso 4 (frontend PWA)

- **Responsive**, pensado para usarse con el teléfono parado frente a los
  estantes en la fermentería.
- **Instalable como PWA** (ícono en el home, pantalla completa, manifest +
  service worker).
- **Escaneo de QR por cámara** con la convención de arriba.
- Consumir los endpoints existentes (no reimplementar lógica de negocio en el
  cliente; el frontend es solo presentación + captura).
- Pantallas mínimas útiles: registrar movimiento (el flujo principal), ver
  inventario con alertas de stock bajo, ver historial, y editor de
  catálogo/reporte de ventas como secundarias.
- Decisión pendiente: framework del frontend (React/Vue con build, o algo más
  ligero tipo HTML+JS/HTMX). Conviene discutirlo al inicio del nuevo chat.

## Cómo me gusta trabajar (preferencias de Gabo)

- Propón cambios radicales cuando aplique y **haz preguntas aclaratorias antes
  de codear**; confirma tu entendimiento antes de construir.
- Explica el **porqué**, no solo el qué — me interesa aprender habilidades
  transferibles como futuro ingeniero de datos, además de resolver el problema.
- Responde las preguntas en orden numerado, igual que se hicieron.
- Prueba contra datos reales cuando se pueda (tengo un export real de AppSheet
  de 414 movimientos).
- Convenciones del código: inglés en código/comentarios, snake_case, comentarios
  que expliquen el porqué. (El pseudocódigo en inglés y `←` es de la materia,
  no aplica aquí.)

## Cómo arrancar el chat nuevo

1. Nuevo chat (mismo Project, para conservar la memoria).
2. Adjunta este archivo + `app/main.py` + `app/schemas.py` (definen el contrato
   exacto de la API). Si el nuevo chat pide más, adjunta `fermentario/core.py`
   y `generate_lot_label.py`.
3. Di algo como: "Continuemos con el paso 4 del roadmap: el frontend PWA.
   Empecemos decidiendo el framework y el flujo de registrar un movimiento con
   escaneo de QR."
