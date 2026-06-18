/* TEK2day Finance — table image export (PNG via html2canvas).
 * Adds a "↓ Image" button to a table panel; captures the on-screen panel
 * (title + table + faint Source line) to a PNG — WYSIWYG, on-brand, no server.
 * LOGIN-GATED: a free account is required; signed-out clicks open the sign-up modal. */
(function () {
  const css = document.createElement("style");
  css.textContent = `
    .img-export-btn{margin-left:8px;flex:0 0 auto;font-family:var(--mono);font-size:9px;
      letter-spacing:.08em;text-transform:uppercase;padding:3px 8px;border:1px solid var(--line-2);
      border-radius:3px;color:var(--amber);background:transparent;cursor:pointer;white-space:nowrap}
    .img-export-btn:hover{background:var(--amber);color:#000;border-color:var(--amber)}
    .img-export-btn:disabled{opacity:.6;cursor:wait}
  `;
  document.head.appendChild(css);

  function signedIn() {
    try { const me = window.TEK2Auth && window.TEK2Auth.me(); return !!(me && me.uid); }
    catch (e) { return false; }
  }
  function slug(s) {
    return String(s || "").trim().replace(/[^A-Za-z0-9]+/g, "_").replace(/^_+|_+$/g, "") || "table";
  }
  function download(blob, filename) {
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url; a.download = filename;
    document.body.appendChild(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
  async function capture(node, filename) {
    if (!window.html2canvas) return;
    let bg = "";
    try { bg = getComputedStyle(node).backgroundColor; } catch (e) {}
    if (!bg || bg === "rgba(0, 0, 0, 0)" || bg === "transparent") bg = "#0a0a07";
    const canvas = await window.html2canvas(node, {
      backgroundColor: bg,
      scale: 2,
      logging: false,
      useCORS: true,
      // Don't paint the export button (or anything opted out) into the image.
      ignoreElements: (el) => !!(el.classList && el.classList.contains("no-export")),
    });
    await new Promise((resolve) =>
      canvas.toBlob((blob) => { if (blob) download(blob, filename); resolve(); }, "image/png")
    );
  }
  function attach(panel, opts) {
    opts = opts || {};
    if (!panel || !panel.head || !panel.root) return;
    if (panel.head.querySelector(".img-export-btn")) return;  // already added
    // Excel button (server-side .xlsx download), shown left of the image button.
    if (opts.excelUrl) {
      const xb = document.createElement("button");
      xb.className = "img-export-btn no-export";
      xb.type = "button";
      xb.textContent = "↓ Excel";
      xb.title = "Download this table as an Excel file (free account required)";
      xb.addEventListener("click", (e) => {
        e.preventDefault(); e.stopPropagation();
        if (!signedIn()) {
          if (window.TEK2Auth && window.TEK2Auth.open) window.TEK2Auth.open();
          return;
        }
        window.open(opts.excelUrl, "_blank");
      });
      panel.head.appendChild(xb);
    }
    const btn = document.createElement("button");
    btn.className = "img-export-btn no-export";
    btn.type = "button";
    btn.textContent = "↓ Image";
    btn.title = "Save this table as a PNG image (free account required)";
    btn.addEventListener("click", async (e) => {
      e.preventDefault(); e.stopPropagation();
      if (!signedIn()) {
        if (window.TEK2Auth && window.TEK2Auth.open) window.TEK2Auth.open();
        return;
      }
      const prev = btn.textContent; btn.textContent = "…"; btn.disabled = true;
      try { await capture(panel.root, slug(opts.name) + ".png"); }
      catch (err) { /* leave the table as-is on failure */ }
      btn.textContent = prev; btn.disabled = false;
    });
    panel.head.appendChild(btn);
  }

  window.TableExport = { attach: attach, signedIn: signedIn };
})();
