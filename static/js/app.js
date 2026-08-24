// Timing — interactions côté client, sans framework.

(function projectFormPreview() {
  const daysPerWeek = document.getElementById("days_per_week");
  const durationValue = document.getElementById("duration_value");
  const durationUnit = document.getElementById("duration_unit");
  const dayRate = document.getElementById("day_rate");
  const priceTotal = document.getElementById("price_total");
  const previewDays = document.getElementById("preview-days");
  const previewPrice = document.getElementById("preview-price");

  if (!daysPerWeek || !previewDays) return;

  const WEEKS_PER_MONTH = 4.345;

  function computePreview() {
    const dpw = parseFloat(daysPerWeek.value) || 0;
    const dur = parseFloat(durationValue.value) || 0;
    const weeks = durationUnit.value === "months" ? dur * WEEKS_PER_MONTH : dur;
    const totalDays = Math.round(dpw * weeks * 100) / 100;

    previewDays.textContent = totalDays > 0 ? totalDays + " j" : "—";

    const rate = parseFloat(dayRate.value);
    const price = parseFloat(priceTotal.value);
    let total = null;
    if (!isNaN(rate) && rate > 0) total = Math.round(rate * totalDays);
    else if (!isNaN(price) && price > 0) total = price;

    previewPrice.textContent = total !== null ? total.toLocaleString("fr-FR") : "—";
  }

  [daysPerWeek, durationValue, durationUnit, dayRate, priceTotal].forEach((el) => {
    el.addEventListener("input", computePreview);
    el.addEventListener("change", computePreview);
  });
  computePreview();
})();

(function capacityDayDetail() {
  const box = document.getElementById("day-detail");
  if (!box) return;

  // Tout est construit avec createElement + textContent, jamais innerHTML :
  // les noms de projets viennent de la saisie utilisateur, et les injecter
  // en HTML permettrait à un nom comme "<img onerror=...>" d'exécuter du
  // script au simple survol d'un jour.
  function renderDetail(cell) {
    box.textContent = "";

    const dateSpan = document.createElement("span");
    dateSpan.className = "dd-date";
    dateSpan.textContent = cell.dataset.date;
    box.appendChild(dateSpan);

    const off = cell.dataset.off;
    if (off) {
      box.appendChild(document.createTextNode(" — non travaillé (" + off + ")"));
      return;
    }

    let contributors = [];
    try { contributors = JSON.parse(cell.dataset.contributors || "[]"); } catch (e) { contributors = []; }

    if (!contributors.length) {
      box.appendChild(document.createTextNode(" — journée libre"));
      return;
    }

    box.appendChild(document.createTextNode(" — " + cell.dataset.pct + "% de charge"));
    const list = document.createElement("ul");
    contributors.forEach((c) => {
      const li = document.createElement("li");
      let suffixe = "";
      if (c.provisional) suffixe += " (provisoire)";
      if (c.overrun) suffixe += " (au-delà de sa fin prévue)";
      li.textContent = c.name + suffixe + " — " + c.pct + "%";
      list.appendChild(li);
    });
    box.appendChild(list);
  }

  // Le détail d'une journée n'existait qu'au survol et au clic : sans
  // tabindex ni rôle, la carte de charge était entièrement hors d'atteinte
  // au clavier, et invisible pour un lecteur d'écran. Les flèches gauche et
  // droite passent d'un jour à l'autre, comme dans la grille hebdo.
  const cells = Array.from(document.querySelectorAll(".day-cell, .cap-col"));
  cells.forEach((cell, i) => {
    const handler = () => renderDetail(cell);
    cell.addEventListener("mouseenter", handler);
    cell.addEventListener("click", handler);
    cell.addEventListener("focus", handler);

    if (!cell.hasAttribute("tabindex")) cell.setAttribute("tabindex", "0");
    if (!cell.hasAttribute("role")) cell.setAttribute("role", "button");
    if (!cell.hasAttribute("aria-label")) {
      const off = cell.dataset.off;
      cell.setAttribute("aria-label", cell.dataset.date +
        (off ? " — non travaillé (" + off + ")" : " — " + (cell.dataset.pct || 0) + "% de charge"));
    }

    cell.addEventListener("keydown", (e) => {
      let target = null;
      if (e.key === "ArrowRight") target = cells[i + 1];
      else if (e.key === "ArrowLeft") target = cells[i - 1];
      else if (e.key === "Enter" || e.key === " ") { e.preventDefault(); handler(); return; }
      if (target) { e.preventDefault(); target.focus(); }
    });
  });
})();

(function weekGridNavigation() {
  // Flèches haut/bas pour passer d'une ligne à l'autre dans la même
  // colonne : saisir une semaine se fait au clavier, pas à la souris.
  const inputs = Array.from(document.querySelectorAll(".wg-input"));
  if (!inputs.length) return;

  const cols = 7;
  inputs.forEach((input, i) => {
    input.addEventListener("keydown", (e) => {
      let target = null;
      if (e.key === "ArrowDown") target = inputs[i + cols];
      else if (e.key === "ArrowUp") target = inputs[i - cols];
      if (target) { e.preventDefault(); target.focus(); target.select(); }
    });
    input.addEventListener("focus", () => input.select());
  });
})();

(function checkboxPills() {
  document.querySelectorAll(".checkbox-pill input").forEach((input) => {
    input.addEventListener("change", () => {
      input.closest(".checkbox-pill").classList.toggle("is-checked", input.checked);
    });
  });
})();

