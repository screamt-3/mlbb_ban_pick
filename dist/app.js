const state = { drafts: [], draftSequence: null, index: 0, sourceUrl: "" };

const elements = {
  view: document.querySelector("#draft-view"),
  select: document.querySelector("#draft-select"),
  previous: document.querySelector("#previous-draft"),
  next: document.querySelector("#next-draft"),
  vod: document.querySelector("#vod-link"),
  draftCount: document.querySelector("#draft-count"),
  pickCount: document.querySelector("#pick-count"),
  banCount: document.querySelector("#ban-count"),
  sequence: document.querySelector("#draft-sequence"),
  sequenceNote: document.querySelector("#sequence-note"),
  rulesBadge: document.querySelector("#rules-badge"),
};

const sequenceGroups = [
  { label: "Opening bans", start: 1, end: 4 },
  { label: "Opening picks", start: 5, end: 8 },
  { label: "Final bans", start: 9, end: 12 },
  { label: "Final picks", start: 13, end: 15 },
];

const imageExtensions = new Map([
  ["arlott", "webp"],
  ["marcel", "webp"],
  ["nolan", "webp"],
  ["sora", "webp"],
  ["suyou", "webp"],
  ["zhuxin", "webp"],
]);

function heroId(name) {
  return name.toLowerCase().replace(/\./g, "-").replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "");
}

