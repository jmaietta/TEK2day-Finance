/* TEK2day Finance — Firebase sign-in (front-end).
 * Loads the web config from /firebase/config, signs in with the Firebase JS SDK
 * (Google + email/password), posts the ID token to /auth/login (server sets an
 * HTTPOnly session cookie). Browsing works signed-out; this only gates watchlist
 * + export later. No Kilby code. */
(function () {
  let cfg = null, configured = false, fbReady = false;
  let _me = { uid: null }, _onChange = null;
  const slot = () => document.getElementById("auth-slot");

  const css = document.createElement("style");
  css.textContent = `
    .auth-slot{display:flex;align-items:center;gap:8px;margin-left:10px}
    .auth-btn{font-family:var(--mono);font-size:11px;letter-spacing:.05em;padding:5px 11px;border:1px solid var(--amber-dim);border-radius:3px;color:var(--amber);background:var(--amber-glow);cursor:pointer;white-space:nowrap}
    .auth-btn:hover{background:var(--amber);color:#000}
    .auth-email{color:var(--muted);font-size:10px;max-width:170px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
    .auth-link{color:var(--muted);font-size:10px;cursor:pointer;border:0;background:none;font-family:var(--mono)}
    .auth-link:hover{color:var(--amber)}
    .auth-scrim{position:fixed;inset:0;background:rgba(0,0,0,.6);display:none;align-items:center;justify-content:center;z-index:1000}
    .auth-scrim.open{display:flex}
    .auth-modal{width:340px;max-width:92vw;background:var(--panel,#070705);border:1px solid var(--line-2,#2a2a20);border-radius:4px;padding:20px;font-family:var(--mono)}
    .auth-modal h3{font-family:var(--sans,sans-serif);font-size:15px;color:var(--text,#e8e6df);margin:0 0 4px}
    .auth-modal p{color:var(--muted,#8a8878);font-size:11px;margin:0 0 14px;line-height:1.5}
    .auth-google{width:100%;display:flex;align-items:center;justify-content:center;gap:8px;padding:9px;border:1px solid var(--line-2,#2a2a20);border-radius:3px;background:#fff;color:#1f1f1f;font-weight:600;font-size:12px;cursor:pointer}
    .auth-or{display:flex;align-items:center;gap:8px;color:var(--muted);font-size:10px;margin:14px 0}
    .auth-or::before,.auth-or::after{content:"";flex:1;height:1px;background:var(--line,#1c1b14)}
    .auth-modal input{width:100%;background:var(--panel-2,#0c0c09);border:1px solid var(--line-2,#2a2a20);border-radius:3px;color:var(--text,#e8e6df);font-family:var(--mono);font-size:13px;padding:9px;margin-bottom:8px;outline:none}
    .auth-modal input:focus{border-color:var(--amber-dim,#b97a14)}
    .auth-primary{width:100%;padding:9px;border:1px solid var(--amber-dim,#b97a14);border-radius:3px;background:var(--amber,#ff9f1c);color:#000;font-weight:700;font-size:12px;letter-spacing:.04em;cursor:pointer}
    .auth-rowx{display:flex;justify-content:space-between;margin-top:10px}
    .auth-err{color:var(--red,#ff4d42);font-size:11px;min-height:14px;margin-top:8px}
    .auth-close{float:right;color:var(--muted);cursor:pointer;border:0;background:none;font-size:13px}
  `;
  document.head.appendChild(css);

  async function getJSON(path, opts) {
    const r = await fetch(path, Object.assign({ headers: { "Content-Type": "application/json" } }, opts || {}));
    return r;
  }

  async function refreshState() {
    let me = { uid: null };
    try { me = await (await fetch("/auth/me")).json(); } catch (e) {}
    _me = me;
    renderSlot(me);
    if (_onChange) { try { _onChange(me); } catch (e) {} }
  }

  // Integration API for watchlist.js
  window.TEK2Auth = {
    open: openModal,
    refresh: refreshState,
    me: () => _me,
    onChange: (cb) => { _onChange = cb; if (_me) { try { cb(_me); } catch (e) {} } },
  };

  function renderSlot(me) {
    const el = slot(); if (!el) return;
    el.innerHTML = "";
    if (me && me.uid) {
      const email = document.createElement("span");
      email.className = "auth-email"; email.textContent = me.email || "Signed in";
      const out = document.createElement("button");
      out.className = "auth-link"; out.textContent = "Sign out"; out.onclick = signOut;
      el.append(email, out);
    } else {
      const inb = document.createElement("button");
      inb.className = "auth-btn"; inb.textContent = "Sign in"; inb.onclick = openModal;
      el.append(inb);
    }
  }

  async function ensureFirebase() {
    if (fbReady) return true;
    if (cfg === null) {
      try {
        const c = await (await fetch("/firebase/config")).json();
        configured = !!c.configured; cfg = c.firebaseConfig || false;
      } catch (e) { configured = false; cfg = false; }
    }
    if (!configured || !window.firebase) return false;
    if (!window.firebase.apps.length) window.firebase.initializeApp(cfg);
    fbReady = true;
    return true;
  }

  async function postToken(user, errEl) {
    const idToken = await user.getIdToken();
    const r = await getJSON("/auth/login", { method: "POST", body: JSON.stringify({ token: idToken }) });
    if (!r.ok) { if (errEl) errEl.textContent = "Sign-in failed on the server."; return false; }
    return true;
  }

  let scrim = null;
  function buildModal() {
    scrim = document.createElement("div"); scrim.className = "auth-scrim";
    scrim.innerHTML = `
      <div class="auth-modal">
        <button class="auth-close" data-x>&#10005;</button>
        <h3>Sign in to TEK2day&nbsp;Finance</h3>
        <button class="auth-google" data-google>Sign in with Google</button>
        <div class="auth-or">or</div>
        <input type="email" placeholder="Email" data-email autocomplete="username">
        <input type="password" placeholder="Password" data-pass autocomplete="current-password">
        <button class="auth-primary" data-signin>Sign in</button>
        <div class="auth-rowx">
          <button class="auth-link" data-create>Create account</button>
          <button class="auth-link" data-reset>Forgot password?</button>
        </div>
        <div class="auth-err" data-err></div>
      </div>`;
    document.body.appendChild(scrim);
    const q = (s) => scrim.querySelector(s);
    const err = q("[data-err]");
    scrim.addEventListener("click", (e) => { if (e.target === scrim) closeModal(); });
    q("[data-x]").onclick = closeModal;
    q("[data-google]").onclick = () => doGoogle(err);
    q("[data-signin]").onclick = () => doEmail("signin", q, err);
    q("[data-create]").onclick = () => doEmail("create", q, err);
    q("[data-reset]").onclick = () => doReset(q, err);
  }

  async function openModal() {
    if (!scrim) buildModal();
    const err = scrim.querySelector("[data-err]"); err.textContent = "";
    const ok = await ensureFirebase();
    if (!ok) err.textContent = "Sign-in isn't configured yet (add Firebase config to .env).";
    scrim.classList.add("open");
  }
  function closeModal() { if (scrim) scrim.classList.remove("open"); }

  async function doGoogle(err) {
    if (!(await ensureFirebase())) return;
    try {
      const provider = new window.firebase.auth.GoogleAuthProvider();
      const res = await window.firebase.auth().signInWithPopup(provider);
      if (await postToken(res.user, err)) { closeModal(); refreshState(); }
    } catch (e) { err.textContent = humanize(e); }
  }
  async function doEmail(mode, q, err) {
    if (!(await ensureFirebase())) return;
    const email = q("[data-email]").value.trim(), pass = q("[data-pass]").value;
    if (!email || !pass) { err.textContent = "Enter email and password."; return; }
    try {
      const a = window.firebase.auth();
      const res = mode === "create"
        ? await a.createUserWithEmailAndPassword(email, pass)
        : await a.signInWithEmailAndPassword(email, pass);
      if (await postToken(res.user, err)) { closeModal(); refreshState(); }
    } catch (e) { err.textContent = humanize(e); }
  }
  async function doReset(q, err) {
    if (!(await ensureFirebase())) return;
    const email = q("[data-email]").value.trim();
    if (!email) { err.textContent = "Enter your email above to reset."; return; }
    try { await window.firebase.auth().sendPasswordResetEmail(email); err.textContent = "Reset email sent."; }
    catch (e) { err.textContent = humanize(e); }
  }
  async function signOut() {
    try { if (window.firebase && window.firebase.apps && window.firebase.apps.length) await window.firebase.auth().signOut(); } catch (e) {}
    try { await getJSON("/auth/logout", { method: "POST" }); } catch (e) {}
    refreshState();
  }
  function humanize(e) {
    const m = (e && e.code) || (e && e.message) || "Sign-in error";
    return String(m).replace("auth/", "").replace(/[-_]/g, " ");
  }

  if (document.readyState !== "loading") refreshState();
  else document.addEventListener("DOMContentLoaded", refreshState);
})();
