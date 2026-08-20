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
    const unit = durationUnit.value;
    const weeks = unit === "months" ? dur * WEEKS_PER_MONTH : dur;
    const totalDays = Math.round(dpw * weeks * 100) / 100;

    previewDays.textContent = totalDays > 0 ? totalDays + " j" : "—";

    const rate = parseFloat(dayRate.value);
    const price = parseFloat(priceTotal.value);
    let total = null;
    if (!isNaN(rate) && rate > 0) {
      total = Math.round(rate * totalDays);
    } else if (!isNaN(price) && price > 0) {
      total = price;
    }
    previewPrice.textContent = total !== null ? total.toLocaleString("fr-FR") + " " : "—";
  }

  [daysPerWeek, durationValue, durationUnit, dayRate, priceTotal].forEach((el) => {
    el.addEventListener("input", computePreview);
    el.addEventListener("change", computePreview);
  });
  computePreview();
})();

(function capacityDayDetail() {
  const detailBoxes = document.querySelectorAll("#day-detail");
  if (!detailBoxes.length) return;

  function renderDetail(box, dateLabel, pct, contributorsJson) {
    let contributors = [];
    try { contributors = JSON.parse(contributorsJson || "[]"); } catch (e) { contributors = []; }

    if (!contributors.length) {
      box.innerHTML = `<span class="dd-date">${dateLabel}</span> — journée libre`;
      return;
    }
    const items = contributors
      .map((c) => `<li>${c.name}${c.provisional ? " (provisoire)" : ""} — ${c.pct}%</li>`)
      .join("");
    box.innerHTML = `<span class="dd-date">${dateLabel}</span> — ${pct}% de charge<ul>${items}</ul>`;
  }

  document.querySelectorAll(".day-cell").forEach((cell) => {
    const handler = () => {
      const box = document.getElementById("day-detail");
      if (!box) return;
      renderDetail(box, cell.dataset.date, cell.dataset.pct, cell.dataset.contributors);
    };
    cell.addEventListener("mouseenter", handler);
    cell.addEventListener("click", handler);
  });
})();
