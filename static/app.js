// SkinSync front-end (vanilla JS, no build step)
const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const money = (n) => "$" + Number(n).toFixed(2).replace(/\.00$/, "");
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

const state = { file: null, sample: false, session: null, analysis: null, plan: null, tab: "am",
  look: null, lip: null, blush: null, catalog: [], bag: JSON.parse(localStorage.getItem("ss_bag") || "[]"), trace: [] };

const CAT_COLORS = { cleanser: "#9FB7C9", toner: "#B9CDB4", serum: "#E2B57D", treatment: "#8E3F67", eye: "#A99BC9",
  moisturizer: "#D9A9A0", sunscreen: "#E7C66A", mask: "#8FA89A", foundation: "#C99A74", lipstick: "#B5656B", blush: "#E8A08A" };

async function api(path, opts = {}) {
  const r = await fetch(path, opts);
  const j = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(j.error || j.detail || `Request failed (${r.status})`);
  return j;
}
function toast(msg, ms = 2600) { const t = $("#toast"); t.textContent = msg; t.hidden = false; clearTimeout(t._h); t._h = setTimeout(() => (t.hidden = true), ms); }

// ---------- boot ----------
(async function boot() {
  try {
    const h = await api("/api/health");
    const b = $("#modeBadge");
    b.textContent = h.demo_mode ? "Demo mode" : "Live · YouCam";
    b.classList.toggle("live", !h.demo_mode);
    b.title = h.demo_mode ? "Running with sample YouCam responses (no API key set)" : "Calling YouCam AI APIs live";
    state.catalog = (await api("/api/catalog")).products;
  } catch (e) { console.warn(e); }
  renderBag();
})();

// ---------- upload & prefs ----------
$("#fileInput").addEventListener("change", (e) => {
  const f = e.target.files[0]; if (!f) return;
  state.file = f; state.sample = false; showPreview(URL.createObjectURL(f));
});
$("#sampleBtn").addEventListener("click", () => { state.file = null; state.sample = true; showPreview("/static/img/sample_selfie.jpg"); });
const drop = $("#drop");
["dragover", "dragenter"].forEach((ev) => drop.addEventListener(ev, (e) => { e.preventDefault(); drop.style.borderColor = "#6B2D4E"; }));
drop.addEventListener("drop", (e) => { e.preventDefault(); const f = e.dataTransfer.files[0]; if (f) { state.file = f; state.sample = false; showPreview(URL.createObjectURL(f)); } });
function showPreview(src) { const p = $("#preview"); p.src = src; p.hidden = false; $("#dropHint").hidden = true; $("#analyzeBtn").disabled = false; }
$("#budget").addEventListener("input", (e) => ($("#budgetOut").textContent = "$" + e.target.value));
$$("#goals .chip").forEach((c) => c.addEventListener("click", () => c.classList.toggle("on")));
function prefs() {
  return { budget: Number($("#budget").value), sensitive: $("#sensitive").checked, fragrance_free: $("#ffree").checked,
    goals: $$("#goals .chip.on").map((c) => c.dataset.goal) };
}

// ---------- progress ----------
function step(name, msg) {
  const order = ["upload", "skin", "tone", "agent", "vto"]; const i = order.indexOf(name);
  $$("#progress li").forEach((li, j) => { li.classList.toggle("done", j < i); li.classList.toggle("active", j === i); });
  if (msg) $("#progressMsg").textContent = msg;
}
function progressDone() { $$("#progress li").forEach((li) => { li.classList.remove("active"); li.classList.add("done"); }); }

// ---------- main flow ----------
$("#analyzeBtn").addEventListener("click", run);
async function run() {
  $("#analyzeBtn").disabled = true;
  $("#progress").hidden = false; $("#results").hidden = true;
  $("#progress").scrollIntoView({ behavior: "smooth", block: "center" });
  try {
    step("upload", "Fixing orientation and resizing to YouCam's specs…");
    const fd = new FormData();
    if (state.file) fd.append("file", state.file); else fd.append("sample", "true");
    setTimeout(() => step("skin", "Scoring 14 concerns: pores, redness, acne, texture, hydration…"), 400);
    setTimeout(() => step("tone", "Reading your skin colour for shade matching…"), 1100);
    const a = await api("/api/analyze", { method: "POST", body: fd });
    state.session = a.session_id; state.analysis = a; state.trace = a.youcam_calls.map((c) => ({ kind: "youcam", ...c }));
    step("agent", "The agent is reading your scores and shopping the catalog…");
    const plan = await api("/api/plan", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session_id: state.session, prefs: prefs() }) });
    state.plan = plan;
    renderResults();
    step("vto", "Rendering your recommended look with YouCam Makeup Try-On…");
    await tryOn();
    progressDone(); $("#progressMsg").textContent = `Done — ${a.mode === "demo" ? "demo responses" : "live YouCam calls"}, plan by ${plan.engine}.`;
    $("#results").scrollIntoView({ behavior: "smooth" });
  } catch (e) {
    $("#progressMsg").textContent = "Something went wrong: " + e.message;
    toast(e.message, 5000);
  } finally { $("#analyzeBtn").disabled = false; }
}

