/* TEK2day Finance — command palette + watchlist (front-end).
 * Palette: typing in the command bar (slash optional) shows commands + ticker
 *   matches together, and runs the app's existing runCommand. Watchlist: a
 *   LOGIN-GATED sidebar + monitor backed by /api/watchlists. No Kilby code. */
(function () {
  const $ = (id) => document.getElementById(id);
  const cmd = $("command-input");
  const wlSidebar = $("wl-sidebar");
  const content = $("content");

  const fmtPx = (n) => (n == null ? "—" : Number(n).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 }));
  const fmtChg = (n) => (n == null ? "—" : (n >= 0 ? "+" : "−") + Math.abs(n).toFixed(2) + "%");
  const chgCls = (n) => (n == null ? "" : n >= 0 ? "up" : "down");
  const esc = (s) => String(s == null ? "" : s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

  const css = document.createElement("style");
  css.textContent = `
    .pal{position:fixed;z-index:1500;background:#0a0a07;border:1px solid var(--line-2);border-radius:3px;box-shadow:0 16px 40px rgba(0,0,0,.7);overflow:hidden;display:none}
    .pal.open{display:block}
    .pal-sec{color:var(--muted);font-size:9px;letter-spacing:.12em;padding:6px 12px 3px;background:var(--panel)}
    .pal-row{display:grid;grid-template-columns:120px 1fr auto;gap:10px;align-items:baseline;padding:6px 12px;cursor:pointer;border-top:1px solid var(--line);font-size:11.5px}
    .pal-row.sel{background:var(--amber-glow)}
    .pal-code{color:var(--amber);font-weight:700;white-space:nowrap}
    .pal-name{color:var(--text);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
    .pal-meta{color:var(--muted);font-size:10px;text-align:right;white-space:nowrap}
    .wl-head{padding:10px 8px 4px}
    .wl-tabs{display:flex;gap:4px;flex-wrap:wrap;margin-bottom:8px}
    .wl-tab{font-size:10px;letter-spacing:.05em;padding:3px 8px;border-radius:3px;color:var(--muted);border:1px solid var(--line-2);cursor:pointer;white-space:nowrap}
    .wl-tab.on{color:#000;background:var(--amber);border-color:var(--amber);font-weight:700}
    .wl-tab.add{color:var(--amber-dim);border-style:dashed}
    .wl-count{display:flex;justify-content:space-between;color:var(--muted);font-size:9px;letter-spacing:.1em;padding:2px 4px 6px}
    .wl-item{display:grid;grid-template-columns:50px 1fr 54px 14px;gap:8px;align-items:baseline;padding:6px 8px;border-left:2px solid transparent;cursor:pointer;font-size:11.5px}
    .wl-item:hover{background:rgba(255,255,255,.04)}
    .wl-item.active{background:var(--panel-2);border-left-color:var(--amber)}
    .wl-sym{color:var(--amber);font-weight:700}
    .wl-px{text-align:right;font-variant-numeric:tabular-nums}
    .wl-chg{text-align:right;font-variant-numeric:tabular-nums;font-size:10.5px}
    .up{color:var(--green)} .down{color:var(--red)}
    .wl-wx{visibility:hidden;color:var(--muted);text-align:right;cursor:pointer;font-size:10px}
    .wl-item:hover .wl-wx{visibility:visible}
    .wl-wx:hover{color:var(--red)}
    .wl-empty{color:var(--muted);font-size:11px;padding:16px 10px;line-height:1.6}
    .wl-foot{display:flex;gap:8px;padding:9px 8px;border-top:1px solid var(--line)}
    .wl-btn{font-size:10px;letter-spacing:.05em;padding:5px 9px;border:1px solid var(--line-2);border-radius:3px;color:var(--text);background:var(--panel-2);cursor:pointer}
    .wl-btn:hover{border-color:var(--amber-dim)}
    .wl-btn.amber{border-color:var(--amber-dim);color:var(--amber);background:var(--amber-glow);font-weight:700}
    .mon-toolbar{display:flex;align-items:center;gap:8px;padding:12px 16px;border-bottom:1px solid var(--line)}
    .mon-title{font-family:var(--sans);font-weight:700;letter-spacing:.04em}
    .mon-title b{color:var(--amber)}
    .mon table{width:100%;border-collapse:collapse;font-size:11.5px}
    .mon thead th{position:sticky;top:0;background:var(--panel);text-align:right;color:var(--muted);font-weight:600;font-size:9.5px;letter-spacing:.07em;padding:8px 16px;border-bottom:1px solid var(--line-2);white-space:nowrap;cursor:pointer}
    .mon thead th.l{text-align:left}
    .mon thead th.on{color:var(--amber)}
    .mon tbody td{text-align:right;padding:7px 16px;border-bottom:1px solid var(--line);font-variant-numeric:tabular-nums;white-space:nowrap}
    .mon tbody td.l{text-align:left}
    .mon tbody tr{cursor:pointer}
    .mon tbody tr:hover{background:rgba(255,255,255,.03)}
    .mon .t-sym{color:var(--amber);font-weight:700}
    .mon .t-sec{color:var(--muted)}
    .mon .rm{color:var(--muted);cursor:pointer}.mon .rm:hover{color:var(--red)}
    .wlmenu{position:fixed;background:#0a0a07;border:1px solid var(--line-2);border-radius:3px;z-index:1600;box-shadow:0 12px 30px rgba(0,0,0,.6);font-size:11px}
    .wlmenu div{padding:7px 16px;border-bottom:1px solid var(--line);cursor:pointer}.wlmenu div:last-child{border-bottom:0}.wlmenu div:hover{background:var(--amber-glow);color:var(--amber)}
    .wl-scrim{position:fixed;inset:0;background:rgba(0,0,0,.6);display:none;align-items:center;justify-content:center;z-index:1400}
    .wl-scrim.open{display:flex}
    .wl-modal{width:380px;max-width:92vw;background:var(--panel);border:1px solid var(--line-2);border-radius:4px;padding:18px;font-family:var(--mono)}
    .wl-modal h3{font-family:var(--sans);font-size:14px;margin:0 0 12px}
    .wl-lbl{color:var(--muted);font-size:9px;letter-spacing:.12em;margin-bottom:6px}
    .wl-modal input,.wl-modal textarea{width:100%;background:var(--panel-2);border:1px solid var(--line-2);border-radius:3px;color:var(--text);font-family:var(--mono);font-size:13px;padding:8px;outline:none}
    .wl-modal textarea{height:64px;margin-top:6px}
    .wl-modal input:focus,.wl-modal textarea:focus{border-color:var(--amber-dim)}
    .wl-sec{margin-bottom:14px}
    .wl-chips{display:flex;flex-wrap:wrap;gap:6px;max-height:130px;overflow:auto}
    .wl-chip{display:inline-flex;align-items:center;gap:7px;padding:4px 8px;border:1px solid var(--line-2);border-radius:3px;font-size:11px}
    .wl-chip b{color:var(--amber)} .wl-chip .cx{color:var(--muted);cursor:pointer}.wl-chip .cx:hover{color:var(--red)}
    .wl-ta{position:absolute;left:0;right:0;background:#0a0a07;border:1px solid var(--line-2);border-radius:3px;z-index:1700;max-height:170px;overflow:auto;display:none}
    .wl-ta.open{display:block}
    .wl-ta div{padding:6px 9px;cursor:pointer;font-size:11px;border-top:1px solid var(--line)}
    .wl-ta div:hover,.wl-ta div.sel{background:var(--amber-glow)}
    .wl-row2{display:flex;justify-content:space-between;align-items:center;margin-top:10px}
    .wl-danger{border-color:#5a2420;color:var(--red);background:rgba(255,77,66,.08)}
    .wl-x{float:right;color:var(--muted);cursor:pointer;border:0;background:none;font-size:13px}
    .wl-toast{position:fixed;bottom:18px;left:50%;transform:translateX(-50%);background:#0a0a07;border:1px solid var(--amber-dim);color:var(--amber);padding:9px 16px;border-radius:3px;font-size:11px;z-index:2000;display:none}
    .wl-toast.show{display:block}
  `;
  document.head.appendChild(css);

  let toastEl;
  function toast(m) {
    if (!toastEl) { toastEl = document.createElement("div"); toastEl.className = "wl-toast"; document.body.appendChild(toastEl); }
    toastEl.textContent = m; toastEl.classList.add("show");
    clearTimeout(toastEl._t); toastEl._t = setTimeout(() => toastEl.classList.remove("show"), 2200);
  }

  // ---------- state ----------
  let me = { uid: null };
  let lists = [], activeId = null, quotes = {}, monitorOpen = false;
  let sortKey = "sym", sortDir = 1;   // Monitor defaults to A→Z

  async function api(path, opts) {
    const r = await fetch(path, Object.assign({ headers: { "Content-Type": "application/json" } }, opts || {}));
    if (r.status === 401) { me = { uid: null }; renderSidebar(); throw new Error("unauthorized"); }
    if (!r.ok) throw new Error("http " + r.status);
    return r;
  }
  const jget = async (p) => (await api(p)).json();

  function activeList() { return lists.find((l) => l.id === activeId) || null; }

  async function loadLists() {
    try {
      const d = await jget("/api/watchlists");
      lists = d.watchlists || [];
      if (!lists.find((l) => l.id === activeId)) activeId = lists.length ? lists[0].id : null;
      await loadQuotes();
      renderSidebar();
      if (monitorOpen) renderMonitor();
    } catch (e) {}
  }
  async function loadQuotes() {
    const l = activeList(); if (!l || !l.tickers.length) return;
    try {
      const d = await jget("/api/watchlist/quotes?symbols=" + encodeURIComponent(l.tickers.join(",")));
      (d.quotes || []).forEach((q) => (quotes[q.symbol] = q));
    } catch (e) {}
  }
  const createList = (name) => api("/api/watchlists", { method: "POST", body: JSON.stringify({ name }) }).then((r) => r.json());
  const patchList = (id, patch) => api("/api/watchlists/" + id, { method: "PATCH", body: JSON.stringify(patch) }).then((r) => r.json());

  async function setTickers(id, tickers) {
    const updated = await patchList(id, { tickers });
    const i = lists.findIndex((l) => l.id === id); if (i >= 0) lists[i] = updated;
    await loadQuotes(); renderSidebar(); if (monitorOpen) renderMonitor(); if (scrim && scrim.classList.contains("open")) renderManage();
  }
  async function addTicker(sym) {
    const l = activeList(); if (!l) return;
    sym = sym.toUpperCase(); if (l.tickers.includes(sym)) return;
    await setTickers(l.id, l.tickers.concat([sym])); toast(sym + " added to " + l.name);
  }
  async function removeTicker(sym) {
    const l = activeList(); if (!l) return;
    await setTickers(l.id, l.tickers.filter((t) => t !== sym)); toast(sym + " removed");
  }

  // ---------- sidebar ----------
  function renderSidebar() {
    if (!wlSidebar) return;
    if (!me.uid) {
      wlSidebar.innerHTML = `<div class="wl-empty">Sign in to build a watchlist.<br><br><button class="wl-btn amber" data-signin>Sign in</button></div>`;
      const b = wlSidebar.querySelector("[data-signin]"); if (b) b.onclick = () => window.TEK2Auth && window.TEK2Auth.open();
      return;
    }
    const l = activeList();
    const tabs = lists.map((x) => `<div class="wl-tab ${x.id === activeId ? "on" : ""}" data-tab="${esc(x.id)}">${esc(x.name)}</div>`).join("") + `<div class="wl-tab add" data-newlist title="New watchlist">+</div>`;
    let rows = "";
    if (!l) rows = `<div class="wl-empty">Create your first watchlist →</div>`;
    else if (!l.tickers.length) rows = `<div class="wl-empty">Empty. Type a ticker in the bar and ★ it, or use ▤ Watchlist to add.</div>`;
    else rows = [...l.tickers].sort().map((s) => {
      const q = quotes[s] || {};
      return `<div class="wl-item" data-load="${esc(s)}"><span class="wl-sym">${esc(s)}</span><span class="wl-px">${fmtPx(q.price)}</span><span class="wl-chg ${chgCls(q.chg)}">${fmtChg(q.chg)}</span><span class="wl-wx" data-rm="${esc(s)}" title="Remove">✕</span></div>`;
    }).join("");
    wlSidebar.innerHTML = `
      <div class="wl-head">
        <div class="wl-tabs">${tabs}</div>
        <div class="wl-count"><span>${l ? esc(l.name.toUpperCase()) + " WATCHLIST" : "WATCHLISTS"}</span><span>${l ? l.tickers.length + " NAMES" : ""}</span></div>
      </div>
      <div class="wl-list">${rows}</div>
      <div class="wl-foot">
        <div class="wl-btn" data-manage>▤ WATCHLIST</div>
        <div class="wl-btn amber" data-monitor>${monitorOpen ? "▢ CLOSE" : "⤢ MONITOR"}</div>
      </div>`;
    wlSidebar.querySelectorAll("[data-tab]").forEach((t) => (t.onclick = () => { activeId = t.dataset.tab; loadQuotes().then(() => { renderSidebar(); if (monitorOpen) renderMonitor(); }); }));
    wlSidebar.querySelector("[data-newlist]").onclick = newList;
    wlSidebar.querySelectorAll("[data-load]").forEach((r) => (r.onclick = (e) => { if (e.target.dataset.rm) { e.stopPropagation(); removeTicker(e.target.dataset.rm); } else { monitorOpen = false; window.runTerminalCommand("/" + r.dataset.load); renderSidebar(); } }));
    const mb = wlSidebar.querySelector("[data-manage]"); if (mb) mb.onclick = openManage;
    const mo = wlSidebar.querySelector("[data-monitor]"); if (mo) mo.onclick = toggleMonitor;
  }

  async function newList() {
    try { const l = await createList("New list"); lists.push(l); activeId = l.id; renderSidebar(); openManage(); const ni = $("wl-name"); if (ni) { ni.focus(); ni.select(); } }
    catch (e) { toast("Could not create list"); }
  }

  // ---------- manage modal ----------
  let scrim = null;
  function openManage() {
    if (!activeList()) { newList(); return; }
    if (!scrim) buildModal();
    renderManage(); scrim.classList.add("open");
  }
  function closeManage() { if (scrim) scrim.classList.remove("open"); }
  function buildModal() {
    scrim = document.createElement("div"); scrim.className = "wl-scrim";
    scrim.innerHTML = `
      <div class="wl-modal">
        <button class="wl-x" data-x>✕</button>
        <h3>Manage watchlist</h3>
        <div class="wl-sec"><div class="wl-lbl">WATCHLIST NAME</div><input id="wl-name" maxlength="40"></div>
        <div class="wl-sec" style="position:relative">
          <div class="wl-lbl">ADD TICKERS</div>
          <input id="wl-add" placeholder="Type a symbol… (e.g. NVDA)" autocomplete="off">
          <div class="wl-ta" id="wl-ta"></div>
          <textarea id="wl-csv" placeholder="…or paste symbols: AAPL, MSFT, NVDA"></textarea>
          <div class="wl-row2"><input type="file" id="wl-file" accept=".csv,text/csv" style="font-size:11px;width:auto;border:0;padding:0;background:none;color:var(--muted)"><span class="wl-btn amber" id="wl-addbtn">ADD</span></div>
        </div>
        <div class="wl-sec"><div class="wl-lbl">CURRENT TICKERS · <span id="wl-cnt">0</span></div><div class="wl-chips" id="wl-chips"></div></div>
        <div class="wl-row2" style="border-top:1px solid var(--line);padding-top:12px">
          <span class="wl-btn wl-danger" data-del>🗑 DELETE THIS WATCHLIST</span>
          <span class="wl-btn" data-done>DONE</span>
        </div>
      </div>`;
    document.body.appendChild(scrim);
    scrim.addEventListener("click", (e) => { if (e.target === scrim) closeManage(); });
    scrim.querySelector("[data-x]").onclick = closeManage;
    scrim.querySelector("[data-done]").onclick = closeManage;
    scrim.querySelector("[data-del]").onclick = delActive;
    $("wl-name").onchange = () => doRename($("wl-name").value);
    $("wl-addbtn").onclick = doCsvAdd;
    wireAddTypeahead();
  }
  function renderManage() {
    const l = activeList(); if (!l) return;
    $("wl-name").value = l.name;
    $("wl-cnt").textContent = l.tickers.length;
    const box = $("wl-chips"); box.innerHTML = l.tickers.length ? "" : '<span style="color:var(--muted);font-size:11px">No tickers yet.</span>';
    l.tickers.forEach((s) => { const c = document.createElement("span"); c.className = "wl-chip"; c.innerHTML = `<b>${esc(s)}</b><span class="cx" title="Remove">✕</span>`; c.querySelector(".cx").onclick = () => removeTicker(s); box.appendChild(c); });
  }
  async function doRename(name) { name = (name || "").trim(); const l = activeList(); if (!l || !name || name === l.name) { renderManage(); return; } try { const u = await patchList(l.id, { name }); const i = lists.findIndex((x) => x.id === l.id); lists[i] = u; renderSidebar(); renderManage(); if (monitorOpen) renderMonitor(); } catch (e) {} }
  async function delActive() {
    const l = activeList(); if (!l) return;
    if (!confirm(`Delete the "${l.name}" watchlist? This can't be undone.`)) return;
    try { await api("/api/watchlists/" + l.id, { method: "DELETE" }); lists = lists.filter((x) => x.id !== l.id); activeId = lists.length ? lists[0].id : null; closeManage(); await loadQuotes(); renderSidebar(); if (monitorOpen) renderMonitor(); toast("Watchlist deleted"); } catch (e) {}
  }
  function doCsvAdd() {
    const f = $("wl-file").files[0];
    const apply = (text) => { const syms = (text || "").toUpperCase().split(/[^A-Z.]+/).filter(Boolean); const l = activeList(); if (!l) return; const merged = l.tickers.concat(syms.filter((s) => !l.tickers.includes(s))); setTickers(l.id, merged).then(() => { $("wl-csv").value = ""; $("wl-file").value = ""; toast("Added " + (merged.length - l.tickers.length) + " to " + l.name); }); };
    if (f) { const r = new FileReader(); r.onload = () => apply(r.result); r.readAsText(f); } else apply($("wl-csv").value);
  }
  function wireAddTypeahead() {
    const inp = $("wl-add"), ta = $("wl-ta"); let items = [], sel = 0, t = null;
    const close = () => { ta.classList.remove("open"); items = []; };
    inp.addEventListener("input", () => { clearTimeout(t); const q = inp.value.trim(); if (!q) { close(); return; } t = setTimeout(async () => { try { items = await jget("/api/search?q=" + encodeURIComponent(q)); } catch (e) { items = []; } sel = 0; render(); }, 150); });
    function render() { if (!items.length) { close(); return; } ta.innerHTML = items.map((it, i) => `<div class="${i === sel ? "sel" : ""}" data-i="${i}"><b style="color:var(--amber)">${esc(it.symbol)}</b> &nbsp;${esc(it.name)}</div>`).join(""); ta.classList.add("open"); ta.querySelectorAll("[data-i]").forEach((d) => (d.onmousedown = (e) => { e.preventDefault(); pick(items[+d.dataset.i]); })); }
    function pick(it) { if (!it) return; addTicker(it.symbol); inp.value = ""; close(); inp.focus(); }
    inp.addEventListener("keydown", (e) => { if (!items.length) return; if (e.key === "ArrowDown") { e.preventDefault(); sel = (sel + 1) % items.length; render(); } else if (e.key === "ArrowUp") { e.preventDefault(); sel = (sel - 1 + items.length) % items.length; render(); } else if (e.key === "Enter") { e.preventDefault(); pick(items[sel]); } else if (e.key === "Escape") close(); });
    inp.addEventListener("blur", () => setTimeout(close, 150));
  }

  // ---------- monitor ----------
  function openMonitor() { monitorOpen = true; renderMonitor(); renderSidebar(); }
  function toggleMonitor() {
    if (monitorOpen) { monitorOpen = false; renderSidebar(); window.runTerminalCommand("/" + ((window.getCurrentSymbol && window.getCurrentSymbol()) || "AAPL")); }
    else openMonitor();
  }
  function renderMonitor() {
    const l = activeList(); if (!l) { monitorOpen = false; return; }
    const cols = [["sym", "TICKER", "l"], ["name", "COMPANY", "l"], ["price", "LAST"], ["chg", "CHG %"], ["sector", "SECTOR", "l"]];
    let rows = l.tickers.map((s) => Object.assign({ symbol: s }, quotes[s] || {}));
    rows.sort((a, b) => { const x = a[sortKey === "sym" ? "symbol" : sortKey], y = b[sortKey === "sym" ? "symbol" : sortKey]; if (x == null) return 1; if (y == null) return -1; return (typeof x === "string" ? String(x).localeCompare(y) : x - y) * sortDir; });
    const th = cols.map((c) => `<th class="${c[2] === "l" ? "l" : ""} ${c[0] === sortKey ? "on" : ""}" data-k="${c[0]}">${c[1]}${c[0] === sortKey ? (sortDir < 0 ? " ▼" : " ▲") : ""}</th>`).join("") + "<th></th>";
    const body = rows.map((q) => `<tr data-sym="${esc(q.symbol)}"><td class="l t-sym">${esc(q.symbol)}</td><td class="l">${esc(q.name || q.symbol)}</td><td>${fmtPx(q.price)}</td><td class="${chgCls(q.chg)}">${fmtChg(q.chg)}</td><td class="l t-sec">${esc(q.sector || "")}</td><td class="rm" data-rm="${esc(q.symbol)}">✕</td></tr>`).join("") || '<tr><td class="l" colspan="6" style="color:var(--muted)">Empty list.</td></tr>';
    content.innerHTML = `<div class="mon">
      <div class="mon-toolbar"><div class="mon-title">MONITOR · <b>${esc(l.name)}</b></div><span style="flex:1"></span>
        <span class="wl-btn amber" id="m-add">+ ADD</span><span class="wl-btn" id="m-manage">▤ WATCHLIST</span><span class="wl-btn" id="m-export">↓ EXPORT ▾</span></div>
      <div style="overflow:auto"><table><thead><tr>${th}</tr></thead><tbody>${body}</tbody></table></div>
      <div style="padding:9px 16px;color:var(--muted);font-size:10px;letter-spacing:.06em">${l.tickers.length} NAMES · click a row to load · ✕ to remove · click a column to sort</div></div>`;
    content.querySelectorAll("thead th[data-k]").forEach((h) => (h.onclick = () => { const k = h.dataset.k; if (k === sortKey) sortDir *= -1; else { sortKey = k; sortDir = (k === "sym" || k === "name" || k === "sector") ? 1 : -1; } renderMonitor(); }));
    content.querySelectorAll("tbody tr[data-sym]").forEach((r) => (r.onclick = (e) => { if (e.target.dataset.rm) { removeTicker(e.target.dataset.rm); } else { monitorOpen = false; renderSidebar(); window.runTerminalCommand("/" + r.dataset.sym); } }));
    $("m-add").onclick = () => { if (cmd) cmd.focus(); };
    $("m-manage").onclick = openManage;
    $("m-export").onclick = exportMenu;
  }
  function exportMenu(e) {
    closeMenu();
    const l = activeList(); if (!l) return;
    const m = document.createElement("div"); m.className = "wlmenu"; m.id = "wl-expmenu";
    m.innerHTML = `<div data-f="csv">CSV (.csv)</div><div data-f="xls">Excel (.xls)</div>`;
    document.body.appendChild(m);
    const r = e.target.getBoundingClientRect(); m.style.left = r.left + "px"; m.style.top = r.bottom + 4 + "px";
    m.querySelectorAll("div").forEach((d) => (d.onclick = () => { window.open("/api/watchlists/" + l.id + "/export?fmt=" + d.dataset.f, "_blank"); closeMenu(); }));
  }
  function closeMenu() { const m = $("wl-expmenu"); if (m) m.remove(); }
  document.addEventListener("click", (e) => { if (!e.target.closest("#m-export") && !e.target.closest("#wl-expmenu")) closeMenu(); });

  // ---------- command palette ----------
  const CMDS = [
    { code: "/ticker", label: "Summary", sub: "" }, { code: "/ticker inc", label: "Income statement", sub: "inc" },
    { code: "/ticker bal", label: "Balance sheet", sub: "bal" }, { code: "/ticker cf", label: "Cash flow", sub: "cf" },
    { code: "/ticker mgmt", label: "Management / CEO", sub: "mgmt" }, { code: "/ticker filings", label: "SEC filings", sub: "filings" },
    { code: "/ticker news", label: "Recent news", sub: "news" }, { code: "/comp T1 T2", label: "Comparison table", comp: true },
    { code: "/help", label: "Show all commands", help: true },
  ];
  let pal = null, palItems = [], palSel = 0, searchT = null;
  function palEl() { if (!pal) { pal = document.createElement("div"); pal.className = "pal"; document.body.appendChild(pal); } return pal; }
  function place() { const r = cmd.getBoundingClientRect(); const p = palEl(); p.style.left = r.left + "px"; p.style.top = r.bottom + 6 + "px"; p.style.width = Math.max(r.width, 360) + "px"; }
  function closePal() { if (pal) pal.classList.remove("open"); palItems = []; }
  function buildPal(raw) {
    const q = raw.trim(); if (!q) { closePal(); return; }
    const body = (q[0] === "/" ? q.slice(1) : q).trim(); const tok = body.split(/\s+/)[0].toUpperCase();
    let cmds = (q === "/") ? CMDS.slice() : CMDS.filter((c) => c.label.toLowerCase().includes(body.toLowerCase()) || c.code.toLowerCase().includes(body.toLowerCase()) || (c.sub && c.sub.startsWith(body.toLowerCase())));
    palItems = cmds.map((c) => ({ type: "cmd", c }));
    renderPal();
    if (tok && q !== "/") { clearTimeout(searchT); searchT = setTimeout(async () => { let tk = []; try { tk = await jget("/api/search?q=" + encodeURIComponent(tok)); } catch (e) {} palItems = tk.slice(0, 6).map((t) => ({ type: "tk", t })).concat(cmds.map((c) => ({ type: "cmd", c }))); palSel = 0; renderPal(); }, 140); }
  }
  function renderPal() {
    const p = palEl(); if (!palItems.length) { closePal(); return; }
    let html = "", last = "";
    palItems.forEach((it, i) => { if (it.type !== last) { html += `<div class="pal-sec">${it.type === "tk" ? "TICKERS" : "COMMANDS"}</div>`; last = it.type; } if (it.type === "tk") { html += `<div class="pal-row ${i === palSel ? "sel" : ""}" data-i="${i}"><span class="pal-code">${esc(it.t.symbol)}</span><span class="pal-name">${esc(it.t.name)}</span><span class="pal-meta">${esc(it.t.sector || "")}</span></div>`; } else { html += `<div class="pal-row ${i === palSel ? "sel" : ""}" data-i="${i}"><span class="pal-code">${esc(it.c.code)}</span><span class="pal-name">${esc(it.c.label)}</span><span class="pal-meta">⏎</span></div>`; } });
    place(); p.innerHTML = html; p.classList.add("open");
    p.querySelectorAll("[data-i]").forEach((r) => (r.onmousedown = (e) => { e.preventDefault(); palSel = +r.dataset.i; runPal(); }));
  }
  function runPal() {
    const it = palItems[palSel]; closePal(); if (!it) return;
    if (it.type === "tk") { window.runTerminalCommand("/" + it.t.symbol); return; }
    const c = it.c;
    if (c.help) return window.runTerminalCommand("/help");
    if (c.comp) { cmd.value = "/comp "; cmd.focus(); return; }
    const sym = (window.getCurrentSymbol && window.getCurrentSymbol()) || "AAPL";
    window.runTerminalCommand("/" + sym + (c.sub ? " " + c.sub : ""));
  }
  if (cmd) {
    cmd.addEventListener("input", (e) => buildPal(e.target.value));
    cmd.addEventListener("keydown", (e) => {
      if (!pal || !pal.classList.contains("open") || !palItems.length) return;
      if (e.key === "ArrowDown") { e.preventDefault(); palSel = (palSel + 1) % palItems.length; renderPal(); }
      else if (e.key === "ArrowUp") { e.preventDefault(); palSel = (palSel - 1 + palItems.length) % palItems.length; renderPal(); }
      else if (e.key === "Enter") { e.preventDefault(); e.stopPropagation(); runPal(); }
      else if (e.key === "Escape") closePal();
    });
    cmd.addEventListener("blur", () => setTimeout(closePal, 150));
    window.addEventListener("resize", () => { if (pal && pal.classList.contains("open")) place(); });
  }

  // ---------- auth wiring ----------
  function onAuth(m) {
    const was = me.uid; me = m || { uid: null };
    if (me.uid && me.uid !== was) loadLists();
    else if (!me.uid) { lists = []; activeId = null; monitorOpen = false; renderSidebar(); }
    else renderSidebar();
  }
  if (window.TEK2Auth) window.TEK2Auth.onChange(onAuth);
  else document.addEventListener("DOMContentLoaded", () => { renderSidebar(); if (window.TEK2Auth) window.TEK2Auth.onChange(onAuth); });
  renderSidebar();
})();
