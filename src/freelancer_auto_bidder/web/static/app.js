"use strict";

const state = {csrf: "", dashboard: null, settings: null, jobs: [], overviewJobs: [], offset: 0, limit: 20, total: 0, selectedJob: null, view: "overview", loadingJobs: false};
const $ = (id) => document.getElementById(id);
const SVG = "http://www.w3.org/2000/svg";
const countryNames = {US: "United States", GB: "United Kingdom", CA: "Canada", AU: "Australia", AE: "United Arab Emirates"};
const viewNames = {overview: "Overview", jobs: "Discover jobs", history: "Bid history", analytics: "Analytics", settings: "Settings"};

function element(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined && text !== null) node.textContent = String(text);
  return node;
}
function svgElement(tag, attrs) {
  const node = document.createElementNS(SVG, tag);
  for (const [key, value] of Object.entries(attrs || {})) node.setAttribute(key, String(value));
  return node;
}
function badge(text, variant = "neutral") { return element("span", `badge badge-${variant}`, text); }
function number(value) { return Number.isFinite(Number(value)) ? Number(value).toLocaleString() : "0"; }
function date(value) {
  if (!value) return null;
  const parsed = typeof value === "number" ? new Date(value < 1e12 ? value * 1000 : value) : new Date(value);
  return Number.isNaN(parsed.getTime()) ? null : parsed;
}
function timeLabel(value, full = false) {
  const parsed = date(value);
  if (!parsed) return "Not yet";
  if (full) return parsed.toLocaleString(undefined, {dateStyle: "medium", timeStyle: "short"});
  const diff = Math.max(0, Date.now() - parsed.getTime());
  if (diff < 60000) return "Just now";
  if (diff < 3600000) return `${Math.floor(diff / 60000)}m ago`;
  if (diff < 86400000) return `${Math.floor(diff / 3600000)}h ago`;
  return parsed.toLocaleDateString(undefined, {month: "short", day: "numeric"});
}
function flag(code) {
  return /^[A-Z]{2}$/.test(code || "") ? String.fromCodePoint(...[...code].map((char) => char.charCodeAt(0) + 127397)) : "◎";
}
function country(job) {
  const info = job.country || {};
  return info.name || countryNames[info.code] || info.code || "Country unknown";
}
function amount(value, currency = "USD") {
  const numeric = Number(value);
  if (value === null || value === undefined || value === "" || !Number.isFinite(numeric)) return "Unspecified";
  try { return new Intl.NumberFormat(undefined, {style: "currency", currency, maximumFractionDigits: numeric % 1 ? 2 : 0}).format(numeric); }
  catch (_) { return `${currency} ${number(numeric)}`; }
}
function budget(job) {
  const data = job.budget || {};
  if (data.minimum == null && data.maximum == null) return "Budget not provided";
  if (data.minimum != null && data.maximum != null && String(data.minimum) !== String(data.maximum)) return `${amount(data.minimum, data.currency)} – ${amount(data.maximum, data.currency)}`;
  return amount(data.maximum ?? data.minimum, data.currency);
}
function safeProjectURL(raw) {
  try {
    const url = new URL(raw);
    return url.protocol === "https:" && ["www.freelancer.com", "www.freelancer-sandbox.com"].includes(url.hostname) ? url.href : null;
  } catch (_) { return null; }
}
function toast(message, error = false) {
  const node = element("div", error ? "toast error" : "toast", message);
  while ($("toast-container").children.length >= 2) $("toast-container").firstElementChild.remove();
  $("toast-container").append(node);
  setTimeout(() => node.remove(), error ? 8500 : 4500);
}
async function api(path, options = {}) {
  const headers = {Accept: "application/json", ...(options.headers || {})};
  if (options.method && options.method !== "GET") {
    headers["Content-Type"] = "application/json";
    headers["X-CSRF-Token"] = state.csrf;
  }
  let response;
  try { response = await fetch(path, {...options, headers, credentials: "same-origin"}); }
  catch (_) { throw new Error("Unable to reach your workspace. Check the connection and try again."); }
  if (response.status === 401) { window.location.assign("/login"); throw new Error("Your session expired. Please sign in again."); }
  let result = {};
  try { result = await response.json(); } catch (_) { /* Standard error below. */ }
  if (!response.ok) {
    const detail = typeof result.detail === "string" ? result.detail : "The request could not be completed. Check your settings and try again.";
    throw new Error(detail);
  }
  return result;
}
async function busy(button, operation, label = "Working…") {
  const original = button.textContent;
  button.disabled = true;
  button.textContent = label;
  try { return await operation(); }
  catch (failure) { toast(failure.message, true); return null; }
  finally { button.disabled = false; if (button.textContent === label) button.textContent = original; }
}
function empty(container, title, description, compact = false) {
  const node = element("div", compact ? "empty-state compact-empty" : "empty-state");
  node.append(element("span", "empty-icon", "⌕"), element("h3", "", title), element("p", "", description));
  container.replaceChildren(node);
}
function setView(view) {
  state.view = Object.hasOwn(viewNames, view) ? view : "overview";
  document.querySelectorAll("[data-view-panel]").forEach((panel) => panel.classList.toggle("hidden", panel.dataset.viewPanel !== state.view));
  document.querySelectorAll("[data-nav]").forEach((link) => {
    const active = link.dataset.nav === state.view;
    link.classList.toggle("active", active);
    if (active) link.setAttribute("aria-current", "page"); else link.removeAttribute("aria-current");
  });
  $("breadcrumb-current").textContent = viewNames[state.view];
  document.title = `${viewNames[state.view]} · BidPilot`;
  closeNavigation();
  if (state.view === "jobs") loadJobs().catch((failure) => toast(failure.message, true));
  if (state.view === "history") loadHistory().catch((failure) => toast(failure.message, true));
  if (state.view === "analytics" && state.dashboard) renderAnalytics(state.dashboard);
}
function closeNavigation() {
  $("sidebar").classList.remove("open");
  $("sidebar-scrim").classList.add("hidden");
  $("menu-button").setAttribute("aria-expanded", "false");
}
function statusVariant(status) {
  if (status === "submitted") return "green";
  if (status === "uncertain" || status === "rejected") return "amber";
  return "neutral";
}

