/* SocioSense dashboard.
 *
 * Renders only what /api/plan returns. No mock venues, no client-side scoring.
 * All API strings are inserted as text nodes, never as HTML.
 */

const STATE = {
  constraints: new Set(),
  profile: null,
  loading: false,
  pending: false,
  timer: null,
  startedAt: 0,
};

const $ = (id) => document.getElementById(id);

/* --------------------------------------------------------------- helpers */
function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined && text !== null) node.textContent = String(text);
  return node;
}

function icon(name) {
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("class", "icon");
  const use = document.createElementNS("http://www.w3.org/2000/svg", "use");
  use.setAttribute("href", `#i-${name}`);
  svg.appendChild(use);
  return svg;
}

function badge(label, variant, iconName) {
  const node = el("span", `badge ${variant || ""}`.trim());
  if (iconName) node.appendChild(icon(iconName));
  node.appendChild(el("span", null, label));
  return node;
}

function clear(node) {
  while (node.firstChild) node.removeChild(node.firstChild);
}

function money(level) {
  if (!level) return null;
  return "$".repeat(Math.max(0, Math.min(4, Number(level))));
}

function shortAddress(venue) {
  const parts = String(venue.address || "")
    .split(",")
    .map((part) => part.trim())
    .filter(Boolean);
  if (!parts.length) return venue.city || "Address unavailable";
  const joined = parts.slice(0, 2).join(", ");
  if (venue.city && !joined.toLowerCase().includes(String(venue.city).toLowerCase())) {
    return `${joined}, ${venue.city}`;
  }
  return joined;
}

function percent(value) {
  return `${Math.round(Number(value || 0) * 100)}%`;
}

function memberLabel(id) {
  const member = (STATE.profile?.members || []).find((item) => item.id === id);
  return member ? member.label : id;
}

/* --------------------------------------------------------------- sidebar */
function renderMembers(profile) {
  const host = $("members");
  clear(host);
  for (const member of profile.members) {
    const card = el("div", "member");
    const row = el("div", "member-row");
    row.appendChild(el("span", "member-name", member.label));
    if (member.age_band) row.appendChild(el("span", "member-age", `Age ${member.age_band}`));
    card.appendChild(row);

    const tastes = el("div", "tastes");
    for (const taste of member.tastes) {
      tastes.appendChild(badge(taste.query, taste.kind === "tag" ? "badge-primary" : ""));
    }
    card.appendChild(tastes);
    host.appendChild(card);
  }
}

function syncToggles() {
  for (const button of document.querySelectorAll(".toggle")) {
    const active = STATE.constraints.has(button.dataset.key);
    button.classList.toggle("active", active);
    button.setAttribute("aria-pressed", String(active));
  }
}

/* ---------------------------------------------------------------- status */
function setLoading(loading) {
  STATE.loading = loading;
  const button = $("plan-button");
  button.disabled = loading || !STATE.profile;
  button.querySelector(".spinner").hidden = !loading;
  $("plan-icon").style.display = loading ? "none" : "";
  $("plan-label").textContent = loading ? "Planning…" : "Update plan";
  $("plan").classList.toggle("loading", loading);
  $("plan").setAttribute("aria-busy", String(loading));

  if (loading) {
    STATE.startedAt = Date.now();
    $("status").textContent = "Resolving taste seeds… 0s";
    STATE.timer = setInterval(() => {
      const seconds = Math.floor((Date.now() - STATE.startedAt) / 1000);
      $("status").textContent = `Resolving taste seeds… ${seconds}s`;
    }, 1000);
  } else {
    clearInterval(STATE.timer);
    STATE.timer = null;
  }
}