function level(score) { return score < 62 ? "bad" : score < 72 ? "warn" : "good"; }

function renderResults() {
  const a = state.analysis, p = state.plan;
  $("#results").hidden = false;
  $("#beforeImg").src = a.image_url;
  const ov = a.analysis.overall ?? "–";
  $("#overall").textContent = ov;
  requestAnimationFrame(() => ($("#ringBar").style.strokeDashoffset = 327 - (327 * (Number(ov) || 0)) / 100));
  $("#skinAge").textContent = a.analysis.skin_age ?? "—";
  $("#toneSwatch").style.background = a.tone.skin_color || "#ccc";
  $("#toneHex").textContent = a.tone.skin_color || "—";
  $("#skinType").textContent = p.skin_type || "—";
  $("#headline").textContent = p.headline;
  $("#summary").textContent = p.summary;
  $("#topConcerns").innerHTML = p.concerns.map((c) => `
    <div class="concern"><div class="num lv-${level(c.score)}">${c.score}</div>
    <div><b>${esc(c.label)}</b><p>${esc(c.explanation)}</p></div></div>`).join("");
  const rows = Object.entries(a.analysis.concerns).sort((x, y) => x[1].ui_score - y[1].ui_score);
  $("#bars").innerHTML = rows.map(([k, v]) => `
    <div class="bar-row"><span>${esc(a.labels[k] || k)}${v.mask_url ? ` <a href="${esc(v.mask_url)}" target="_blank" rel="noopener">mask</a>` : ""}</span>
    <div class="t"><div class="f ${level(v.ui_score)}" style="width:${v.ui_score}%"></div></div><b>${v.ui_score}</b></div>`).join("");
  $("#tips").innerHTML = (p.weekly_tips || []).map((t) => `<li>${esc(t)}</li>`).join("");
  $("#routineTotal").textContent = money(p.basket_total);
  renderRoutine(); renderLookControls(); renderTrace();
}

function productCard(s, i) {
  const pr = s.product, inBag = state.bag.some((b) => b.id === pr.id);
  return `<div class="step">
    <div class="bottle" style="background:${CAT_COLORS[pr.category] || "#c9b"}">${esc(pr.brand[0])}</div>
    <span class="n">Step ${i + 1} · ${esc(s.step)}</span>
    <span class="pname">${esc(pr.name)}</span><span class="brand-s">${esc(pr.brand)}</span>
    <span class="why">${esc(s.why)}</span>
    <span class="ing">${esc((pr.key_ingredients || []).join(" · "))}</span>
    <div class="row"><span class="price">${money(pr.price)}</span>
    <button class="add ${inBag ? "added" : ""}" data-id="${pr.id}">${inBag ? "In bag ✓" : "Add"}</button></div></div>`;
}
function renderRoutine() {
  const steps = state.tab === "am" ? state.plan.am_routine : state.plan.pm_routine;
  $("#routine").innerHTML = steps.map(productCard).join("");
  $$("#routine .add").forEach((b) => b.addEventListener("click", () => { addToBag([b.dataset.id]); renderRoutine(); }));
}
$$(".tab").forEach((t) => t.addEventListener("click", () => {
  $$(".tab").forEach((x) => x.classList.remove("active")); t.classList.add("active"); state.tab = t.dataset.tab; renderRoutine();
}));
$("#addRoutine").addEventListener("click", () => { addToBag(state.plan.basket.map((p) => p.id)); renderRoutine(); openBag(); });