function renderDashboard(data) {
  state.dashboard = data;
  const stats = data.stats || {};
  $("metric-jobs").textContent = number(stats.jobs_total);
  $("metric-recommended").textContent = number(stats.recommended);
  $("metric-today").textContent = number(stats.attempted_today ?? stats.submitted_today);
  $("metric-limit").textContent = ` / ${number(stats.daily_limit)}`;
  $("metric-drafts").textContent = number(stats.drafts);
  $("nav-job-count").textContent = number(stats.jobs_total);
  const demo = data.mode === "demo";
  const live = data.mode === "live";
  const banner = $("mode-banner");
  banner.classList.toggle("demo-mode", demo);
  banner.classList.toggle("live-mode", live);
  $("mode-message").textContent = demo ? "Demo workspace · sample projects only. No Freelancer requests or real bids are sent." : live ? "Live submission is permitted by your server settings. Automation still follows your ON/OFF control, schedule, and limits." : "You're in dry-run mode. Explore projects and draft proposals with no real bid submissions.";
  $("connection-label").textContent = demo ? "Demo workspace" : data.configured ? "Freelancer API configured" : "Connect Freelancer API";
  $("connection-pill").querySelector(".status-dot").classList.toggle("online", Boolean(data.configured) && !demo);
  $("monitor-toggle").checked = Boolean(data.monitor_enabled);
  $("monitor-dot").classList.toggle("online", Boolean(data.monitor_enabled));
  $("monitor-summary").textContent = data.monitor_enabled ? "Monitoring every 5 minutes" : "Monitoring is off";
  const next = date(data.next_run_at);
  $("next-run-label").textContent = data.monitor_enabled ? next ? `Next check ${next.toLocaleTimeString(undefined, {hour: "numeric", minute: "2-digit"})}` : "Waiting for the next scheduled check" : "Start monitoring to schedule a search";
  $("automation-badge").textContent = data.auto_bidder_enabled ? (live ? "Live automation" : "Dry-run active") : "Paused";
  $("automation-badge").className = `badge badge-${data.auto_bidder_enabled ? "green" : "neutral"}`;
  $("automation-toggle").textContent = data.auto_bidder_enabled ? "Pause Auto Bidder" : data.live_allowed ? "Enable live automation →" : "Start Auto Bidder →";
  $("automation-description").textContent = data.last_error ? `Last run needs attention: ${data.last_error}` : data.auto_bidder_enabled && !live ? "Automation is active in dry-run. New matches generate proposals without submitting bids." : "Monitor new projects every five minutes and let your preferences do the filtering.";
  $("workspace-updated").textContent = data.last_run_at ? `Last completed search · ${timeLabel(data.last_run_at, true)}` : "Your first completed search will appear here";
  renderActivity(data.activity || []);
  renderAnalytics(data);
}
function renderActivity(items) {
  const container = $("activity-list");
  container.replaceChildren();
  if (!items.length) { empty(container, "Your workspace is ready.", "Run your first search or turn on monitoring to see activity here.", true); return; }
  for (const activity of items.slice(0, 4)) {
    const row = element("div", "activity-item");
    const isError = ["error", "warning"].includes(activity.level);
    const marker = element("span", `activity-icon ${isError ? "error" : "success"}`, isError ? "!" : "✓");
    marker.setAttribute("aria-hidden", "true");
    const copy = element("div");
    const time = element("time", "", timeLabel(activity.created_at));
    const parsed = date(activity.created_at); if (parsed) time.dateTime = parsed.toISOString();
    copy.append(element("p", "", activity.message), time);
    row.append(marker, copy); container.append(row);
  }
}
function scoreDisplay(job) {
  const value = Math.min(100, Math.max(0, Number(job.score) || 0));
  const wrap = element("div", "score-display");
  const ring = svgElement("svg", {viewBox: "0 0 44 44", class: "score-ring", "aria-hidden": "true"});
  ring.append(svgElement("circle", {cx: 22, cy: 22, r: 18, class: "score-track"}), svgElement("circle", {cx: 22, cy: 22, r: 18, class: "score-fill", "stroke-dasharray": `${value * 1.131} 113.1`}));
  const center = svgElement("text", {x: 22, y: 26, "text-anchor": "middle", transform: "rotate(90 22 22)", class: "score-number", fill: "#866299", stroke: "none", "font-size": 11, "font-weight": 600});
  center.textContent = value;
  ring.append(center);
  const label = element("span", "score-label");
  label.append(element("strong", "", value), document.createTextNode("out of 100"));
  wrap.append(ring, label); wrap.setAttribute("aria-label", `Score ${value} out of 100`);
  return wrap;
}
function jobMeta(job) {
  const meta = element("div", "job-meta");
  const countryLabel = element("span", "", `${flag(job.country?.code)} ${country(job)}`);
  countryLabel.title = job.country?.source ? "Country reported in the project owner's API profile" : "Client country is unknown";
  meta.append(countryLabel);
  if (job.client_rating != null && job.client_reviews) meta.append(element("span", "", `★ ${Number(job.client_rating).toFixed(1)} (${number(job.client_reviews)} reviews)`));
  else meta.append(element("span", "", "No usable reviews"));
  meta.append(element("span", job.payment_verified === true ? "payment-verified" : "", job.payment_verified === true ? "✓ Payment verified" : job.payment_verified === false ? "Payment unverified" : "Payment unknown"));
  return meta;
}
function renderJobCard(job, compact = false) {
  if (compact) {
    const card = element("article", "compact-job");
    const icon = element("span", "compact-job-icon", job.matched_services?.includes("motion_graphics") ? "✧" : "▷");
    icon.setAttribute("aria-hidden", "true");
    const copy = element("div", "compact-job-copy");
    const title = element("button", "compact-job-title", job.title || `Project #${job.id}`);
    title.type = "button"; title.addEventListener("click", () => openProposal(job));
    const meta = element("div", "compact-job-meta");
    meta.append(element("span", "country-label", `${flag(job.country?.code)} ${country(job)}`), element("span", "", "·"), element("span", "", budget(job)));
    copy.append(title, meta);
    const score = badge(`${job.score} score`, job.decision === "BID" ? "green" : "neutral"); score.classList.add("compact-job-score");
    card.append(icon, copy, score); return card;
  }
  const card = element("article", "job-card");
  const top = element("div", "job-card-top");
  const titleWrap = element("div", "job-card-title-wrap");
  const badges = element("div", "job-card-badges");
  badges.append(badge(job.decision === "BID" ? "→ BID · Recommended" : "SKIP · Review criteria", job.decision === "BID" ? "green" : "neutral"), element("span", "job-date", job.updated_at ? `Updated ${timeLabel(job.updated_at)}` : "Fixed-price project"));
  const url = safeProjectURL(job.url);
  const title = element(url ? "a" : "span", "job-card-title", job.title || `Project #${job.id}`);
  if (url) { title.href = url; title.target = "_blank"; title.rel = "noopener noreferrer"; }
  titleWrap.append(badges, title); top.append(titleWrap, scoreDisplay(job)); card.append(top, jobMeta(job));
  const description = job.description || (job.reasons || []).slice(0, 2).join(". ") || "Open this project to review the available assessment details.";
  card.append(element("p", "job-description", description));
  const tags = element("div", "skill-tags");
  const skills = (job.required_skills?.length ? job.required_skills : job.matched_services || []).slice(0, 5);
  for (const skill of skills) tags.append(element("span", "skill-tag", String(skill).replaceAll("_", " ")));
  if (skills.length) card.append(tags);
  const footer = element("div", "job-card-footer");
  const price = element("span", "job-budget"); price.append(element("strong", "", budget(job)), document.createTextNode("Fixed price"));
  const actions = element("div", "job-card-actions");
  if (url) { const link = element("a", "button button-secondary", "View project →"); link.href = url; link.target = "_blank"; link.rel = "noopener noreferrer"; actions.append(link); }
  const proposal = element("button", "button button-primary", job.draft ? "Edit saved draft ✧" : "Draft proposal ✧");
  proposal.type = "button"; proposal.addEventListener("click", () => openProposal(job)); actions.append(proposal);
  footer.append(price, actions); card.append(footer); return card;
}
async function loadOverviewJobs() {
  const result = await api("/api/jobs?sort=score&limit=3&offset=0");
  state.overviewJobs = result.items || [];
  const container = $("overview-jobs"); container.replaceChildren();
  if (!state.overviewJobs.length) empty(container, "Your next project starts here.", "Search live projects or enable monitoring to discover opportunities that fit your skills.", true);
  for (const job of state.overviewJobs) container.append(renderJobCard(job, true));
}
function filterParams() {
  const params = new URLSearchParams({limit: state.limit, offset: state.offset, sort: $("jobs-sort").value});
  const values = {query: $("job-query").value.trim(), skill: $("filter-skill").value, currency: $("filter-currency").value, min_budget: $("filter-min").value, max_budget: $("filter-max").value, decision: $("filter-decision").value};
  for (const [key, value] of Object.entries(values)) if (value !== "") params.set(key, value);
  const countries = [...document.querySelectorAll('input[name="country"]:checked')].map((node) => node.value);
  const additional = $("filter-other-countries").value.split(",").map((value) => value.trim().toUpperCase()).filter(Boolean);
  countries.push(...additional);
  if (countries.length) params.set("countries", countries.join(","));
  return params;
}
let jobsRequest = 0;
async function loadJobs() {
  const requestId = ++jobsRequest;
  const min = $("filter-min").value; const max = $("filter-max").value;
  if (min !== "" && max !== "" && Number(min) > Number(max)) { toast("Minimum budget must be no greater than maximum budget.", true); return; }
  if ((min !== "" || max !== "") && !$("filter-currency").value) { toast("Choose a project currency before filtering budget amounts.", true); return; }
  if ($("filter-other-countries").value.split(",").some((code) => code.trim() && !/^[A-Za-z]{2}$/.test(code.trim()))) { toast("Use two-letter country codes, separated by commas, such as DE, FR.", true); return; }
  const data = await api(`/api/jobs?${filterParams()}`);
  if (requestId !== jobsRequest) return;
  state.jobs = data.items || []; state.total = data.total || 0;
  $("jobs-total").textContent = number(state.total);
  const container = $("jobs-list"); container.replaceChildren();
  if (!state.jobs.length) empty(container, "Make a little room for opportunity.", "No saved projects match these filters. Adjust your filters or use Search live jobs to find new opportunities.");
  for (const job of state.jobs) container.append(renderJobCard(job));
  $("pagination-label").textContent = state.total ? `${state.offset + 1}–${Math.min(state.offset + state.jobs.length, state.total)} of ${number(state.total)} projects` : "No matching projects";
  $("page-prev").disabled = state.offset === 0;
  $("page-next").disabled = state.offset + state.limit >= state.total;
}
async function refreshDashboard() { renderDashboard(await api("/api/dashboard")); }
async function loadHistory() {
  const data = await api("/api/history?limit=100");
  const items = data.items || [];
  $("history-total").textContent = `${number(data.total ?? items.length)} attempts`;
  $("history-body").replaceChildren();
  $("history-empty").classList.toggle("hidden", items.length > 0);
  $("history-body").closest(".table-wrap").classList.toggle("hidden", !items.length);
  for (const item of items) {
    const row = element("tr"); const project = element("td", "", item.title || `Project #${item.project_id}`);
    project.append(element("small", "", `Project #${item.project_id}`));
    const status = element("td"); status.append(badge(String(item.status || "unknown").replaceAll("_", " "), statusVariant(item.status)));
    row.append(project, status, element("td", "", item.bid_id ?? "—"), element("td", "", item.environment || "—"), element("td", "", timeLabel(item.created_at, true)));
    $("history-body").append(row);
  }
}
function renderAnalytics(data) {
  const stats = data.stats || {};
  $("analytics-jobs").textContent = number(stats.jobs_total);
  $("analytics-recommended").textContent = number(stats.recommended);
  $("analytics-submitted").textContent = number(stats.submitted_total);
  $("analytics-drafts").textContent = number(stats.drafts);
  const points = data.chart || [];
  const chart = $("activity-chart"); chart.replaceChildren();
  if (!points.length || points.every((p) => !p.assessed && !p.recommended && !p.submitted)) empty(chart, "A clearer picture is on its way.", "Your first completed search will start the activity chart.", true);
  else {
    const width = 560; const height = 220; const left = 32; const bottom = 185; const max = Math.max(1, ...points.map((p) => Number(p.assessed) || 0), ...points.map((p) => Number(p.recommended) || 0));
    const svg = svgElement("svg", {viewBox: `0 0 ${width} ${height}`, role: "img", "aria-label": "Daily project assessments and recommendations in UTC"});
    const title = svgElement("title"); title.textContent = "Activity over the last seven days (UTC)"; svg.append(title);
    for (let i = 0; i <= 4; i++) {
      const y = bottom - i * 39;
      svg.append(svgElement("line", {x1: left, y1: y, x2: width - 8, y2: y, class: "chart-grid"}));
      const label = svgElement("text", {x: left - 9, y: y + 4, "text-anchor": "end", class: "chart-axis-label"}); label.textContent = Math.round(max * i / 4); svg.append(label);
    }
    const step = (width - left - 12) / points.length;
    points.forEach((point, index) => {
      const x = left + step * index + step / 2;
      const assessed = Number(point.assessed) || 0; const recommended = Number(point.recommended) || 0;
      const barA = svgElement("rect", {x: x - 14, y: bottom - assessed / max * 156, width: 11, height: Math.max(1, assessed / max * 156), rx: 3, fill: "#a78ad6"});
      const barB = svgElement("rect", {x: x + 2, y: bottom - recommended / max * 156, width: 11, height: Math.max(1, recommended / max * 156), rx: 3, fill: "#9dc6ae"});
      const detail = svgElement("title"); detail.textContent = `${point.date}: ${assessed} assessed, ${recommended} recommended, ${point.submitted || 0} submitted`; barA.append(detail);
      const label = svgElement("text", {x, y: bottom + 23, "text-anchor": "middle", class: "chart-label"});
      const day = new Date(`${point.date}T12:00:00Z`); label.textContent = Number.isNaN(day.getTime()) ? point.date : day.toLocaleDateString(undefined, {weekday: "short", timeZone: "UTC"});
      svg.append(barA, barB, label);
    });
    chart.append(svg);
  }
  const container = $("country-chart"); container.replaceChildren();
  const countries = data.country_breakdown || [];
  if (!countries.length) empty(container, "Opportunity has no borders.", "Country distribution appears after projects are assessed.", true);
  const maxCountry = Math.max(1, ...countries.map((item) => Number(item.count) || 0));
  for (const item of countries.slice(0, 7)) {
    const code = item.country;
    const row = element("div", "country-row");
    row.append(element("span", "country-name", `${flag(code)} ${countryNames[code] || code || "Unknown"}`));
    const svg = svgElement("svg", {class: "country-bar", viewBox: "0 0 100 7", "aria-hidden": "true"});
    svg.append(svgElement("rect", {x: 0, y: 0, width: 100, height: 7, rx: 4, fill: "#f0e9f6"}), svgElement("rect", {x: 0, y: 0, width: Math.max(2, Number(item.count) / maxCountry * 100), height: 7, rx: 4, fill: "#c4abdc"}));
    row.append(svg, element("strong", "", number(item.count))); container.append(row);
  }
}