/* ------------------------------------------------------------------ plan */
function renderHeader(payload) {
  $("location").textContent = payload.location || "Unknown area";
  $("title").textContent = payload.label || payload.request || "Family plan";

  const active = payload.active_constraints || [];
  const cuisine = payload.cuisine ? `${payload.cuisine} cuisine` : "the group's taste profile";
  const constraintText = active.length
    ? `${active.length} hard constraint${active.length === 1 ? "" : "s"}`
    : "no hard constraints (everything is unverified)";
  $("lede").textContent = `Culturally aligned venues matched to ${cuisine}, filtered through ${constraintText}.`;

  const mode = payload.data_mode === "live" ? "live" : "fixture";
  const modeBadge = $("mode-badge");
  modeBadge.textContent = mode === "live" ? "Live" : "Fixture";
  modeBadge.className = `badge ${mode === "live" ? "badge-primary" : "badge-warning"}`;

  const banner = $("banner");
  banner.className = "banner hidden";
  if (mode === "fixture") {
    banner.className = "banner fixture";
    banner.textContent =
      "Fixture data: venues below are synthetic demo records, not real places.";
  }
  if (payload.disclaimer) $("disclaimer").textContent = payload.disclaimer;
}

function affinityBlock(rec, payload) {
  const list = el("div", "affinity-list");
  const reasons = rec.reasons || [];
  let shown = 0;

  for (const member of payload.members || []) {
    const value = rec.affinities ? rec.affinities[member.id] : undefined;
    if (value === undefined || value === null) continue;
    shown += 1;

    const reason = reasons.find((line) => line.startsWith(`${member.label}:`)) || "";
    const sourceMatch = reason.match(/ from (.+)$/);
    const estimated = (rec.affinity_evidence || {})[member.id] === "lower_bound";

    const row = el("div", "affinity-row");
    const head = el("div", "affinity-head");
    head.appendChild(el("span", "affinity-name", member.label));
    head.appendChild(el("span", "affinity-value", percent(value)));
    row.appendChild(head);

    const source = sourceMatch ? `from ${sourceMatch[1]}` : "no seed attribution";
    row.appendChild(
      el("p", "affinity-source", estimated ? `${source} · estimated lower bound` : source)
    );
    list.appendChild(row);
  }

  if (!shown) list.appendChild(el("p", "affinity-source", "No member affinity for this venue."));
  return list;
}

function venueCard(rec, payload) {
  const card = el("article", "venue-card");

  const head = el("div", "venue-head");
  const titleWrap = el("div");
  const titleRow = el("div", "venue-title-row");
  titleRow.appendChild(el("h3", "venue-name", rec.name));
  titleRow.appendChild(
    rec.status === "ready"
      ? badge("Verified", "badge-success", "check")
      : badge("Needs check", "badge-warning", "alert")
  );
  titleWrap.appendChild(titleRow);

  const meta = el("div", "venue-meta");
  const place = el("span", "meta-item");
  place.appendChild(icon("pin"));
  place.appendChild(el("span", null, shortAddress(rec)));
  meta.appendChild(place);

  if (rec.rating) {
    const rating = el("span", "meta-item rating");
    rating.appendChild(icon("star"));
    rating.appendChild(el("span", null, Number(rec.rating).toFixed(1)));
    meta.appendChild(rating);
  }
  const price = money(rec.price_level);
  if (price) meta.appendChild(el("span", "meta-item", price));

  titleWrap.appendChild(meta);
  head.appendChild(titleWrap);

  const score = el("div", "score-box");
  const caption = el("div", "score-caption");
  caption.appendChild(el("strong", null, "Bridge score"));
  caption.appendChild(el("span", null, "Worst-member weighted"));
  score.appendChild(caption);
  const ringValue = Math.round(Number(rec.bridge_score || 0) * 100);
  score.appendChild(el("div", `score-ring ${ringValue >= 65 ? "good" : ""}`.trim(), `${ringValue}%`));
  head.appendChild(score);
  card.appendChild(head);

  const body = el("div", "venue-body");

  const affinityCol = el("div", "venue-col");
  const affinityTitle = el("h4", "col-title affinity");
  affinityTitle.appendChild(icon("heart"));
  affinityTitle.appendChild(el("span", null, "Cultural affinity"));
  affinityCol.appendChild(affinityTitle);
  affinityCol.appendChild(affinityBlock(rec, payload));
  if (rec.cuisine) {
    const note = String(rec.cuisine);
    affinityCol.appendChild(
      el("p", "cuisine-note", `Qloo cuisine note: ${note.length > 220 ? `${note.slice(0, 217)}…` : note}`)
    );
  }
  body.appendChild(affinityCol);

  const verifyCol = el("div", "venue-col");
  const verifyTitle = el("h4", "col-title verify");
  verifyTitle.appendChild(icon("info"));
  verifyTitle.appendChild(el("span", null, "Verification & safety"));
  verifyCol.appendChild(verifyTitle);

  const sourceLine = el("p", "source-line");
  sourceLine.appendChild(el("span", null, "Source: "));
  sourceLine.appendChild(el("strong", null, rec.provenance || "None (unverified)"));
  verifyCol.appendChild(sourceLine);

  const caveats = rec.caveats || [];
  if (caveats.length) {
    const box = el("div", "caveat-box");
    const boxTitle = el("div", "caveat-title");
    boxTitle.appendChild(icon("alert"));
    boxTitle.appendChild(el("span", null, "Unverified constraints"));
    box.appendChild(boxTitle);
    const list = el("ul", "caveat-list");
    for (const caveat of caveats) list.appendChild(el("li", null, caveat));
    box.appendChild(list);
    verifyCol.appendChild(box);
  } else {
    const ok = el("div", "ok-box");
    ok.appendChild(icon("check"));
    ok.appendChild(el("span", null, "All hard constraints verified by the venue."));
    verifyCol.appendChild(ok);
  }
  body.appendChild(verifyCol);

  card.appendChild(body);
  return card;
}

