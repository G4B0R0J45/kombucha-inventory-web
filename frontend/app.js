/* El Fermentario — PWA frontend (all screens).
   RULE OF THIS FILE (mirrors the backend layering): presentation and capture
   only. Every business decision — FIFO, stock math, validation — happens on
   the server; this file gathers input, calls the API, and renders what comes
   back. If a rule feels missing here, it belongs in fermentario/, not here.

   Structure (top to bottom): shared helpers -> one section per screen ->
   navigation -> wire-up. Each screen owns a load_x() that fetches fresh data
   every time its tab opens: stock changes under our feet (another phone, the
   CLI), so nothing user-facing is ever trusted from a previous visit. */

"use strict";

// Same origin as the API (FastAPI serves this file), so no host, no CORS.
const API_BASE = "";
// Must match core.FLAVOR_QR_PREFIX — the one constant the two sides share.
const FLAVOR_QR_PREFIX = "FLAVOR:";

const el = (id) => document.getElementById(id);
const state = {
  flavors: [],          // [{flavor_name, total, minimum_stock, stock_status}]
  staff: [],
  movement_type: "entry",
  scanner: null,        // Html5Qrcode instance, created lazily
};

// ------------------------------------------------------------
// Shared API helpers
// ------------------------------------------------------------
async function get_json(path) {
  const response = await fetch(API_BASE + path);
  if (!response.ok) throw new Error(`GET ${path} -> ${response.status}`);
  return response.json();
}

