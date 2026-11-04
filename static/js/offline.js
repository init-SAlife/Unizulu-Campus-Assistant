// "Connection lost" page and "Back online" pop-up.
(function () {
  if (window.showConnectionLost) return;           // loaded twice: do nothing

  const css = `
  .ol-bg {
    position: fixed; inset: 0; z-index: 9999;
    background: rgba(15, 23, 42, .35);
    display: none; align-items: center; justify-content: center;
    padding: 16px; font-family: "Segoe UI", Arial, sans-serif;
  }
  .ol-bg.show { display: flex; animation: olFade .25s ease; }
  .ol-card {
    width: 100%; max-width: 590px; min-height: 460px;
    border-radius: 6px; padding: 52px 40px 28px;
    background: linear-gradient(160deg, #f7fbfe 0%, #e9f1f8 55%, #d6e6f3 100%);
    box-shadow: 0 24px 60px rgba(0, 0, 0, .35);
    display: flex; flex-direction: column; align-items: center; text-align: center;
  }
  .ol-card h2 { font-size: 24px; font-weight: 700; color: #243447; margin: 22px 0 18px; }
  .ol-card p { font-size: 15px; line-height: 1.55; color: #3b4a5c; max-width: 440px; margin: 0; }
  .ol-art { margin: 40px 0 20px; animation: olFloat 3.2s ease-in-out infinite; }
  .ol-shadow {
    width: 70px; height: 8px; border-radius: 50%;
    background: rgba(70, 90, 120, .18); margin-bottom: 44px;
    animation: olShadow 3.2s ease-in-out infinite;
  }
  .ol-status { font-size: 13px; color: #5b6b7d; min-height: 18px; margin: -26px 0 10px; }
  .ol-status.ok { color: #1d7a45; font-weight: 600; }
  .ol-btn {
    margin-top: auto; background: #0b5394; color: #fff; border: none;
    border-radius: 18px; padding: 9px 22px; font-size: 15px; font-weight: 600;
    cursor: pointer; font-family: inherit;
  }
  .ol-btn:hover { background: #0a4a85; }
  .ol-btn:focus-visible { outline: 2px solid #0b5394; outline-offset: 3px; }

  /* "Back online" pop-up */
  .on-pop {
    position: fixed; left: 50%; top: calc(var(--header-h, 0px) + 14px); z-index: 10000;
    transform: translate(-50%, -20px); opacity: 0; pointer-events: none;
    display: flex; align-items: center; gap: 12px;
    width: max-content; max-width: calc(100vw - 32px);
    padding: 12px 14px 12px 12px; border-radius: 10px;
    background: linear-gradient(160deg, #f7fbfe 0%, #e9f1f8 100%);
    border: 1px solid #cfe0ee; border-left: 4px solid #1d7a45;
    box-shadow: 0 14px 34px rgba(0, 0, 0, .22);
    font-family: "Segoe UI", Arial, sans-serif;
    transition: opacity .25s ease, transform .25s ease;
  }
  .on-pop.show { opacity: 1; transform: translate(-50%, 0); pointer-events: auto; }
  .on-ico {
    flex: 0 0 34px; width: 34px; height: 34px; border-radius: 50%;
    background: #dcf3e5; display: flex; align-items: center; justify-content: center;
  }
  .on-txt strong { display: block; font-size: 15px; color: #243447; }
  .on-txt span { font-size: 13px; color: #3b4a5c; }
  .on-x {
    margin-left: 6px; background: none; border: none; cursor: pointer;
    font-size: 20px; line-height: 1; color: #5b6b7d; padding: 2px 6px; border-radius: 6px;
  }
  .on-x:hover { background: rgba(0, 0, 0, .06); }

  /* dark theme of the app */
  [data-theme="dark"] .ol-card,
  [data-theme="dark"] .on-pop { background: linear-gradient(160deg, #1d2a3d 0%, #172234 100%); }
  [data-theme="dark"] .on-pop { border-color: #2c3d55; border-left-color: #3fbf73; }
  [data-theme="dark"] .ol-card h2,
  [data-theme="dark"] .on-txt strong { color: #e8eef6; }
  [data-theme="dark"] .ol-card p,
  [data-theme="dark"] .on-txt span,
  [data-theme="dark"] .ol-status,
  [data-theme="dark"] .on-x { color: #b4c2d3; }
  [data-theme="dark"] .ol-status.ok { color: #5fd38f; }
  [data-theme="dark"] .on-ico { background: #1f4a33; }

  @keyframes olFade { from { opacity: 0 } to { opacity: 1 } }
  @keyframes olFloat { 0%,100% { transform: translateY(0) } 50% { transform: translateY(-8px) } }
  @keyframes olShadow { 0%,100% { transform: scaleX(1); opacity: 1 } 50% { transform: scaleX(.8); opacity: .6 } }
  @media (max-width: 480px) {
    .ol-card { padding: 36px 22px 24px; min-height: 420px; }
    .ol-card h2 { font-size: 21px; }
  }
  @media (prefers-reduced-motion: reduce) {
    .ol-art, .ol-shadow { animation: none; }
    .on-pop { transition: none; }
  }`;

  // warning triangle drawing
  const art = `
  <svg width="130" height="122" viewBox="0 0 130 122" aria-hidden="true">
    <defs>
      <linearGradient id="olGlass" x1="0" y1="0" x2="1" y2="1">
        <stop offset="0" stop-color="#ffffff"/>
        <stop offset="1" stop-color="#e3e9f5"/>
      </linearGradient>
      <linearGradient id="olEdge" x1="0" y1="0" x2="1" y2="1">
        <stop offset="0" stop-color="#7dd3fc"/>
        <stop offset=".5" stop-color="#a78bfa"/>
        <stop offset="1" stop-color="#f472b6"/>
      </linearGradient>
      <linearGradient id="olMark" x1="0" y1="0" x2="0" y2="1">
        <stop offset="0" stop-color="#c026d3"/>
        <stop offset=".6" stop-color="#f43f5e"/>
        <stop offset="1" stop-color="#fb923c"/>
      </linearGradient>
    </defs>
    <path d="M70 12 Q65 4 60 12 L9 100 Q5 110 16 110 L116 110 Q126 110 121 100 Z"
          transform="translate(5 5)" fill="#c7d7ee" opacity=".7"/>
    <path d="M70 12 Q65 4 60 12 L9 100 Q5 110 16 110 L116 110 Q126 110 121 100 Z"
          fill="url(#olGlass)" stroke="url(#olEdge)" stroke-width="3" stroke-linejoin="round"/>
    <rect x="58" y="34" width="14" height="44" rx="7" fill="url(#olMark)"/>
    <circle cx="65" cy="92" r="7.5" fill="url(#olMark)"/>
  </svg>`;

  // green tick for the "Back online" pop-up
  const tick = `
  <svg width="18" height="18" viewBox="0 0 24 24" aria-hidden="true">
    <path d="M5 12.5l4.2 4.2L19 7" fill="none" stroke="#1d7a45" stroke-width="3"
          stroke-linecap="round" stroke-linejoin="round"/>
  </svg>`;

  const style = document.createElement("style");
  style.textContent = css;
  document.head.appendChild(style);

  // "Connection lost" card
  const bg = document.createElement("div");
  bg.className = "ol-bg";
  bg.setAttribute("role", "alertdialog");
  bg.setAttribute("aria-modal", "true");
  bg.setAttribute("aria-labelledby", "olTitle");
  bg.innerHTML = `
    <div class="ol-card">
      <h2 id="olTitle">Connection lost</h2>
      <p>Without an internet connection, Campus AI Assistant cannot answer your questions,
         load handbook information or show routes around campus.</p>
      <div class="ol-art">${art}</div>
      <div class="ol-shadow"></div>
      <div class="ol-status" id="olStatus">Waiting for your connection to come back</div>
      <button class="ol-btn" id="olBtn" type="button">Got it</button>
    </div>`;
  document.body.appendChild(bg);

  // "Back online" pop-up
  const pop = document.createElement("div");
  pop.className = "on-pop";
  pop.setAttribute("role", "status");
  pop.setAttribute("aria-live", "polite");
  pop.innerHTML = `
    <div class="on-ico">${tick}</div>
    <div class="on-txt">
      <strong>Back online</strong>
      <span>Your connection is back. You can carry on where you left off.</span>
    </div>
    <button class="on-x" type="button" aria-label="Close">&times;</button>`;
  document.body.appendChild(pop);

  const status = bg.querySelector("#olStatus");
  const btn = bg.querySelector("#olBtn");
  let dismissed = false;     // "Got it" pressed for this drop
  let wentOffline = false;   // only say "Back online" after we were really offline
  let lastFocus = null;
  let popTimer = null, hideTimer = null;

  function show() {
    if (dismissed) return;
    clearTimeout(hideTimer);
    hidePop();
    status.textContent = "Waiting for your connection to come back";
    status.className = "ol-status";
    if (!bg.classList.contains("show")) lastFocus = document.activeElement;
    bg.classList.add("show");
    btn.focus();
  }

  function hide() {
    bg.classList.remove("show");
    if (lastFocus && lastFocus.focus) { try { lastFocus.focus(); } catch (e) {} }
    lastFocus = null;
  }

  function showPop() {
    clearTimeout(popTimer);
    pop.classList.add("show");
    popTimer = setTimeout(hidePop, 4500);
  }

  function hidePop() {
    clearTimeout(popTimer);
    pop.classList.remove("show");
  }

  // dismiss until the connection drops again
  btn.onclick = () => { dismissed = true; hide(); };
  pop.querySelector(".on-x").onclick = hidePop;

  // keep the keyboard inside the card while it is open
  bg.addEventListener("keydown", (e) => {
    if (e.key === "Tab") { e.preventDefault(); btn.focus(); }
    if (e.key === "Escape") { dismissed = true; hide(); }
  });

  window.addEventListener("offline", () => {
    wentOffline = true;
    dismissed = false;
    show();
  });

  window.addEventListener("online", () => {
    if (!wentOffline) return;
    wentOffline = false;
    if (bg.classList.contains("show")) {
      status.textContent = "You are back online";
      status.className = "ol-status ok";
      hideTimer = setTimeout(() => { hide(); showPop(); }, 1200);
    } else {
      showPop();
    }
  });

  if (!navigator.onLine) { wentOffline = true; show(); }

  // lets other scripts open it, e.g. when a fetch fails
  window.showConnectionLost = () => { wentOffline = true; dismissed = false; show(); };
})();