function section(kind, title, subtitle, children) {
  const wrapper = el("section", "section");
  const heading = el("h2", `section-heading ${kind}`);
  heading.appendChild(icon(kind === "ready" ? "check" : kind === "pending" ? "alert" : "alert"));
  heading.appendChild(el("span", null, title));
  wrapper.appendChild(heading);
  if (subtitle) wrapper.appendChild(el("p", "section-sub", subtitle));
  wrapper.appendChild(children);
  return wrapper;
}

function venueSection(kind, title, subtitle, venues, payload) {
  if (!venues.length) {
    const empty = el("div", "empty");
    empty.textContent =
      kind === "ready"
        ? "No venue passed every hard constraint with verified evidence. Review the shortlist below."
        : "No venues in this bucket for the current constraints.";
    return section(kind, title, subtitle, empty);
  }
  const list = el("div", "venue-list");
  for (const rec of venues) list.appendChild(venueCard(rec, payload));
  return section(kind, title, subtitle, list);
}

function renderExtras(payload) {
  const nodes = [];

  if ((payload.excluded || []).length) {
    const list = el("ul", "plain-list");
    for (const rec of payload.excluded) {
      const reason = (rec.caveats && rec.caveats[0]) || (rec.reasons && rec.reasons[0]) || "hard constraint failed";
      list.appendChild(el("li", null, `${rec.name} — ${reason}`));
    }
    nodes.push(section("excluded", `Excluded (${payload.excluded.length})`, "A hard constraint was violated with evidence.", list));
  }

  if ((payload.unresolved_seeds || []).length) {
    const list = el("ul", "plain-list");
    for (const seed of payload.unresolved_seeds) {
      list.appendChild(
        el("li", null, `${seed.query} (${seed.domain}, ${memberLabel(seed.member)}) — ${seed.error || "no match"}`)
      );
    }
    nodes.push(
      section(
        "excluded",
        `Ungrounded tastes (${payload.unresolved_seeds.length})`,
        "These seeds were rejected instead of guessed; API errors are shown verbatim.",
        list
      )
    );
  }

  if ((payload.uncovered_members || []).length) {
    const list = el("ul", "plain-list");
    for (const id of payload.uncovered_members) list.appendChild(el("li", null, memberLabel(id)));
    nodes.push(section("pending", "Members not represented", "No usable seeds, so the ranking cannot claim to cover them.", list));
  }

  if ((payload.trace || []).length) {
    const details = el("details", "trace");
    details.appendChild(el("summary", null, `Audit trace (${payload.trace.length} steps)`));
    const table = el("table", "trace-table");
    const thead = el("thead");
    const headRow = el("tr");
    for (const heading of ["Step", "Detail", "Time"]) headRow.appendChild(el("th", null, heading));
    thead.appendChild(headRow);
    table.appendChild(thead);
    const tbody = el("tbody");
    for (const event of payload.trace) {
      const row = el("tr");
      row.appendChild(el("td", null, event.step));
      row.appendChild(el("td", null, event.detail));
      row.appendChild(el("td", "ms", `${Math.round(event.duration_ms || 0)} ms`));
      tbody.appendChild(row);
    }
    table.appendChild(tbody);
    details.appendChild(table);
    nodes.push(details);
  }

  return nodes;
}