(function dayPresets() {
  // Boutons de préréglage de la vue jour : ils remplissent le champ, ils
  // n'envoient rien — l'enregistrement reste un geste explicite.
  document.querySelectorAll(".day-preset").forEach((button) => {
    button.addEventListener("click", () => {
      const field = document.getElementsByName(button.dataset.target)[0];
      if (!field) return;
      const value = button.dataset.value;
      field.value = value === "0" ? "" : value;
      field.focus();
    });
  });
})();


(function undoBar() {
  // La barre d'annulation disparaît au bout de dix secondes : passé ce
  // délai, elle n'annule plus le geste qu'on vient de faire, elle occupe
  // seulement le bas de l'écran. La corbeille prend le relais.
  const bar = document.getElementById("undo-bar");
  if (!bar) return;
  setTimeout(() => bar.classList.add("is-gone"), 10000);
})();

(function commandPalette() {
  // Aller à un projet demandait de passer par Projets puis de chercher. Une
  // palette évite les deux clics, et sert aussi de menu complet sur mobile,
  // où le rail ne tient plus.
  const overlay = document.getElementById("palette");
  const input = document.getElementById("palette-input");
  const list = document.getElementById("palette-results");
  const help = document.getElementById("shortcuts");
  if (!overlay || !input || !list) return;

  let index = null;   // chargé à la première ouverture, pas au chargement de
                      // la page : la palette ne sert pas à chaque visite.
  let matches = [];
  let active = 0;

  function close(el) { el.hidden = true; }

  function openHelp() {
    if (help) { help.hidden = false; }
  }

  async function load() {
    if (index) return index;
    try {
      const res = await fetch("/api/recherche", { headers: { "Accept": "application/json" } });
      index = await res.json();
    } catch (e) {
      index = { projets: [], clients: [], pages: [] };
    }
    return index;
  }

  function normalise(text) {
    // Sans cette normalisation, chercher « facturation » ne trouvait pas
    // « Facturation », et « prevision » ne trouvait pas « prévisionnel ».
    return (text || "").toLowerCase().normalize("NFD").replace(/[\u0300-\u036f]/g, "");
  }

  function build(query) {
    const q = normalise(query);
    const out = [];
    (index.pages || []).forEach((p) => {
      if (!q || normalise(p.nom).includes(q)) out.push({ label: p.nom, meta: "page", url: p.url });
    });
    (index.projets || []).forEach((p) => {
      if (!q || normalise(p.nom + " " + p.client).includes(q)) {
        out.push({ label: p.nom, meta: p.client || p.statut, url: "/projects/" + p.id });
      }
    });
    (index.clients || []).forEach((c) => {
      if (q && normalise(c.nom).includes(q)) {
        out.push({ label: c.nom, meta: "client", url: "/clients/" + c.id });
      }
    });
    return out.slice(0, 20);
  }

  function render() {
    list.textContent = "";
    matches.forEach((m, i) => {
      // createElement + textContent, jamais innerHTML : les noms de projets
      // et de clients viennent de la saisie utilisateur.
      const li = document.createElement("li");
      if (i === active) li.className = "is-active";
      const name = document.createElement("span");
      name.textContent = m.label;
      const meta = document.createElement("span");
      meta.className = "pal-meta";
      meta.textContent = m.meta || "";
      li.appendChild(name);
      li.appendChild(meta);
      li.addEventListener("click", () => { window.location.href = m.url; });
      list.appendChild(li);
    });
  }

  async function open() {
    overlay.hidden = false;
    input.value = "";
    await load();
    matches = build("");
    active = 0;
    render();
    input.focus();
  }

  input.addEventListener("input", () => {
    matches = build(input.value);
    active = 0;
    render();
  });

  input.addEventListener("keydown", (e) => {
    if (e.key === "ArrowDown") { e.preventDefault(); active = Math.min(active + 1, matches.length - 1); render(); }
    else if (e.key === "ArrowUp") { e.preventDefault(); active = Math.max(active - 1, 0); render(); }
    else if (e.key === "Enter" && matches[active]) { e.preventDefault(); window.location.href = matches[active].url; }
  });

  overlay.addEventListener("click", (e) => { if (e.target === overlay) close(overlay); });
  if (help) help.addEventListener("click", (e) => { if (e.target === help) close(help); });

  document.querySelectorAll("[data-open-palette]").forEach((b) => {
    b.addEventListener("click", open);
  });

  // Raccourcis globaux. Ignorés dès que le curseur est dans un champ :
  // sinon taper « n » dans une note ouvrait un nouveau projet.
  let pendingGoto = false;
  document.addEventListener("keydown", (e) => {
    const tag = (e.target.tagName || "").toLowerCase();
    const typing = tag === "input" || tag === "textarea" || tag === "select" || e.target.isContentEditable;

    if (e.key === "Escape") {
      close(overlay);
      if (help) close(help);
      return;
    }
    if (typing || e.metaKey || e.ctrlKey || e.altKey) return;

    if (e.key === "/") { e.preventDefault(); open(); return; }
    if (e.key === "?") { e.preventDefault(); openHelp(); return; }

    if (pendingGoto) {
      pendingGoto = false;
      const url = (window.TIMING_ROUTES || {})[e.key.toLowerCase()];
      if (url) { e.preventDefault(); window.location.href = url; }
      return;
    }
    if (e.key === "g") { pendingGoto = true; setTimeout(() => { pendingGoto = false; }, 1200); return; }
    if (e.key === "n") {
      const url = (window.TIMING_ROUTES || {}).n;
      if (url) { e.preventDefault(); window.location.href = url; }
    }
  });
})();
