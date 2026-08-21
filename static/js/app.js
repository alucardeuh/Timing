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

  document.querySelectorAll(".day-cell").forEach((cell) => {
    const handler = () => renderDetail(cell);
    cell.addEventListener("mouseenter", handler);
    cell.addEventListener("click", handler);
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
