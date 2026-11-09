(function () {
  var $ = function (id) { return document.getElementById(id); };
  var form = $("permit-form");
  if (!form) return;

  function num(id) {
    var el = $(id);
    if (!el) return null;
    var raw = parseFloat(el.value);
    return Number.isFinite(raw) ? raw : null;
  }
  function money(v) {
    return v === null ? "—" : v.toLocaleString("zh-CN", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  }
  function pct(v) {
    return v === null ? "—" : (v * 100).toFixed(2) + "%";
  }
  function set(id, text) {
    var el = $(id);
    if (el) el.textContent = text;
  }

  function recalc() {
    var total = 0, anyScore = false;
    ["score_market", "score_sector", "score_stock", "score_corr"].forEach(function (id) {
      var v = num(id);
      if (v !== null) { total += v; anyScore = true; }
    });
    set("calc-total", anyScore ? String(total) : "—");

    var buy = num("buy_price"), stop = num("stop_price"), target = num("target_price");
    var qty = num("qty"), cap = num("risk_cap");

    var amount = buy !== null && qty !== null ? buy * qty : null;
    var stopDist = buy !== null && stop !== null && buy !== 0 ? Math.abs(buy - stop) / Math.abs(buy) : null;
    var upside = buy !== null && target !== null && buy !== 0 ? target / buy - 1 : null;
    var risk = buy !== null && stop !== null && qty !== null ? Math.abs(buy - stop) * qty : null;
    var rr = buy !== null && stop !== null && target !== null && buy !== stop ? (target - buy) / (buy - stop) : null;

    set("calc-amount", money(amount));
    set("calc-stopdist", pct(stopDist));
    set("calc-upside", pct(upside));
    set("calc-rr", rr === null ? "—" : rr.toFixed(2));
    set("calc-risk", money(risk));
    set("calc-budget", risk !== null && cap !== null ? (risk <= cap ? "预算内" : "超预算") : "—");
  }

  form.addEventListener("input", recalc);
  form.addEventListener("change", recalc);
  recalc();
})();