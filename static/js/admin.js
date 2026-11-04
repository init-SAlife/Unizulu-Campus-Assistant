// the admin dashboard (/admin), from the campus_ai_assistant admin package
(function(){
  const $ = id => document.getElementById(id);
  const css = n => getComputedStyle(document.documentElement).getPropertyValue(n).trim();
  const esc = t => String(t === null || t === undefined ? "" : t)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;").replace(/'/g, "&#39;");
  const pad = n => String(n).padStart(2, "0");
  const clock = d => pad(d.getHours()) + ":" + pad(d.getMinutes());
  const stamp = d => d.toLocaleDateString("en-ZA", { day: "numeric", month: "short", year: "numeric" }) + " " + clock(d);
  function when(iso){
    if(!iso) return "-";
    const d = new Date(iso), now = new Date();
    const yesterday = new Date(now); yesterday.setDate(now.getDate() - 1);
    if(d.toDateString() === now.toDateString()) return "Today " + clock(d);
    if(d.toDateString() === yesterday.toDateString()) return "Yesterday " + clock(d);
    return stamp(d);
  }
  function duration(a, b){
    const m = Math.max(0, Math.round((b - a) / 60000));
    return m >= 60 ? Math.floor(m / 60) + "h " + (m % 60) + "m" : m + "m";
  }
  function toast(text){
    const t = $("toast"); t.textContent = text; t.hidden = false;
    clearTimeout(toast.timer); toast.timer = setTimeout(() => { t.hidden = true; }, 2600);
  }

  // every call to the admin api; an ended session or a lost admin role is handled here
  async function api(url, options = {}){
    const res = await fetch(url, { credentials: "same-origin", ...options });
    const data = await res.json().catch(() => ({}));
    if(res.status === 401){
      if(data.code === "session_ended"){ window.location.reload(); }          // /admin opens a new session
      else { window.location.href = "/login?next=/admin"; }
      throw new Error("signed out");
    }
    if(!res.ok) throw new Error(data.error || "Something went wrong.");
    return data;
  }
  const post = (url, body) => api(url, { method: "POST", headers: { "Content-Type": "application/json" },
                                         body: JSON.stringify(body || {}) });

  // state
  const titles = { dashboard: "Overview", users: "User Management", requests: "Admin Requests", feedback: "Feedback Inbox",
                   sessions: "Admin Sessions", audit: "Audit Log" };
  let currentTab = "dashboard";
  let overview = null, users = [], requests = [], feedback = [], sessions = [], audit = [], current = null;

  // charts
  // If Chart.js could not load, the charts are left empty but the rest of the
  // dashboard (numbers, users, admin requests, feedback...) still works.
  if(typeof window.Chart === "undefined"){
    console.warn("[admin] Chart.js did not load: the charts are hidden.");
    window.Chart = function(canvas){
      if(canvas && canvas.parentElement) canvas.parentElement.innerHTML = '<p class="empty">Chart could not be loaded.</p>';
      this.data = { labels: [], datasets: [{}, {}] };
      this.options = { scales: { x: { ticks: {} }, y: { ticks: {}, grid: {} } } };
    };
    window.Chart.prototype.update = function(){};
    window.Chart.prototype.resize = function(){};
  }
  const lineChart = new Chart($("lineChart"), {
    type: "line",
    data: { labels: [], datasets: [
      { data: [], borderColor: css("--a-orange"), backgroundColor: "rgba(245,163,92,.15)",
        fill: true, tension: .4, pointRadius: 4, pointBackgroundColor: css("--a-orange") },
      { data: [], borderColor: css("--text-dim"), tension: .4, pointRadius: 0, borderWidth: 2 }
    ] },
    options: { maintainAspectRatio: false, plugins: { legend: { display: false } },
      scales: { x: { grid: { display: false }, ticks: { color: css("--text-dim") } },
                y: { grid: { color: css("--border") }, ticks: { color: css("--text-dim"), precision: 0 }, beginAtZero: true } } }
  });
  const ringChart = new Chart($("ringChart"), {
    type: "doughnut",
    data: { datasets: [{ data: [0, 1], backgroundColor: [css("--a-chart"), css("--surface-2")], borderWidth: 0 }] },
    options: { cutout: "78%", plugins: { legend: { display: false }, tooltip: { enabled: false } } }
  });

  // dashboard
  function change(now, before, unit){
    if(now === null || now === undefined) return ["", ""];
    if(before === null || before === undefined || before === 0){
      return now ? ["up", "▲ new this period"] : ["", "no change"];
    }
    if(unit === "pts"){
      const d = now - before;
      return [d >= 0 ? "up" : "down", (d >= 0 ? "▲ " : "▼ ") + Math.abs(d) + " pts"];
    }
    const pct = (now - before) / before * 100;
    return [pct >= 0 ? "up" : "down", (pct >= 0 ? "▲ " : "▼ ") + Math.abs(pct).toFixed(0) + "%"];
  }
  function setStat(id, value, delta){
    $("s" + id).textContent = value;
    const [cls, text] = delta;
    const el = $("d" + id); el.className = cls; el.textContent = text;
  }

  function renderOverview(){
    const o = overview, n = o.stats.now, b = o.stats.before;
    fillProfile(o.me);
    $("lActive").textContent = "Active users";
    $("lChats").textContent = "Questions asked";
    setStat("Users", o.stats.total_users.toLocaleString(), n.new_users ? ["up", "▲ " + n.new_users + " new, " + o.range.label] : ["", "no new users, " + o.range.label]);
    setStat("Active", n.active.toLocaleString(), change(n.active, b.active));
    setStat("Chats", n.questions.toLocaleString(), change(n.questions, b.questions));
    setStat("Helpful", n.helpful === null ? "–" : n.helpful + "%", n.helpful === null ? ["", "no ratings yet"] : change(n.helpful, b.helpful, "pts"));
    setStat("Reg", n.registration.toLocaleString(), change(n.registration, b.registration));
    setStat("Routes", n.routes.toLocaleString(), change(n.routes, b.routes));

    // questions per day (or week) against the period before
    const byDay = o.range.bucket === "day";
    lineChart.data.labels = o.series_labels.map(d => {
      const x = new Date(d + "T00:00:00");
      return byDay ? (o.range.days <= 7 ? x.toLocaleDateString("en-ZA", { weekday: "short" }) : x.getDate() + " " + x.toLocaleDateString("en-ZA", { month: "short" }))
                   : "Wk " + x.getDate() + " " + x.toLocaleDateString("en-ZA", { month: "short" });
    });
    lineChart.data.datasets[0].data = o.series;
    lineChart.data.datasets[1].data = o.prev_series.slice(-o.series.length);
    lineChart.update();
    $("lineSub").textContent = "AI Assistant questions per " + (byDay ? "day" : "week") + ", " + o.range.label;
    $("legNow").textContent = o.range.label.charAt(0).toUpperCase() + o.range.label.slice(1);
    $("legPrev").textContent = "Period before";

    // answer feedback ring (all time)
    const f = o.feedback, total = f.up + f.down;
    ringChart.data.datasets[0].data = total ? [f.up, f.down] : [0, 1];
    ringChart.update();
    $("ringPct").textContent = total ? Math.round(f.up / total * 100) + "%" : "–";
    $("upCount").textContent = f.up; $("downCount").textContent = f.down; $("openReports").textContent = f.open;
    setOpenCount(f.open);
    setReqCount(o.pending_admin_requests || 0);

    $("recentFeedback").innerHTML = o.recent_feedback.length
      ? o.recent_feedback.map(x => feedbackHtml(x, false)).join("")
      : '<p class="empty">No "Bad response" ratings yet.</p>';
    $("recentAudit").innerHTML = o.recent_audit.length
      ? o.recent_audit.map(a => auditRow(a, false)).join("")
      : '<tr><td colspan="3" class="empty">Nothing yet.</td></tr>';

    history = o.history;
    allYears = [...new Set(Object.values(history).flatMap(h => Object.keys(h)))].map(Number).sort();
    yearSelect.innerHTML = allYears.slice().reverse().map(y => `<option>${y}</option>`).join("");
    renderYears();
  }
  function setOpenCount(n){
    $("openCount").textContent = n; $("openCount").hidden = !n;
    $("bellDot").hidden = !n;
    $("bellBtn").title = n ? n + " open feedback report" + (n === 1 ? "" : "s") : "No open feedback";
  }

  // performance throughout the years
  const months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  let history = {}, allYears = [], metric = "questions", view = "years";
  const isPercent = m => m === "helpful";
  const yearSelect = $("yearSelect");
  const yearChart = new Chart($("yearChart"), {
    data: { labels: [], datasets: [
      { type: "bar", data: [], backgroundColor: css("--a-chart"), borderRadius: 6, maxBarThickness: 46 },
      { type: "line", data: [], borderColor: css("--a-orange"), tension: .4, pointRadius: 3,
        pointBackgroundColor: css("--a-orange"), borderWidth: 2 }
    ] },
    options: { maintainAspectRatio: false, plugins: { legend: { display: false } },
      scales: { x: { grid: { display: false }, ticks: { color: css("--text-dim") } },
                y: { grid: { color: css("--border") }, ticks: { color: css("--text-dim") }, beginAtZero: true } } }
  });
  function reduceYear(arr, m){
    const vals = (arr || []).filter(v => v !== null && v !== undefined);
    if(!vals.length) return null;
    const s = vals.reduce((a, b) => a + b, 0);
    return isPercent(m) ? s / vals.length : s;
  }
  const ytd = (arr, upto, m) => reduceYear((arr || []).slice(0, upto), m);
  const setText = (id, t) => { $(id).textContent = t; };
  function setChange(pct){
    const ch = $("ysChange");
    if(pct === null){ ch.textContent = "New this year"; ch.className = "up"; return; }
    ch.textContent = (pct >= 0 ? "▲ " : "▼ ") + Math.abs(pct).toFixed(1) + "%";
    ch.className = pct >= 0 ? "up" : "down";
  }
  function renderYears(){
    const fmt = v => v === null || v === undefined ? "-" : isPercent(metric) ? Math.round(v) + "%" : Math.round(v).toLocaleString();
    const h = history[metric] || {};
    const have = Object.keys(h).map(Number).sort().map(y => [y, reduceYear(h[y], metric)]).filter(x => x[1] !== null);
    document.querySelectorAll("#metricTabs button").forEach(b => b.classList.toggle("on", b.dataset.metric === metric));
    document.querySelectorAll("#viewTabs button").forEach(b => b.classList.toggle("on", b.dataset.view === view));
    yearSelect.style.display = view === "months" ? "" : "none";
    if(!allYears.length || !have.length){
      yearChart.data.labels = []; yearChart.data.datasets.forEach(d => { d.data = []; }); yearChart.update();
      ["ysTotal", "ysAvg", "ysBest", "ysChange"].forEach(id => setText(id, "-"));
      $("ysChange").className = "";
      setText("yearSub", "No data recorded for this yet");
      return;
    }
    const latest = allYears[allYears.length - 1];
    const doneMonths = h[latest] ? h[latest].filter(v => v !== null).length : 12;
    if(view === "years"){
      yearChart.data.labels = allYears.map(y => (y === latest && doneMonths < 12) ? y + " (to date)" : String(y));
      yearChart.data.datasets[0].data = allYears.map(y => h[y] ? reduceYear(h[y], metric) : 0);
      yearChart.data.datasets[1].data = [];
      $("legBWrap").style.display = "none";
      setText("legA", isPercent(metric) ? "Year average" : "Year total");
      setText("yearSub", "Totals for every year since " + allYears[0]);
      const best = have.reduce((a, b) => (b[1] > a[1] ? b : a));
      const grand = have.reduce((a, [, v]) => a + v, 0);
      setText("ysTotalLabel", isPercent(metric) ? "Average all years" : "Total all years");
      setText("ysTotal", fmt(isPercent(metric) ? grand / have.length : grand));
      setText("ysAvgLabel", "Yearly average"); setText("ysAvg", fmt(grand / have.length));
      setText("ysBestLabel", "Best year"); setText("ysBest", best[0] + " · " + fmt(best[1]));
      setText("ysChangeLabel", latest + " vs " + (latest - 1) + " (same months)");
      const a = ytd(h[latest], doneMonths, metric), b = h[latest - 1] ? ytd(h[latest - 1], doneMonths, metric) : null;
      setChange(b ? ((a - b) / b) * 100 : null);
    } else {
      const y = Number(yearSelect.value) || latest;
      const cur = h[y] || Array(12).fill(null), prev = h[y - 1];
      yearChart.data.labels = months;
      yearChart.data.datasets[0].data = cur;
      yearChart.data.datasets[1].data = prev || [];
      $("legBWrap").style.display = prev ? "" : "none";
      setText("legA", String(y)); setText("legB", String(y - 1));
      setText("yearSub", "Monthly figures for " + y + (prev ? " compared with " + (y - 1) : ""));
      const done = cur.map((v, i) => [v, i]).filter(([v]) => v !== null);
      const total = reduceYear(cur, metric);
      const best = done.length ? done.reduce((a, b) => (b[0] > a[0] ? b : a)) : null;
      setText("ysTotalLabel", isPercent(metric) ? "Year average" : "Total " + y); setText("ysTotal", fmt(total));
      setText("ysAvgLabel", "Monthly average");
      setText("ysAvg", fmt(done.length ? done.reduce((a, [v]) => a + v, 0) / done.length : null));
      setText("ysBestLabel", "Best month"); setText("ysBest", best ? months[best[1]] + " · " + fmt(best[0]) : "-");
      setText("ysChangeLabel", "Change vs " + (y - 1));
      const b = prev ? ytd(prev, done.length, metric) : null;
      if(total === null){ setText("ysChange", "No data"); $("ysChange").className = ""; }
      else setChange(b ? ((total - b) / b) * 100 : null);
    }
    yearChart.options.scales.y.max = isPercent(metric) ? 100 : undefined;
    yearChart.update();
  }
  document.querySelectorAll("#metricTabs button").forEach(b => b.onclick = () => { metric = b.dataset.metric; renderYears(); });
  document.querySelectorAll("#viewTabs button").forEach(b => b.onclick = () => { view = b.dataset.view; renderYears(); });
  yearSelect.onchange = renderYears;

  // feedback inbox
  const icon = { up: "👍", down: "👎" };
  function feedbackHtml(f, withActions){
    const status = f.status === "open" ? "Open" : "Resolved";
    const resolved = f.status === "done" && f.type === "down" && f.resolved_by
      ? ` · resolved by ${esc(f.resolved_by)} ${esc(when(f.resolved_at))}` : "";
    return `<div class="fb-item">
      <div class="fb-ico">${icon[f.type] || "⚠️"}</div>
      <div style="flex:1;min-width:0">
        ${f.question ? `<p class="q">${esc(f.question)}</p>` : ""}
        <p class="a">${esc(String(f.answer || "").replace(/\*\*(.+?)\*\*/g, "$1"))}</p>
        <small>${esc(f.user)} · ${esc(when(f.at))}<span class="chip ${f.status}">${status}</span>${resolved}</small>
      </div>
      ${withActions && f.status === "open" ? `<button class="btn sm" data-resolve="${f.id}">Mark resolved</button>` : ""}
    </div>`;
  }
  function renderFeedback(){
    const s = $("fbFilter").value, q = $("fbSearch").value.toLowerCase();
    const list = feedback.filter(f => (!s || f.status === s) && (f.question + " " + f.answer + " " + f.user).toLowerCase().includes(q));
    $("allFeedback").innerHTML = list.length ? list.map(f => feedbackHtml(f, true)).join("")
      : `<p class="empty">${s === "open" ? "No open feedback. Everything has been looked at." : "No feedback matches."}</p>`;
  }
  $("allFeedback").onclick = async e => {
    const btn = e.target.closest("[data-resolve]");
    if(!btn) return;
    btn.disabled = true;
    try{
      await post(`/admin/api/feedback/${btn.dataset.resolve}/resolve`);
      const f = feedback.find(x => String(x.id) === btn.dataset.resolve);
      if(f){ f.status = "done"; f.resolved_by = overview ? overview.me.email : ""; f.resolved_at = new Date().toISOString(); }
      const open = feedback.filter(x => x.status === "open").length;
      setOpenCount(open); $("openReports").textContent = open;
      renderFeedback(); refreshCurrent();
      toast("Feedback marked as resolved.");
    } catch(err){ if(err.message !== "signed out"){ toast(err.message); btn.disabled = false; } }
  };

  // admin requests
  function setReqCount(n){ $("reqCount").textContent = n; $("reqCount").hidden = !n; }
  const reqLabel = { pending: "Waiting", approved: "Approved", rejected: "Rejected" };
  function renderRequests(){
    const s = $("reqFilter").value, q = $("reqSearch").value.toLowerCase();
    const list = requests.filter(r => (!s || r.status === s) && (r.name + " " + r.email + " " + r.reason).toLowerCase().includes(q));
    $("reqList").innerHTML = list.length ? list.map(r => {
      const initial = esc((r.name || "?").trim().charAt(0).toUpperCase());
      const decided = r.status !== "pending" && r.decided_by
        ? ` · ${r.status === "approved" ? "approved" : "rejected"} by ${esc(r.decided_by)} ${esc(when(r.decided_at))}` : "";
      return `<div class="req-item">
        <div class="req-avatar">${initial}</div>
        <div class="req-body">
          <p class="req-name">${esc(r.name)} <span class="chip ${esc(r.status)}">${reqLabel[r.status] || esc(r.status)}</span></p>
          <p class="req-email">${esc(r.email)}${r.disabled ? ' <span class="chip off">Disabled</span>' : ""}</p>
          <p class="req-reason"><b>Role / reason:</b> ${esc(r.reason || "-")}</p>
          <small>Asked ${esc(when(r.requested_at))} · email confirmed ${esc(when(r.verified_at))}${decided}</small>
        </div>
        ${r.status === "pending" ? `<div class="req-actions">
          <button class="btn sm primary" data-approve="${r.id}">Approve</button>
          <button class="btn sm danger" data-reject="${r.id}">Reject</button>
        </div>` : ""}
      </div>`;
    }).join("") : `<p class="empty">${s === "pending" ? "No requests are waiting. New ones show here once the person confirms their email." : "No requests match."}</p>`;
  }
  $("reqList").onclick = async e => {
    const btn = e.target.closest("[data-approve],[data-reject]");
    if(!btn) return;
    const approve = !!btn.dataset.approve, id = btn.dataset.approve || btn.dataset.reject;
    const r = requests.find(x => String(x.id) === id);
    if(!r) return;
    const msg = approve
      ? `Make ${r.name} (${r.email}) an admin? They will be able to see usage, manage users and read feedback.`
      : `Reject ${r.name}'s request? Their account stays a normal student account.`;
    if(!confirm(msg)) return;
    btn.closest(".req-actions").querySelectorAll("button").forEach(b => b.disabled = true);
    try{
      const res = await post(`/admin/api/admin-requests/${id}/${approve ? "approve" : "reject"}`);
      r.status = res.status; r.decided_by = overview ? overview.me.email : ""; r.decided_at = new Date().toISOString();
      setReqCount(res.pending);
      renderRequests(); refreshCurrent();
      toast(approve ? `${r.name} is now an admin.` : `${r.name}'s request was rejected.`);
    } catch(err){
      if(err.message === "signed out") return;
      toast(err.message);
      load("requests");
    }
  };

  // users
  function renderUsers(){
    const q = $("userSearch").value.toLowerCase(), r = $("roleFilter").value, st = $("statusFilter").value;
    const list = users.filter(u => (!r || u.role === r) && (!st || (st === "active") === u.active)
                                  && (u.name + " " + u.email).toLowerCase().includes(q));
    $("userRows").innerHTML = list.length ? list.map(u => `<tr>
        <td>${esc(u.name)}</td><td>${esc(u.email)}</td>
        <td><span class="chip ${u.role === "admin" ? "admin" : "student"}">${esc(u.role)}</span></td>
        <td><span class="chip ${u.active ? "done" : "off"}">${u.active ? "Active" : "Disabled"}</span></td>
        <td>${u.chats}</td><td>${esc(when(u.last))}</td>
        <td>${u.me ? '<span class="empty">You</span>'
          : `<button class="btn sm ${u.active ? "danger" : ""}" data-toggle-user="${u.id}">${u.active ? "Disable" : "Enable"}</button>`}</td>
      </tr>`).join("") : '<tr><td colspan="7" class="empty">No users match.</td></tr>';
  }
  $("userRows").onclick = async e => {
    const btn = e.target.closest("[data-toggle-user]");
    if(!btn) return;
    const u = users.find(x => String(x.id) === btn.dataset.toggleUser);
    if(!u) return;
    if(u.active && !confirm(`Disable ${u.name}? They will be signed out and cannot sign in until enabled again.`)) return;
    btn.disabled = true;
    try{
      const r = await post(`/admin/api/users/${u.id}/status`, { disabled: u.active });
      u.active = r.active;
      renderUsers(); refreshCurrent();
      toast(`${u.name} is now ${u.active ? "enabled" : "disabled"}.`);
    } catch(err){ if(err.message !== "signed out"){ toast(err.message); btn.disabled = false; } }
  };

  // audit log
  function auditRow(a, full){
    const t = new Date(a.at);
    return `<tr><td>${esc(full ? stamp(t) : when(a.at))}</td><td>${esc(a.user)}</td><td>${esc(a.action)}</td>${
      full ? `<td class="wrap">${esc(a.details)}</td><td>${esc(a.ip)}</td>` : ""}</tr>`;
  }
  function renderAudit(){
    const q = $("auditSearch").value.toLowerCase(), src = $("auditSource").value;
    const list = audit.filter(a => (!src || a.source === src) && JSON.stringify(a).toLowerCase().includes(q));
    $("auditRows").innerHTML = list.length ? list.map(a => auditRow(a, true)).join("")
      : '<tr><td colspan="5" class="empty">Nothing matches.</td></tr>';
  }

  // admin sessions
  const actTime = a => clock(new Date(a.at));
  const actsHtml = list => list.length
    ? list.map(a => `<li><time>${esc(actTime(a))}</time><div>${esc(a.action)} <span>${a.details ? "· " + esc(a.details) : ""}</span></div></li>`).join("")
    : '<li class="empty">No actions were done in this session.</li>';
  function renderCurrent(){
    if(!current) return;
    const login = new Date(current.login);
    $("curAdmin").textContent = current.admin.split("@")[0];
    $("curLogin").textContent = clock(login);
    $("curDuration").textContent = duration(login, new Date());
    $("curCount").textContent = current.actions.length;
    $("curActs").innerHTML = actsHtml(current.actions.slice().reverse());
  }
  async function refreshCurrent(){
    try{ current = await api("/admin/api/current-session"); renderCurrent(); } catch(err){}
  }
  setInterval(renderCurrent, 30000);
  const sessAdmin = $("sessAdmin");
  function renderSessions(){
    const q = $("sessSearch").value.toLowerCase(), who = sessAdmin.value;
    const list = sessions.filter(s => (!who || s.admin === who) && JSON.stringify(s).toLowerCase().includes(q));
    $("sessRows").innerHTML = list.length ? list.map((s, i) => {
      const a = new Date(s.login), b = s.logout ? new Date(s.logout) : null;
      const label = s.ended === "timeout" ? "Timed out" : s.ended === "active" ? "Active" : "Logged out";
      return `<tr class="sess-row" data-s="${i}">
          <td>${esc(s.admin)}</td><td>${esc(stamp(a))}</td><td>${b ? esc(stamp(b)) : "-"}</td>
          <td>${b ? duration(a, b) : duration(a, new Date())}</td><td>${s.actions.length}</td>
          <td><span class="chip ${esc(s.ended)}">${label}</span></td><td>${esc(s.ip || "-")}</td>
        </tr>
        <tr class="sess-detail" data-d="${i}" hidden><td colspan="7"><ul class="acts">${actsHtml(s.actions)}</ul></td></tr>`;
    }).join("") : '<tr><td colspan="7" class="empty">No sessions match.</td></tr>';
  }
  $("sessRows").onclick = e => {
    const row = e.target.closest(".sess-row");
    if(!row) return;
    const det = document.querySelector(`[data-d="${row.dataset.s}"]`);
    det.hidden = !det.hidden;
    row.classList.toggle("open", !det.hidden);
  };

  // loading each page's data
  async function load(tab){
    try{
      if(tab === "dashboard"){ overview = await api("/admin/api/overview?range=" + encodeURIComponent($("range").value)); renderOverview(); }
      else if(tab === "users"){ users = await api("/admin/api/users"); renderUsers(); }
      else if(tab === "requests"){
        requests = await api("/admin/api/admin-requests");
        setReqCount(requests.filter(r => r.status === "pending").length);
        renderRequests();
      }
      else if(tab === "feedback"){ feedback = await api("/admin/api/feedback"); renderFeedback(); }
      else if(tab === "sessions"){
        sessions = await api("/admin/api/sessions");
        const admins = [...new Set(sessions.map(s => s.admin))];
        const keep = sessAdmin.value;
        sessAdmin.innerHTML = '<option value="">All admins</option>' + admins.map(a => `<option>${esc(a)}</option>`).join("");
        sessAdmin.value = admins.includes(keep) ? keep : "";
        renderSessions();
      }
      else if(tab === "audit"){ audit = await api("/admin/api/audit"); renderAudit(); }
      await refreshCurrent();
    } catch(err){ if(err.message !== "signed out") toast("Could not load the data: " + err.message); }
  }

  // export CSV (the page that is open)
  function csv(rows){
    return rows.map(r => r.map(v => {
      const s = String(v === null || v === undefined ? "" : v);
      return /[",\n\r]/.test(s) ? '"' + s.replace(/"/g, '""') + '"' : s;
    }).join(",")).join("\r\n");
  }
  function exportRows(){
    if(currentTab === "users") return [["Name", "Email", "Role", "Status", "Chats", "Last active"],
      ...users.map(u => [u.name, u.email, u.role, u.active ? "Active" : "Disabled", u.chats, u.last || ""])];
    if(currentTab === "requests") return [["Name", "Email", "Role / reason", "Status", "Asked", "Email confirmed", "Decided", "Decided by"],
      ...requests.map(r => [r.name, r.email, r.reason, reqLabel[r.status] || r.status, r.requested_at, r.verified_at || "", r.decided_at || "", r.decided_by])];
    if(currentTab === "feedback") return [["Time", "User", "Rating", "Status", "Question", "Answer", "Resolved by"],
      ...feedback.map(f => [f.at, f.user, f.type === "up" ? "Good" : "Bad", f.status, f.question, f.answer, f.resolved_by])];
    if(currentTab === "sessions") return [["Admin", "Logged in", "Logged out", "Ended by", "IP", "Actions"],
      ...sessions.map(s => [s.admin, s.login, s.logout || "", s.ended, s.ip, s.actions.map(a => a.action + (a.details ? ": " + a.details : "")).join(" | ")])];
    if(currentTab === "audit") return [["Time", "User", "Action", "Details", "IP"],
      ...audit.map(a => [a.at, a.user, a.action, a.details, a.ip])];
    const n = overview.stats.now;
    return [["Measure (" + overview.range.label + ")", "Value"], ["Total users", overview.stats.total_users],
      ["New users", n.new_users], ["Active users", n.active], ["Questions asked", n.questions],
      ["Helpful answers %", n.helpful === null ? "" : n.helpful], ["Good ratings", n.good], ["Bad ratings", n.bad],
      ["Registration checks", n.registration], ["Navigator routes", n.routes]];
  }
  $("exportBtn").onclick = () => {
    const rows = exportRows();
    const blob = new Blob(["﻿" + csv(rows)], { type: "text/csv;charset=utf-8" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = `campus-ai-${currentTab}-${new Date().toISOString().slice(0, 10)}.csv`;
    document.body.appendChild(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(a.href), 1000);
    post("/admin/api/actions", { action: "Exported CSV", details: titles[currentTab] }).then(refreshCurrent).catch(() => {});
    toast(`Downloaded ${rows.length - 1} row${rows.length === 2 ? "" : "s"}.`);
  };
  $("refreshBtn").onclick = async () => {
    await load(currentTab);
    post("/admin/api/actions", { action: "Refreshed data", details: titles[currentTab] }).then(refreshCurrent).catch(() => {});
    toast("Refreshed.");
  };
  $("range").onchange = () => {
    load("dashboard");
    post("/admin/api/actions", { action: "Changed period", details: $("range").selectedOptions[0].text }).then(refreshCurrent).catch(() => {});
  };

  // top bar buttons
  $("bellBtn").onclick = () => { $("fbFilter").value = "open"; showTab("feedback"); };
  $("searchBtn").onclick = () => {
    const box = { users: "userSearch", requests: "reqSearch", feedback: "fbSearch", sessions: "sessSearch", audit: "auditSearch" }[currentTab];
    if(box) $(box).focus(); else { showTab("users"); setTimeout(() => $("userSearch").focus(), 50); }
  };

  // profile button + account card (as on the student side)
  const overlay = $("accountOverlay");
  function fillProfile(me){
    const initial = (me.name || "A").trim().charAt(0).toUpperCase();
    $("profileInitial").textContent = initial;
    $("acctAvatar").textContent = initial;
    $("acctName").textContent = me.name || "Admin";
    $("acctEmail").textContent = me.email || "";
    $("profileBtn").title = (me.name || "Admin") + " · your account";
  }
  function syncThemeSwitch(){
    const theme = document.documentElement.getAttribute("data-theme") === "dark" ? "dark" : "light";
    document.querySelectorAll(".theme-opt").forEach(b => b.setAttribute("aria-checked", b.dataset.themeChoice === theme ? "true" : "false"));
    document.querySelector(".theme-switch").dataset.value = theme;
  }
  function openAccount(){ overlay.classList.remove("closing"); overlay.hidden = false; syncThemeSwitch(); $("acctClose").focus(); }
  function closeAccount(){
    if(overlay.hidden) return;
    overlay.classList.add("closing");
    setTimeout(() => { overlay.classList.remove("closing"); overlay.hidden = true; }, 180);
    $("profileBtn").focus();
  }
  $("profileBtn").onclick = openAccount;
  $("acctClose").onclick = closeAccount;
  overlay.addEventListener("click", e => { if(e.target === overlay) closeAccount(); });
  document.addEventListener("keydown", e => { if(e.key === "Escape") closeAccount(); });
  document.querySelectorAll(".theme-opt").forEach(b => b.addEventListener("click", () => {
    document.documentElement.classList.add("theme-changing");
    setTheme(b.dataset.themeChoice);              // common.js: same setting as the student side
    syncThemeSwitch(); recolorCharts();
    setTimeout(() => document.documentElement.classList.remove("theme-changing"), 400);
  }));
  // charts take their colours from the theme: redraw them when it changes
  function recolorCharts(){
    const grid = css("--border"), tick = css("--text-dim");
    [lineChart, yearChart].forEach(ch => {
      ch.options.scales.x.ticks.color = tick; ch.options.scales.y.ticks.color = tick; ch.options.scales.y.grid.color = grid;
    });
    lineChart.data.datasets[1].borderColor = tick;
    yearChart.data.datasets[0].backgroundColor = css("--a-chart");
    ringChart.data.datasets[0].backgroundColor = [css("--a-chart"), css("--surface-2")];
    lineChart.update(); yearChart.update(); ringChart.update();
  }
  window.addEventListener("storage", e => { if(e.key === "campusAI.theme") setTimeout(() => { syncThemeSwitch(); recolorCharts(); }, 0); });

  // menu on the left: ✕ closes it, ☰ opens it (as on the student side)
  const MENU_KEY = "campusAI.menuClosed";
  const phone = window.matchMedia("(max-width: 700px)");
  function setMenuOpen(open, remember){
    $("layout").classList.toggle("collapsed", !open);
    $("menuToggle").textContent = open ? "✕" : "☰";
    $("menuToggle").setAttribute("aria-label", open ? "Close menu" : "Open menu");
    $("menuToggle").setAttribute("aria-expanded", open ? "true" : "false");
    if(remember){ try{ localStorage.setItem(MENU_KEY, open ? "0" : "1"); } catch(e){} }
    setTimeout(() => { lineChart.resize(); yearChart.resize(); }, 300);   // the page got wider / narrower
  }
  $("menuToggle").onclick = () => setMenuOpen($("layout").classList.contains("collapsed"), !phone.matches);
  (function(){
    let closed = false;
    try{ closed = localStorage.getItem(MENU_KEY) === "1"; } catch(e){}
    setMenuOpen(!(closed || phone.matches));
  })();

  // log out: close the session, show what was done
  $("logoutBtn").onclick = async () => {
    overlay.hidden = true;
    let summary = null;
    try{ summary = await post("/admin/api/logout"); } catch(err){ if(err.message === "signed out") return; }
    if(summary){
      const a = new Date(summary.login), b = summary.logout ? new Date(summary.logout) : new Date();
      $("sumLogin").textContent = clock(a);
      $("sumLogout").textContent = clock(b);
      $("sumDuration").textContent = duration(a, b);
      $("sumCount").textContent = summary.actions.length;
      $("sumActs").innerHTML = actsHtml(summary.actions);
    }
    $("logoutModal").classList.add("show");
  };
  $("toLogin").onclick = () => { window.location.href = "/login"; };

  // switching pages
  function showTab(name){
    document.querySelectorAll(".mainnav a[data-tab]").forEach(a => a.classList.toggle("active", a.dataset.tab === name));
    if(phone.matches) setMenuOpen(false);           // on a phone the menu sits above the page: fold it away
    document.querySelectorAll(".panel").forEach(p => p.hidden = p.id !== "panel-" + name);
    $("pageTitle").textContent = titles[name];
    if(name !== currentTab) post("/admin/api/actions", { action: "Opened", details: titles[name] }).then(refreshCurrent).catch(() => {});
    currentTab = name;
    load(name);
    window.scrollTo(0, 0);
  }
  document.querySelectorAll("[data-tab]").forEach(el =>
    el.addEventListener("click", e => { e.preventDefault(); showTab(el.dataset.tab); }));

  ["userSearch", "roleFilter", "statusFilter"].forEach(id => $(id).addEventListener(id === "userSearch" ? "input" : "change", renderUsers));
  $("reqFilter").onchange = renderRequests; $("reqSearch").oninput = renderRequests;
  $("fbFilter").onchange = renderFeedback; $("fbSearch").oninput = renderFeedback;
  $("auditSearch").oninput = renderAudit; $("auditSource").onchange = renderAudit;
  $("sessSearch").oninput = renderSessions; sessAdmin.onchange = renderSessions;

  load("dashboard");
})();