// ---------- try-on ----------
const LOOK_NAMES = { "fresh-dewy": "Fresh Dewy", "soft-glam": "Soft Glam", "office-matte": "Office Matte" };
function renderLookControls() {
  const L = state.plan.look;
  state.look = L.look_id; state.lip = L.lip.id; state.blush = L.blush.id;
  $("#lookWhy").textContent = L.why || L.vibe;
  $("#looks").innerHTML = Object.entries(LOOK_NAMES).map(([id, n]) => `<button class="chip ${id === state.look ? "on" : ""}" data-look="${id}">${n}${id === L.look_id ? " ★" : ""}</button>`).join("");
  $$("#looks .chip").forEach((c) => c.addEventListener("click", () => { state.look = c.dataset.look; $$("#looks .chip").forEach((x) => x.classList.toggle("on", x === c)); }));
  const sw = (cat, cur, key) => state.catalog.filter((p) => p.category === cat).map((p) =>
    `<button class="sw ${p.id === cur ? "on" : ""}" title="${esc(p.name)}" data-id="${p.id}" style="background:${p.shade_hex}"></button>`).join("");
  $("#lipSwatches").innerHTML = sw("lipstick", state.lip);
  $("#blushSwatches").innerHTML = sw("blush", state.blush);
  $$("#lipSwatches .sw").forEach((b) => b.addEventListener("click", () => { state.lip = b.dataset.id; $$("#lipSwatches .sw").forEach((x) => x.classList.toggle("on", x === b)); }));
  $$("#blushSwatches .sw").forEach((b) => b.addEventListener("click", () => { state.blush = b.dataset.id; $$("#blushSwatches .sw").forEach((x) => x.classList.toggle("on", x === b)); }));
  $("#foundation").innerHTML = `<span class="sw" style="background:${L.foundation.shade_hex}"></span><div><b>${esc(L.foundation.name.split("— ")[1] || L.foundation.name)}</b><br><span class="muted">matched to your tone ${esc(state.analysis.tone.skin_color || "")}</span></div>`;
}
$("#renderBtn").addEventListener("click", () => tryOn(true));
async function tryOn(manual = false) {
  $("#vtoLoading").hidden = false;
  try {
    const r = await api("/api/tryon", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session_id: state.session, look_id: state.look, lip_id: state.lip, blush_id: state.blush }) });
    $("#cmpBefore").src = r.before_url; $("#cmpAfter").src = r.after_url;
    $("#vtoNote").textContent = r.note;
    state.lookProducts = r.products;
    $("#lookProducts").innerHTML = r.products.map((p) => `<div><span>${esc(p.name)}</span><b>${money(p.price)}</b></div>`).join("");
    state.trace.push(...r.youcam_calls.map((c) => ({ kind: "youcam", ...c })));
    renderTrace();
    if (manual) toast("Try-on updated");
  } catch (e) { toast("Try-on failed: " + e.message, 5000); }
  finally { $("#vtoLoading").hidden = true; }
}
$("#cmpRange").addEventListener("input", (e) => {
  const v = e.target.value; $("#afterClip").style.clipPath = `inset(0 0 0 ${v}%)`; $("#handle").style.left = v + "%";
});
$("#addLook").addEventListener("click", () => { if (state.lookProducts) { addToBag(state.lookProducts.map((p) => p.id)); openBag(); } });

// ---------- refine ----------
$$("#refineChips .chip").forEach((c) => c.addEventListener("click", () => { $("#refineInput").value = c.textContent; $("#refineForm").requestSubmit(); }));
$("#refineForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  const txt = $("#refineInput").value.trim(); if (!txt) return;
  const btn = e.submitter || $("#refineForm button"); btn.disabled = true; btn.textContent = "Thinking…";
  try {
    state.plan = await api("/api/refine", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ session_id: state.session, instruction: txt }) });
    renderResults(); toast("Plan updated by the agent"); $("#refineInput").value = "";
    await tryOn();
  } catch (err) { toast(err.message, 5000); }
  finally { btn.disabled = false; btn.textContent = "Update plan"; }
});

function renderTrace() {
  const agentSteps = (state.plan?.agent_trace || []).map((t) => `<li>Agent → <code>${esc(t.tool)}</code> ${esc(Object.entries(t.args || {}).map(([k, v]) => `${k}=${v}`).join(", "))} <span class="muted">${esc(t.result || "")}</span></li>`);
  const yc = state.trace.map((c) => `<li>YouCam <code>${esc(c.method)} ${esc(c.endpoint)}</code> <span class="muted">${esc(c.note || "")}${c.ms ? " · " + c.ms + " ms" : ""}</span></li>`);
  $("#trace").innerHTML = [...yc.slice(0, 8), `<li><b>Planner: ${esc(state.plan?.engine || "")}</b></li>`, ...agentSteps, ...yc.slice(8)].join("");
}

// ---------- bag ----------
function addToBag(ids) {
  ids.forEach((id) => { if (!state.bag.some((b) => b.id === id)) { const p = state.catalog.find((x) => x.id === id); if (p) state.bag.push({ id, name: p.name, price: p.price }); } });
  localStorage.setItem("ss_bag", JSON.stringify(state.bag)); renderBag(); toast("Added to bag");
}
function renderBag() {
  $("#bagCount").textContent = state.bag.length;
  $("#bagItems").innerHTML = state.bag.length ? state.bag.map((b, i) => `<div class="bag-item"><span>${esc(b.name)}</span><b>${money(b.price)}</b><button data-i="${i}" aria-label="Remove">✕</button></div>`).join("") : `<p class="muted">Your bag is empty.</p>`;
  $("#bagTotal").textContent = money(state.bag.reduce((s, b) => s + b.price, 0));
  $$("#bagItems button").forEach((b) => b.addEventListener("click", () => { state.bag.splice(Number(b.dataset.i), 1); localStorage.setItem("ss_bag", JSON.stringify(state.bag)); renderBag(); if (state.plan) renderRoutine(); }));
}
function openBag() { $("#bag").classList.add("open"); $("#scrim").hidden = false; }
function closeBag() { $("#bag").classList.remove("open"); $("#scrim").hidden = true; }
$("#bagBtn").addEventListener("click", openBag); $("#bagClose").addEventListener("click", closeBag); $("#scrim").addEventListener("click", closeBag);
$("#checkout").addEventListener("click", () => { if (!state.bag.length) return toast("Bag is empty"); toast(`Order placed (demo): ${state.bag.length} items, ${$("#bagTotal").textContent}`, 3500); state.bag = []; localStorage.setItem("ss_bag", "[]"); renderBag(); closeBag(); });
$("#restart").addEventListener("click", () => { window.scrollTo({ top: 0, behavior: "smooth" }); });
