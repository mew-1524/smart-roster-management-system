// Confirmation dialog for any form with data-confirm="message"
document.addEventListener("submit", function (e) {
  const form = e.target;
  if (!form.dataset.confirm || form.dataset.confirmed) return;
  e.preventDefault();
  const modalEl = document.getElementById("confirmModal");
  modalEl.querySelector(".modal-body").textContent = form.dataset.confirm;
  const modal = bootstrap.Modal.getOrCreateInstance(modalEl);
  const ok = modalEl.querySelector("[data-ok]");
  const handler = function () { ok.removeEventListener("click", handler); form.dataset.confirmed = "1"; modal.hide(); form.submit(); };
  ok.addEventListener("click", handler);
  modal.show();
});

// Chart.js helper used by the dashboard
window.drawChart = function (id, type, labels, data, opts) {
  const el = document.getElementById(id);
  if (!el || !window.Chart) return;
  if (!labels.length || !data.some(function (v) { return v > 0; })) {
    el.parentElement.innerHTML = '<div class="empty"><i class="bi bi-bar-chart"></i>No data for this month yet.</div>';
    return;
  }
  const palette = ["#2563eb", "#16a34a", "#f59e0b", "#dc2626", "#7c3aed", "#0891b2", "#db2777", "#65a30d"];
  new Chart(el, {
    type: type,
    data: { labels: labels, datasets: [Object.assign({ data: data, backgroundColor: (opts && opts.colors) || (type === "line" ? "rgba(37,99,235,.15)" : palette), borderColor: type === "line" ? "#2563eb" : undefined, fill: type === "line", tension: .3, borderRadius: 6 }, (opts && opts.dataset) || {})] },
    options: Object.assign({ responsive: true, maintainAspectRatio: false, plugins: { legend: { display: type === "doughnut", position: "bottom" } } }, (opts && opts.options) || {})
  });
};