// One wrapper for every mutating call: returns {status, data} so each caller
// can branch on the status code — never on the prose (api_client.py's rule).
async function send_json(method, path, body) {
  const response = await fetch(API_BASE + path, {
    method,
    headers: body !== undefined ? { "Content-Type": "application/json" } : {},
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  let data = {};
  try { data = await response.json(); } catch { /* empty body is fine */ }
  return { status: response.status, data };
}

// Pydantic's 422 detail is a list of objects; ours is a plain string.
function format_detail(detail) {
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) return detail.map((d) => d.msg || String(d)).join(" · ");
  if (detail && typeof detail === "object" && detail.message) return detail.message;
  return JSON.stringify(detail);
}

function make(tag, class_name, ...children) {
  const node = document.createElement(tag);
  if (class_name) node.className = class_name;
  node.append(...children);
  return node;
}

// ------------------------------------------------------------
// Catalog state (staff + flavors with live totals) — feeds several screens
// ------------------------------------------------------------
async function load_catalog() {
  // Parallel on purpose: independent requests should travel together; on a
  // phone the per-request latency dominates, not the payload size.
  const [staff_data, flavors_data] = await Promise.all([
    get_json("/staff"),
    get_json("/flavors"),
  ]);
  state.staff = staff_data.staff;
  state.flavors = flavors_data.flavors;
  render_staff();
  render_flavors();
  render_stock_hint();
}

function render_staff() {
  const select = el("staff_select");
  const input = el("staff_input");
  if (state.staff.length === 0) {
    // Empty roster: the API bootstraps the first person from the first
    // movement, so we ask for a free-text name exactly once.
    select.hidden = true;
    input.hidden = false;
    return;
  }
  select.hidden = false;
  input.hidden = true;
  select.replaceChildren(...state.staff.map((name) => new Option(name, name)));
}

function render_flavors() {
  const select = el("flavor_select");
  const previous = select.value;
  select.replaceChildren(
    new Option("— elige un sabor —", "", true, false),
    ...state.flavors.map((f) => new Option(f.flavor_name, f.flavor_name)),
  );
  // Keep the selection across refreshes: registering twice in a row for the
  // same flavor (common while restocking a shelf) needs no re-picking.
  if ([...select.options].some((o) => o.value === previous)) select.value = previous;
}

function render_stock_hint() {
  const hint = el("stock_hint");
  const flavor = state.flavors.find((f) => f.flavor_name === el("flavor_select").value);
  if (!flavor) { hint.hidden = true; return; }
  hint.hidden = false;
  hint.textContent = `Stock actual: ${flavor.total} botellas (mínimo ${flavor.minimum_stock})`;
  hint.classList.toggle("is-low", flavor.stock_status !== "OK");
}

// ============================================================
// SCREEN: registrar
// ============================================================
function set_movement_type(type) {
  state.movement_type = type;
  document.body.dataset.movementType = type;   // CSS reads this attribute
  const is_entry = type === "entry";
  el("btn_entry").classList.toggle("is-active", is_entry);
  el("btn_exit").classList.toggle("is-active", !is_entry);
  el("btn_entry").setAttribute("aria-selected", String(is_entry));
  el("btn_exit").setAttribute("aria-selected", String(!is_entry));
  el("lot_field").hidden = !is_entry;      // exits carry no lot: FIFO decides
  el("fifo_note").hidden = is_entry;
  el("btn_submit").textContent = is_entry ? "Registrar entrada" : "Registrar salida";
}

async function open_scanner() {
  el("scanner_overlay").hidden = false;
  state.scanner = state.scanner || new Html5Qrcode("qr_reader");
  try {
    await state.scanner.start(
      { facingMode: "environment" },           // back camera on phones
      { fps: 10, qrbox: { width: 220, height: 220 } },
      on_scan_success,
    );
  } catch (error) {
    await close_scanner();
    show_error("No se pudo abrir la cámara",
      "Revisa el permiso de cámara del navegador. En un teléfono, la página " +
      "debe servirse por HTTPS (o localhost) para poder usar la cámara.");
  }
}

async function close_scanner() {
  el("scanner_overlay").hidden = true;
  if (state.scanner && state.scanner.isScanning) await state.scanner.stop();
}

async function on_scan_success(decoded_text) {
  await close_scanner();
  const text = decoded_text.trim();

  if (text.toUpperCase().startsWith(FLAVOR_QR_PREFIX)) {
    const name = text.slice(FLAVOR_QR_PREFIX.length).trim();
    // Match ignoring case — the label may predate a rename in casing; real
    // near-duplicates are the catalog's job to prevent, not ours.
    const flavor = state.flavors.find(
      (f) => f.flavor_name.toLowerCase() === name.toLowerCase());
    if (!flavor) {
      show_error("Sabor desconocido",
        `La etiqueta dice "${name}", pero ese sabor no está en el catálogo.`);
      return;
    }
    el("flavor_select").value = flavor.flavor_name;
    render_stock_hint();
  } else if (state.movement_type === "entry") {
    el("lot_input").value = text;
  } else {
    show_error("Las salidas no llevan lote",
      "El sistema descuenta por FIFO. Escanea la etiqueta del sabor, " +
      "o cambia a Entrada si estás ingresando botellas.");
  }
}

async function submit_movement() {
  const staff_name = state.staff.length
    ? el("staff_select").value
    : el("staff_input").value.trim();
  const body = {
    staff_name,
    flavor_name: el("flavor_select").value,
    movement_type: state.movement_type,
    bottle_quantity: Number(el("qty_input").value),
    lot_number: state.movement_type === "entry" ? el("lot_input").value.trim() : "",
    note: el("note_input").value.trim(),
  };

  // Pre-flight only for things the user can fix before a round trip; the
  // server remains the real validator (defense in depth, same as Pydantic).
  if (!body.staff_name) return show_error("Falta el nombre", "Indica quién registra.");
  if (!body.flavor_name) return show_error("Falta el sabor", "Elige un sabor de la lista.");
  if (!Number.isInteger(body.bottle_quantity) || body.bottle_quantity <= 0) {
    return show_error("Cantidad inválida", "Las botellas deben ser un entero mayor que cero.");
  }
  if (body.movement_type === "entry" && !body.lot_number) {
    return show_error("Falta el lote", "Una entrada necesita número de lote (escanéalo o escríbelo).");
  }

  const button = el("btn_submit");
  button.disabled = true;
  try {
    const { status, data } = await send_json("POST", "/movements", body);
    if (status === 201) {
      show_success(data);
      el("qty_input").value = "";
      el("lot_input").value = "";
      el("note_input").value = "";
      await load_catalog();          // totals changed; refresh hints
    } else if (status === 409) {
      show_error("Conflicto", format_detail(data.detail));
    } else if (status === 422) {
      show_error("Solicitud inválida", format_detail(data.detail));
    } else {
      show_error(`Error ${status}`, format_detail(data.detail));
    }
  } catch (error) {
    show_error("Sin conexión con el servidor",
      "¿Está corriendo la API? Revisa la red y vuelve a intentar.");
  } finally {
    button.disabled = false;
  }
}

function show_success(data) {
  const low = data.stock_status !== "OK";
  const box = el("result");
  box.hidden = false;
  box.classList.remove("is-error");
  box.replaceChildren(
    make("p", "result-title", state.movement_type === "entry"
      ? `Entrada registrada — ${data.flavor_name}` : `Salida registrada — ${data.flavor_name}`),
    make("p", "result-stock", `${data.current_stock} `,
      make("small", "", "botellas en stock")),
    make("span", low ? "badge is-low" : "badge", data.stock_status),
    ...(data.lot_display
      ? [make("p", "result-lots", (state.movement_type === "exit" ? "Tomar de: " : "Lote: ")
          + data.lot_display)]
      : []),
  );
  box.scrollIntoView({ behavior: "smooth", block: "nearest" });
}

function show_error(title, message) {
  const box = el("result");
  box.hidden = false;
  box.classList.add("is-error");
  box.replaceChildren(make("p", "result-title", title), make("p", "", message));
  box.scrollIntoView({ behavior: "smooth", block: "nearest" });
}

// ============================================================
// SCREEN: inventario
// ============================================================
async function load_inventory() {
  const list = el("inventory_list");
  const warnings = el("inventory_warnings");
  list.replaceChildren(make("p", "muted", "Cargando…"));
  warnings.replaceChildren();
  try {
    const data = await get_json("/inventory");
    if (data.inconsistencies.length) {
      warnings.append(make("div", "banner",
        make("p", "result-title", "Historial con inconsistencias"),
        ...data.inconsistencies.map((w) => make("p", "", w))));
    }
    if (!data.inventory.length) {
      list.replaceChildren(make("p", "muted",
        "Sin sabores todavía. Registra la primera entrada o agrega sabores en Catálogo."));
      return;
    }
    list.replaceChildren(...data.inventory.map(inventory_card));
  } catch {
    list.replaceChildren(make("p", "muted", "No se pudo cargar el inventario. ¿Está corriendo la API?"));
  }
}

function inventory_card(row) {
  const low = row.stock_status !== "OK";
  const lots = Object.entries(row.lots);
  return make("article", "card",
    make("div", "card-head",
      make("h3", "card-title", row.flavor_name),
      make("span", low ? "badge is-low" : "badge", row.stock_status)),
    make("p", "result-stock", `${row.total} `,
      make("small", "",
        row.minimum_stock === null ? "botellas" : `botellas · mínimo ${row.minimum_stock}`)),
    lots.length
      ? make("p", "result-lots", "Lotes: " + lots.map(([l, q]) => `${l} (${q})`).join(", "))
      : make("p", "muted", "Sin lotes con existencia."),
  );
}

// ============================================================
// SCREEN: historial
// ============================================================
function render_history_filter() {
  const select = el("history_flavor");
  const previous = select.value;
  select.replaceChildren(
    new Option("Todos los sabores", ""),
    ...state.flavors.map((f) => new Option(f.flavor_name, f.flavor_name)),
  );
  if ([...select.options].some((o) => o.value === previous)) select.value = previous;
}

async function load_history() {
  render_history_filter();
  const list = el("history_list");
  list.replaceChildren(make("p", "muted", "Cargando…"));
  const params = new URLSearchParams();
  if (el("history_flavor").value) params.set("flavor", el("history_flavor").value);
  if (el("history_limit").value) params.set("limit", el("history_limit").value);
  const query = params.toString() ? "?" + params.toString() : "";
  try {
    const data = await get_json("/movements" + query);
    if (!data.movements.length) {
      list.replaceChildren(make("p", "muted", "Sin movimientos con ese filtro."));
      return;
    }
    // The API returns history order (oldest first, as stored in the CSV);
    // on screen the useful order is newest first — a presentation choice,
    // so it belongs here, not in the API.
    list.replaceChildren(...data.movements.slice().reverse().map(history_row));
  } catch {
    list.replaceChildren(make("p", "muted", "No se pudo cargar el historial."));
  }
}

function history_row(m) {
  const is_entry = m.movement_type === "entry";
  const qty = Number(m.bottle_quantity);   // the CSV stores strings
  const row = make("article", is_entry ? "card mov mov-entry" : "card mov mov-exit",
    make("div", "card-head",
      make("h3", "card-title", `${is_entry ? "+" : "−"}${qty} ${m.flavor_name}`),
      make("button", "mini-btn is-danger", "Borrar")),
    make("p", "mov-meta",
      `#${m.movement_id} · ${m.date_time} · ${m.staff_name}` +
      (is_entry && m.lot_number ? ` · ${m.lot_number}` : "")),
    ...(m.note ? [make("p", "mov-note", m.note)] : []),
  );
  row.querySelector("button").addEventListener("click", () => delete_movement(m));
  return row;
}

async function delete_movement(m) {
  // Native confirm(): honest and sufficient for a v1 utility app.
  if (!confirm(`¿Borrar el movimiento #${m.movement_id} (${m.movement_type === "entry" ? "entrada" : "salida"} de ${m.bottle_quantity} ${m.flavor_name})?`)) return;
  const { status, data } = await send_json("DELETE", `/movements/${m.movement_id}`);
  if (status === 200) {
    await load_catalog();      // stock changed
    await load_history();
  } else {
    // Typical case: a 400 because removing an entry would make a past exit
    // impossible — the server protects the history; we just relay why.
    alert(format_detail(data.detail) || `Error ${status}`);
  }
}

// ============================================================
// SCREEN: ventas
// ============================================================
async function load_sales() {
  const box = el("sales_content");
  box.replaceChildren(make("p", "muted", "Cargando…"));
  try {
    const data = await get_json("/sales");
    if (!data.has_sales) {
      box.replaceChildren(make("p", "muted",
        "Aún no hay ventas: cada salida registrada cuenta como una."));
      return;
    }
    const s = data.summary;
    const max_flavor = Math.max(...Object.values(s.bottles_by_flavor));
    box.replaceChildren(
      make("article", "card",
        make("p", "result-stock", `${s.total_bottles} `,
          make("small", "", `botellas vendidas en ${s.exit_count} salidas`))),
      make("h2", "section-title", "Por sabor"),
      ...Object.entries(s.bottles_by_flavor)
        .sort((a, b) => b[1] - a[1])
        .map(([flavor, n]) => sales_bar(flavor, n, max_flavor)),
      make("h2", "section-title", "Por mes"),
      ...Object.entries(s.bottles_by_month)
        .sort()                                     // chronological
        .map(([month, n]) => make("p", "sales-month",
          make("span", "", month), make("strong", "", `${n}`))),
    );
  } catch {
    box.replaceChildren(make("p", "muted", "No se pudo cargar el resumen de ventas."));
  }
}

function sales_bar(label, value, max_value) {
  const bar = make("div", "sales-bar");
  // Relative widths turn the list into a chart with zero libraries.
  bar.style.width = `${Math.max(6, Math.round((value / max_value) * 100))}%`;
  return make("div", "sales-row",
    make("span", "sales-label", label),
    make("div", "sales-track", bar),
    make("strong", "", `${value}`));
}

// ============================================================
// SCREEN: catálogo (sabores + personal)
// ============================================================
function render_catalog() {
  const flavors = el("catalog_flavors");
  flavors.replaceChildren(...(state.flavors.length
    ? state.flavors.map(catalog_flavor_row)
    : [make("p", "muted", "Sin sabores en el catálogo.")]));

  const staff = el("catalog_staff");
  staff.replaceChildren(...(state.staff.length
    ? state.staff.map(catalog_staff_row)
    : [make("p", "muted", "Sin personal registrado; el primer movimiento registra a la persona.")]));
}

function catalog_flavor_row(f) {
  const row = make("article", "card",
    make("div", "card-head",
      make("h3", "card-title", f.flavor_name),
      make("span", "muted", `mín ${f.minimum_stock}`)),
    make("div", "row-actions",
      make("button", "mini-btn", "Cambiar mínimo"),
      make("button", "mini-btn", "Renombrar"),
      make("button", "mini-btn is-danger", "Borrar")),
  );
  const [btn_min, btn_rename, btn_delete] = row.querySelectorAll("button");
  btn_min.addEventListener("click", () => change_minimum(f));
  btn_rename.addEventListener("click", () => rename_flavor(f));
  btn_delete.addEventListener("click", () => delete_flavor(f));
  return row;
}

async function refresh_after_catalog_change() {
  await load_catalog();
  render_catalog();
}

async function add_flavor() {
  const name = el("new_flavor_name").value.trim();
  const minimum = Number(el("new_flavor_min").value);
  if (!name) return alert("Escribe el nombre del sabor.");
  if (!Number.isInteger(minimum) || minimum < 0) return alert("El mínimo debe ser un entero ≥ 0.");
  const { status, data } = await send_json("POST", "/flavors",
    { flavor_name: name, minimum_stock: minimum });
  if (status === 201) {
    el("new_flavor_name").value = "";
    el("new_flavor_min").value = "";
    await refresh_after_catalog_change();
  } else {
    alert(format_detail(data.detail));
  }
}

async function change_minimum(f) {
  const answer = prompt(`Nuevo mínimo para ${f.flavor_name}:`, f.minimum_stock);
  if (answer === null) return;                       // cancelled
  const minimum = Number(answer);
  if (!Number.isInteger(minimum) || minimum < 0) return alert("Debe ser un entero ≥ 0.");
  const { status, data } = await send_json("PATCH",
    `/flavors/${encodeURIComponent(f.flavor_name)}`, { minimum_stock: minimum });
  if (status === 200) await refresh_after_catalog_change();
  else alert(format_detail(data.detail));
}

async function rename_flavor(f) {
  const new_name = prompt(`Nuevo nombre para ${f.flavor_name}:`, f.flavor_name);
  if (new_name === null || !new_name.trim() || new_name.trim() === f.flavor_name) return;
  const { status, data } = await send_json("POST",
    `/flavors/${encodeURIComponent(f.flavor_name)}/rename`, { new_name: new_name.trim() });
  if (status === 200) await refresh_after_catalog_change();
  else alert(format_detail(data.detail));
}

async function delete_flavor(f) {
  if (!confirm(`¿Borrar el sabor ${f.flavor_name}?`)) return;
  // First attempt WITHOUT confirm: if the flavor has history the API answers
  // 409 explaining that its rows would be erased — we surface that exact
  // warning and only then retry with ?confirm=true. The dangerous decision
  // is made by the user reading the server's own words, not by the client
  // pre-deciding it (same two-step confirmation the CLI uses).
  let { status, data } = await send_json("DELETE",
    `/flavors/${encodeURIComponent(f.flavor_name)}`);
  if (status === 409) {
    if (!confirm(format_detail(data.detail) + "\n\n¿Continuar de todas formas?")) return;
    ({ status, data } = await send_json("DELETE",
      `/flavors/${encodeURIComponent(f.flavor_name)}?confirm=true`));
  }
  if (status === 200) await refresh_after_catalog_change();
  else alert(format_detail(data.detail));
}

function catalog_staff_row(name) {
  const row = make("article", "card",
    make("div", "card-head",
      make("h3", "card-title", name),
      make("button", "mini-btn is-danger", "Quitar")));
  row.querySelector("button").addEventListener("click", async () => {
    if (!confirm(`¿Quitar a ${name} del personal?`)) return;
    const { status, data } = await send_json("DELETE", `/staff/${encodeURIComponent(name)}`);
    if (status === 200) await refresh_after_catalog_change();
    else alert(format_detail(data.detail));
  });
  return row;
}

async function add_staff() {
  const name = el("new_staff_name").value.trim();
  if (!name) return alert("Escribe el nombre.");
  const { status, data } = await send_json("POST", "/staff", { name });
  if (status === 201) {
    el("new_staff_name").value = "";
    await refresh_after_catalog_change();
  } else {
    alert(format_detail(data.detail));
  }
}

// ============================================================
// Navigation — each screen refreshes its data when it opens
// ============================================================
const SCREENS = {
  register:  { title: "registrar movimiento", on_show: null },
  inventory: { title: "inventario",           on_show: load_inventory },
  history:   { title: "historial",            on_show: load_history },
  sales:     { title: "ventas",               on_show: load_sales },
  catalog:   { title: "catálogo",             on_show: async () => { await load_catalog(); render_catalog(); } },
};

function set_screen(name) {
  for (const key of Object.keys(SCREENS)) {
    el(`screen_${key}`).hidden = key !== name;
  }
  document.querySelectorAll(".tab").forEach((tab) =>
    tab.classList.toggle("is-active", tab.dataset.screen === name));
  el("screen_title").textContent = SCREENS[name].title;
  if (SCREENS[name].on_show) {
    SCREENS[name].on_show().catch(() => { /* each loader shows its own error */ });
  }
}

// ------------------------------------------------------------
// Wire-up
// ------------------------------------------------------------
el("btn_entry").addEventListener("click", () => set_movement_type("entry"));
el("btn_exit").addEventListener("click", () => set_movement_type("exit"));
el("flavor_select").addEventListener("change", render_stock_hint);
el("btn_scan").addEventListener("click", open_scanner);
el("btn_scan_close").addEventListener("click", close_scanner);
el("btn_submit").addEventListener("click", submit_movement);
el("history_flavor").addEventListener("change", load_history);
el("history_limit").addEventListener("change", load_history);
el("btn_add_flavor").addEventListener("click", add_flavor);
el("btn_add_staff").addEventListener("click", add_staff);
document.querySelectorAll(".tab").forEach((tab) =>
  tab.addEventListener("click", () => set_screen(tab.dataset.screen)));

set_movement_type("entry");
set_screen("register");
load_catalog().catch(() =>
  show_error("No se pudo cargar el catálogo",
    "¿Está corriendo la API? Recarga la página para reintentar."));

// PWA: register the service worker (no-op where unsupported).
if ("serviceWorker" in navigator) {
  navigator.serviceWorker.register("service-worker.js");
}