function openProposal(job) {
  state.selectedJob = job;
  $("proposal-project-title").textContent = job.title || `Project #${job.id}`;
  $("proposal-score").textContent = `${job.score} / 100 · ${job.decision}`;
  $("proposal-project-meta").replaceChildren(...jobMeta(job).childNodes);
  $("proposal-requirements").textContent = job.description || "No full description is available for this saved assessment. Review the original Freelancer project before using a proposal.";
  $("proposal-reasons").replaceChildren(...(job.reasons || []).map((reason) => element("li", "", reason)));
  $("proposal-text").value = job.draft?.text || job.proposal || "";
  $("proposal-provider").textContent = providerLabel(job.draft?.provider || "template");
  $("proposal-instructions").value = "";
  updateWords();
  $("proposal-dialog").showModal();
}
function providerLabel(provider) { return provider === "openai" ? "AI-generated draft" : provider === "edited" ? "Your saved draft" : "Engine template"; }
function updateWords() {
  const text = $("proposal-text").value.trim();
  $("proposal-word-count").textContent = `${text ? text.split(/\s+/).length : 0} words · ${$("proposal-text").value.length} characters`;
}
function setSettingsTab(tab) {
  document.querySelectorAll("[data-settings-tab]").forEach((button) => {
    const active = button.dataset.settingsTab === tab;
    button.classList.toggle("active", active); button.setAttribute("aria-selected", String(active));
  });
  document.querySelectorAll("[data-settings-panel]").forEach((panel) => panel.classList.toggle("hidden", panel.dataset.settingsPanel !== tab));
}
function pricingRow(currency, rule = {}) {
  const row = element("tr"); row.dataset.currency = currency; row.append(element("td", "", currency));
  for (const key of ["min_budget", "bid_amount", "max_bid"]) {
    const cell = element("td"); const input = element("input");
    input.type = "number"; input.min = "0.01"; input.step = "0.01"; input.required = true; input.value = rule[key] ?? ""; input.dataset.budgetKey = key;
    input.setAttribute("aria-label", `${currency} ${key.replaceAll("_", " ")}`); cell.append(input); row.append(cell);
  }
  return row;
}
function renderSettings(settings) {
  state.settings = settings;
  const pricing = settings.pricing || {};
  $("setting-daily-limit").value = pricing.max_bids_per_day ?? 3;
  $("setting-period").value = pricing.bid_period_days ?? 5;
  $("setting-threshold").value = pricing.score_threshold ?? 70;
  const body = $("pricing-body"); body.replaceChildren();
  for (const [currency, rule] of Object.entries(pricing.budgets || {})) {
    body.append(pricingRow(currency, rule));
  }
  const ai = settings.ai || {};
  $("setting-ai-instructions").value = ai.instructions || "";
  $("setting-ai-tone").value = ["professional", "friendly", "concise"].includes(ai.tone) ? ai.tone : "professional";
  $("setting-ai-model").value = ai.model || "";
  $("ai-provider-status").textContent = ai.configured ? "AI provider configured" : "Template fallback active";
  $("ai-provider-status").className = `badge badge-${ai.configured ? "green" : "neutral"}`;
  const monitoring = settings.monitor || {}; const schedule = monitoring.schedule || {};
  $("setting-search-queries").value = (monitoring.search_queries || []).join("\n");
  $("setting-schedule-enabled").checked = Boolean(schedule.enabled);
  $("setting-timezone").value = schedule.timezone || "Asia/Karachi";
  $("setting-start").value = schedule.start || "09:00";
  $("setting-end").value = schedule.end || "18:00";
  document.querySelectorAll('input[name="schedule-day"]').forEach((input) => { input.checked = (schedule.days || []).includes(Number(input.value)); });
  const connection = settings.connection || {};
  const details = $("connection-details"); details.replaceChildren();
  const rows = [
    ["Freelancer.com API", "Official API integration; OAuth token remains on your server.", connection.freelancer_configured ? "Configured" : "Not configured", connection.freelancer_configured],
    ["API environment", "Production and sandbox use separate credentials and ledgers.", connection.environment || "production", false],
    ["AI proposal provider", "Optional OpenAI drafts; template fallback is always available.", ai.configured ? "Configured" : "Template fallback", ai.configured],
    ["Real submission gates", "These server-side gates cannot be changed from the dashboard.", connection.live_allowed ? "Permitted by server" : "Disabled · dry-run", false],
    ["Workspace data", "Demo mode is isolated from your Freelancer account.", connection.demo_mode ? "Synthetic demo data" : "Your actual workspace", false]
  ];
  for (const [name, detail, status, online] of rows) {
    const row = element("div", "connection-row"); const copy = element("div"); copy.append(element("strong", "", name), element("p", "", detail)); row.append(copy, badge(status, online ? "green" : "neutral")); details.append(row);
  }
}
async function saveSettings(section, payload, button) {
  await busy(button, async () => {
    const result = await api("/api/settings", {method: "PATCH", body: JSON.stringify({[section]: payload})});
    renderSettings(result); await refreshDashboard(); toast("Your preferences have been saved.");
  }, "Saving…");
}