function renderPlan(payload) {
  renderHeader(payload);

  const ready = (payload.recommendations || []).filter((rec) => rec.status === "ready");
  const pending = (payload.recommendations || []).filter((rec) => rec.status !== "ready");

  const plan = $("plan");
  clear(plan);
  plan.appendChild(
    venueSection(
      "ready",
      `Ready to book${ready.length ? ` (${ready.length})` : ""}`,
      null,
      ready,
      payload
    )
  );
  plan.appendChild(
    venueSection(
      "pending",
      `Worth a phone call${pending.length ? ` (${pending.length})` : ""}`,
      "These venues fit the cultural taste profile but need a check before booking: a hard constraint is unverified.",
      pending,
      payload
    )
  );
  for (const node of renderExtras(payload)) plan.appendChild(node);

  $("status").textContent = `${payload.recommendations.length} venues · ${payload.data_mode} data`;
}

function renderError(message) {
  const plan = $("plan");
  clear(plan);
  const banner = $("banner");
  banner.className = "banner error";
  banner.textContent = message;
  $("status").textContent = "Plan failed";
}

/* ------------------------------------------------------------------ data */
async function getJSON(url) {
  const response = await fetch(url, { headers: { Accept: "application/json" } });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(payload.error || `Request failed (${response.status})`);
  return payload;
}

async function updatePlan() {
  if (!STATE.profile) return;
  // A toggle changed while a plan was in flight: remember it and re-run once,
  // so the rendered plan always matches the visible constraint switches.
  if (STATE.loading) {
    STATE.pending = true;
    return;
  }
  setLoading(true);
  try {
    const query = new URLSearchParams({ constraints: [...STATE.constraints].join(",") });
    const payload = await getJSON(`/api/plan?${query}`);
    renderPlan(payload);
  } catch (error) {
    renderError(String(error.message || error));
  } finally {
    setLoading(false);
    if (STATE.pending) {
      STATE.pending = false;
      updatePlan();
    }
  }
}

async function boot() {
  try {
    const profile = await getJSON("/api/profile");
    STATE.profile = profile;
    STATE.constraints = new Set(
      Object.entries(profile.requirements)
        .filter(([key, value]) => key !== "max_price_level" && key !== "radius_m" && value === true)
        .map(([key]) => key)
    );
    renderMembers(profile);
    syncToggles();
    $("plan-button").disabled = false;
    $("status").textContent = `${profile.members.length} members · ${profile.location || "no area set"}`;
    await updatePlan();
  } catch (error) {
    renderError(`Could not load the profile: ${error.message || error}`);
  }
}

for (const button of document.querySelectorAll(".toggle")) {
  button.addEventListener("click", () => {
    const key = button.dataset.key;
    if (STATE.constraints.has(key)) STATE.constraints.delete(key);
    else STATE.constraints.add(key);
    syncToggles();
    updatePlan();
  });
}

$("plan-button").addEventListener("click", updatePlan);

boot();