function heroImage(name) {
  const id = heroId(name);
  return `./hero-assets/${id}.${imageExtensions.get(id) || "jpg"}`;
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function heroName(hero) {
  return typeof hero === "string" ? hero : hero.name;
}

function banCard(hero) {
  const item = typeof hero === "string" ? { name: hero } : hero;
  const marker = item.review
    ? '<span class="review-marker" title="Needs an independent portrait check" aria-label="Needs an independent portrait check">?</span>'
    : "";
  return `<figure class="hero-card ban-card">
    <img src="${heroImage(item.name)}" alt="" loading="lazy">
    ${marker}
    <figcaption>${escapeHtml(item.name)}</figcaption>
  </figure>`;
}

function pickCard(name, index, lastPick) {
  const isLast = name === lastPick;
  return `<figure class="hero-card pick-card${isLast ? " is-last" : ""}">
    <img src="${heroImage(name)}" alt="" loading="lazy">
    <span class="pick-number">${index + 1}</span>
    ${isLast ? '<span class="last-ribbon">LAST LOCK</span>' : ""}
    <figcaption>${escapeHtml(name)}</figcaption>
  </figure>`;
}

function sidePanel(side, draft) {
  const team = draft[side];
  const lastPick = draft.last_pick.side === side ? draft.last_pick.hero : null;
  const isSideToMiddle = draft.ordering?.direction === "side_to_middle";
  const orderHint = isSideToMiddle ? '<span class="order-hint">side → centre</span>' : "";
  const pickLabel = draft.ordering?.frame_stage === "pre_swap" ? "Pre-swap picks" : "Final picks";
  return `<article class="side-panel ${side}-panel">
    <header class="team-heading">
      <div>
        <p class="side-kicker">${side} side</p>
        <h2>${escapeHtml(team.team)}</h2>
      </div>
      <span class="side-chip">${side.toUpperCase()}</span>
    </header>
    <p class="section-label">Bans ${orderHint}</p>
    <div class="ban-list">${team.bans.map(banCard).join("")}</div>
    <p class="section-label">${pickLabel} ${orderHint}</p>
    <div class="pick-grid">${team.picks.map((hero, index) => pickCard(hero, index, lastPick)).join("")}</div>
  </article>`;
}

function mapHeroesToPhases(phases, draft) {
  if (draft.ordering?.direction !== "side_to_middle" || draft.ordering?.frame_stage !== "pre_swap") {
    return phases.map((phase) => ({ ...phase, heroes: [] }));
  }

  const offsets = {
    red: { ban: 0, pick: 0 },
    blue: { ban: 0, pick: 0 },
  };
  return phases.map((phase) => {
    const pool = draft[phase.side][`${phase.action}s`].map(heroName);
    const start = offsets[phase.side][phase.action];
    offsets[phase.side][phase.action] += phase.count;
    return { ...phase, heroes: pool.slice(start, start + phase.count) };
  });
}

function sequenceCard(phase, draft, lastPickPhase) {
  const team = draft[phase.side].team;
  const isLastLock = phase.phase_id === lastPickPhase?.phase_id
    && draft.last_pick.side === phase.side;
  const action = phase.action === "ban" ? "Ban" : "Pick";
  const countLabel = `${phase.count} ${phase.count === 1 ? "hero" : "heroes"}`;
  const heroes = phase.heroes.length ? phase.heroes : isLastLock ? [draft.last_pick.hero] : [];
  const heroList = heroes.length
    ? `<div class="sequence-heroes">
        ${isLastLock ? '<small class="last-lock-label">Observed last lock</small>' : ""}
        ${heroes.map((hero) => `<span class="sequence-hero">
          <img src="${heroImage(hero)}" alt="" loading="lazy">
          <strong>${escapeHtml(hero)}</strong>
        </span>`).join("")}
      </div>`
    : "";

  return `<li class="sequence-step ${phase.side}-step${isLastLock ? " is-last" : ""}">
    <div class="sequence-step-top">
      <span class="sequence-number">${String(phase.ordinal).padStart(2, "0")}</span>
      <span class="sequence-action">${action} · ${countLabel}</span>
    </div>
    <strong class="sequence-team">${escapeHtml(team)}</strong>
    <span class="sequence-side">${phase.side} side</span>
    ${heroList}
  </li>`;
}

function renderSequence(draft) {
  const rules = state.draftSequence;
  if (!rules?.phases?.length) {
    elements.sequence.innerHTML = '<p class="error-state">Draft sequence is unavailable.</p>';
    return;
  }

  const configuredPhases = [...rules.phases].sort((a, b) => a.ordinal - b.ordinal);
  const phases = mapHeroesToPhases(configuredPhases, draft);
  const lastPickPhase = [...phases].reverse().find((phase) => phase.action === "pick");
  elements.sequenceNote.textContent = draft.ordering?.validation === "user_confirmed"
    ? "Hero batches are populated from the user-confirmed pre-swap order, reading from each team’s outer side toward the centre."
    : "The rules establish team, action, and batch size. Hero batches will be populated after the pre-swap side-to-centre order is confirmed; only the observed last lock is named for now.";
  elements.sequence.innerHTML = sequenceGroups.map((group) => {
    const groupPhases = phases.filter((phase) => phase.ordinal >= group.start && phase.ordinal <= group.end);
    return `<section class="sequence-group" aria-label="${group.label}">
      <h3>${group.label}</h3>
      <ol>${groupPhases.map((phase) => sequenceCard(phase, draft, lastPickPhase)).join("")}</ol>
    </section>`;
  }).join("");
}

function render() {
  const draft = state.drafts[state.index];
  if (!draft) return;

  elements.select.value = String(state.index);
  const statusClass = draft.certification === "manual" ? " manual" : "";
  const statusText = draft.certification === "manual" ? "Visual check" : "Timer certified";
  const sourceSeconds = draft.requested_seconds;
  elements.vod.href = `${state.sourceUrl}&t=${sourceSeconds}s`;

  elements.view.innerHTML = `${sidePanel("red", draft)}
    <aside class="versus" aria-label="Draft details">
      <p class="game-number">Game ${draft.game}</p>
      <p class="versus-mark">VS</p>
      <div class="last-pick">
        <p class="last-label">Last pick</p>
        <strong>${escapeHtml(draft.last_pick.hero)}</strong>
        <span>${escapeHtml(draft.last_pick.team)}</span>
      </div>
      <p class="timestamp">Requested<br><strong>${escapeHtml(draft.requested)}</strong><br><br>Final<br><strong>${escapeHtml(draft.final)}</strong></p>
      <span class="status-chip${statusClass}">${statusText}</span>
    </aside>
    ${sidePanel("blue", draft)}`;
  renderSequence(draft);
}

function move(offset) {
  state.index = (state.index + offset + state.drafts.length) % state.drafts.length;
  render();
}

async function loadDrafts() {
  try {
    const response = await fetch("./drafts.json");
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const data = await response.json();
    state.drafts = data.drafts;
    state.draftSequence = data.draft_sequence;
    state.sourceUrl = data.source_url;
    elements.rulesBadge.textContent = state.draftSequence?.rules_id || "Draft rules unavailable";

    elements.draftCount.textContent = state.drafts.length;
    elements.pickCount.textContent = state.drafts.reduce((total, draft) => total + draft.red.picks.length + draft.blue.picks.length, 0);
    elements.banCount.textContent = state.drafts.reduce((total, draft) => total + draft.red.bans.length + draft.blue.bans.length, 0);
    elements.select.replaceChildren(...state.drafts.map((draft, index) => {
      const option = document.createElement("option");
      option.value = String(index);
      option.textContent = `${draft.series} · Game ${draft.game}`;
      return option;
    }));
    render();
  } catch (error) {
    elements.view.innerHTML = `<p class="error-state">Could not load draft data: ${escapeHtml(error.message)}</p>`;
  }
}

elements.previous.addEventListener("click", () => move(-1));
elements.next.addEventListener("click", () => move(1));
elements.select.addEventListener("change", (event) => {
  state.index = Number(event.target.value);
  render();
});
document.addEventListener("keydown", (event) => {
  if (event.target.matches("select")) return;
  if (event.key === "ArrowLeft") move(-1);
  if (event.key === "ArrowRight") move(1);
});

loadDrafts();