function bindEvents() {
  window.addEventListener("hashchange", () => setView(window.location.hash.slice(1)));
  $("menu-button").addEventListener("click", () => {
    const open = !$("sidebar").classList.contains("open");
    $("sidebar").classList.toggle("open", open); $("sidebar-scrim").classList.toggle("hidden", !open); $("menu-button").setAttribute("aria-expanded", String(open));
  });
  $("sidebar-scrim").addEventListener("click", closeNavigation);
  document.addEventListener("keydown", (event) => { if (event.key === "Escape") closeNavigation(); });
  for (const id of ["safety-link", "help-button", "mode-settings-link"]) $(id).addEventListener("click", () => { window.location.hash = "settings"; setSettingsTab("connection"); });
  $("account-button").addEventListener("click", () => busy($("account-button"), async () => { await api("/api/auth/logout", {method: "POST", body: "{}"}); window.location.assign("/login"); }, "Signing out…"));
  $("monitor-toggle").addEventListener("change", async () => {
    const input = $("monitor-toggle"); input.disabled = true;
    try { renderDashboard(await api("/api/controls", {method: "POST", body: JSON.stringify({monitor_enabled: input.checked})})); toast(input.checked ? "Job monitoring is on." : "Monitoring and automation are paused."); }
    catch (failure) { input.checked = !input.checked; toast(failure.message, true); }
    finally { input.disabled = false; }
  });
  $("automation-toggle").addEventListener("click", () => busy($("automation-toggle"), async () => {
    const enabled = !state.dashboard?.auto_bidder_enabled;
    const result = await api("/api/controls", {method: "POST", body: JSON.stringify({auto_bidder_enabled: enabled})});
    renderDashboard(result);
    toast(enabled ? result.mode === "live" ? "Live automation enabled. Your schedule and limits apply." : "Automation is on in dry-run mode. No real bids will be submitted." : "Auto Bidder paused.");
  }));
  $("search-form").addEventListener("submit", (event) => {
    event.preventDefault();
    busy($("search-button"), async () => {
      await api("/api/search", {method: "POST", body: JSON.stringify({query: $("job-query").value.trim()})});
      state.offset = 0; await Promise.all([loadJobs(), refreshDashboard(), loadOverviewJobs()]); toast("Search complete. Your latest matches are ready to review.");
    }, "Searching live projects…");
  });
  $("refresh-jobs").addEventListener("click", () => busy($("refresh-jobs"), loadJobs, "Refreshing…"));
  $("refresh-history").addEventListener("click", () => busy($("refresh-history"), loadHistory, "Refreshing…"));
  let filterTimer;
  function changed() { clearTimeout(filterTimer); state.offset = 0; filterTimer = setTimeout(() => loadJobs().catch((failure) => toast(failure.message, true)), 250); }
  $("filters-form").addEventListener("change", changed);
  $("filters-form").addEventListener("submit", (event) => { event.preventDefault(); changed(); });
  $("job-query").addEventListener("input", changed);
  $("jobs-sort").addEventListener("change", changed);
  $("clear-filters").addEventListener("click", () => { $("filters-form").reset(); state.offset = 0; loadJobs().catch((failure) => toast(failure.message, true)); });
  $("page-prev").addEventListener("click", () => { state.offset = Math.max(0, state.offset - state.limit); loadJobs().catch((failure) => toast(failure.message, true)); });
  $("page-next").addEventListener("click", () => { state.offset += state.limit; loadJobs().catch((failure) => toast(failure.message, true)); });
  $("close-proposal").addEventListener("click", () => $("proposal-dialog").close());
  $("proposal-dialog").addEventListener("click", (event) => {
    const rect = $("proposal-dialog").getBoundingClientRect();
    if (event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom) $("proposal-dialog").close();
  });
  $("proposal-text").addEventListener("input", updateWords);
  $("generate-proposal").addEventListener("click", () => busy($("generate-proposal"), async () => {
    if (!state.selectedJob) return;
    const draft = await api(`/api/jobs/${encodeURIComponent(state.selectedJob.id)}/proposal`, {method: "POST", body: JSON.stringify({instructions: $("proposal-instructions").value.trim()})});
    $("proposal-text").value = draft.text || ""; $("proposal-provider").textContent = providerLabel(draft.provider); updateWords();
    state.selectedJob.draft = draft;
    toast(draft.provider === "openai" ? "Your AI draft is ready. Review and make it yours." : "Your engine template is ready. Configure an AI provider for generative drafts.");
    refreshDashboard().catch(() => {});
  }, "Creating your draft…"));
  $("save-proposal").addEventListener("click", () => busy($("save-proposal"), async () => {
    if (!state.selectedJob) return;
    const text = $("proposal-text").value.trim(); if (!text) throw new Error("Add some proposal text before saving.");
    const draft = await api(`/api/jobs/${encodeURIComponent(state.selectedJob.id)}/proposal`, {method: "PUT", body: JSON.stringify({text})});
    state.selectedJob.draft = draft; $("proposal-provider").textContent = providerLabel(draft.provider || "edited");
    toast("Draft saved. No bid has been submitted."); await Promise.all([refreshDashboard(), loadOverviewJobs()]);
    if (state.view === "jobs") await loadJobs();
  }, "Saving draft…"));
  $("copy-proposal").addEventListener("click", () => busy($("copy-proposal"), async () => {
    if (!$("proposal-text").value.trim()) throw new Error("There is no draft to copy yet.");
    if (!navigator.clipboard?.writeText) { $("proposal-text").focus(); $("proposal-text").select(); throw new Error("Clipboard is unavailable here. Your draft is selected; use your device's Copy command."); }
    await navigator.clipboard.writeText($("proposal-text").value); toast("Proposal copied to your clipboard.");
  }, "Copying…"));
  document.querySelectorAll("[data-settings-tab]").forEach((button) => button.addEventListener("click", () => setSettingsTab(button.dataset.settingsTab)));
  $("pricing-form").addEventListener("submit", (event) => {
    event.preventDefault(); const budgets = {};
    $("pricing-body").querySelectorAll("tr").forEach((row) => { budgets[row.dataset.currency] = {}; row.querySelectorAll("input").forEach((input) => { budgets[row.dataset.currency][input.dataset.budgetKey] = input.value; }); });
    saveSettings("pricing", {max_bids_per_day: Number($("setting-daily-limit").value), bid_period_days: Number($("setting-period").value), score_threshold: Number($("setting-threshold").value), budgets}, event.submitter);
  });
  $("add-currency").addEventListener("click", () => {
    const code = $("pricing-new-currency").value.trim().toUpperCase();
    if (!/^[A-Z]{3}$/.test(code)) { toast("Enter a three-letter currency code, such as GBP.", true); return; }
    if ([...$("pricing-body").querySelectorAll("tr")].some((row) => row.dataset.currency === code)) { toast("That currency already has a pricing rule.", true); return; }
    const row = pricingRow(code); $("pricing-body").append(row); row.querySelector("input").focus(); $("pricing-new-currency").value = "";
  });
  $("ai-form").addEventListener("submit", (event) => { event.preventDefault(); saveSettings("ai", {instructions: $("setting-ai-instructions").value, tone: $("setting-ai-tone").value, model: $("setting-ai-model").value.trim()}, event.submitter); });
  $("monitor-form").addEventListener("submit", (event) => {
    event.preventDefault(); saveSettings("monitor", {search_queries: $("setting-search-queries").value.split("\n").map((value) => value.trim()).filter(Boolean), schedule: {enabled: $("setting-schedule-enabled").checked, timezone: $("setting-timezone").value.trim(), days: [...document.querySelectorAll('input[name="schedule-day"]:checked')].map((input) => Number(input.value)), start: $("setting-start").value, end: $("setting-end").value}}, event.submitter);
  });
}

async function boot() {
  bindEvents();
  try {
    const session = await api("/api/session"); state.csrf = session.csrf_token;
    $("account-name").textContent = session.username || "Admin";
    $("account-initials").textContent = (session.username || "A").slice(0, 1).toUpperCase();
    setView(window.location.hash.slice(1) || "overview");
    const results = await Promise.allSettled([refreshDashboard(), api("/api/settings").then(renderSettings), loadOverviewJobs()]);
    for (const result of results) if (result.status === "rejected") toast(result.reason.message, true);
    setInterval(() => { if (!document.hidden) refreshDashboard().catch(() => {}); }, 30000);
  } catch (failure) { toast(failure.message, true); }
}
boot();
