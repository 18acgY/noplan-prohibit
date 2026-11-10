(() => {
  const POLL_MS = 3000;

  const pad = (n) => String(n).padStart(2, "0");

  const fmtDuration = (seconds) => {
    const s = Math.max(0, Math.floor(seconds));
    const h = Math.floor(s / 3600);
    const m = Math.floor((s % 3600) / 60);
    const sec = s % 60;
    return h > 0 ? `${h}:${pad(m)}:${pad(sec)}` : `${pad(m)}:${pad(sec)}`;
  };

  const parseTime = (value) => {
    if (!value) return null;
    const t = Date.parse(value);
    return Number.isNaN(t) ? null : t;
  };

  const elapsedFor = (node) => {
    const started = parseTime(node.dataset.started);
    if (!started) return null;
    const finished = parseTime(node.dataset.finished);
    return ((finished || Date.now()) - started) / 1000;
  };

  const paintElapsed = (node) => {
    const el = node.querySelector("[data-elapsed]");
    if (!el) return;
    const seconds = elapsedFor(node);
    if (seconds === null) {
      el.textContent = "";
      return;
    }
    el.textContent = `已用 ${fmtDuration(seconds)}`;
  };

  const paintStages = (node, stageIndex, stageLabel, status) => {
    node.querySelectorAll(".step").forEach((step, index) => {
      step.classList.toggle("done", stageIndex !== null && index < stageIndex);
      step.classList.toggle("current", stageIndex !== null && index === stageIndex);
    });
    const label = node.querySelector("[data-label]");
    if (!label) return;
    if (status === "failed") {
      label.textContent = `失败于「${stageLabel || "运行"}」`;
    }
    if (status === "done") label.textContent = "已完成";
    node.dataset.stage = stageLabel || "";
  };

  const paintSubsteps = (node, detail, stageIndex) => {
    const box = node.querySelector("[data-substeps]");
    if (!box) return;
    const parts = box.querySelectorAll(".substep");
    if (!parts.length) return;
    if (stageIndex === 1 || (stageIndex !== null && stageIndex > 1)) {
      box.hidden = false;
    }
  };

  const pollOnce = async (node) => {
    const id = node.dataset.analysisId;
    if (!id) return true;
    try {
      const resp = await fetch(`/analyses/${id}/status.json`, {
        headers: { Accept: "application/json" },
        cache: "no-store",
      });
      if (!resp.ok) return true;
      const data = await resp.json();
      if (["done", "failed", "cancelled"].includes(data.status)) {
        window.location.reload();
        return false;
      }
      node.dataset.status = data.status;
      node.dataset.stage = data.stage || "";
      node.dataset.queueAhead = String(data.queue_ahead ?? 0);
      node.dataset.finished = data.finished_at || "";
      if (data.started_at) node.dataset.started = data.started_at;

      if (data.status === "running") {
        const label = node.querySelector("[data-label]");
        if (label) label.textContent = data.cancel_requested ? "取消中…" : data.stage_label || "运行中";
        paintStages(node, data.stage_index, data.stage_label, data.status);
        paintSubsteps(node, data.detail || {}, data.stage_index);
      } else if (data.status === "queued") {
        const ahead = data.queue_ahead ?? 0;
        const label = node.querySelector("[data-label]");
        if (label) label.textContent = ahead > 0 ? `排队中：前面还有 ${ahead} 个任务` : "排队中，等待 worker 领取";
      }
      paintElapsed(node);
      return true;
    } catch (err) {
      return true;
    }
  };

  const start = (node) => {
    if (node.dataset.polling === "1") return;
    node.dataset.polling = "1";
    paintElapsed(node);
    setInterval(() => paintElapsed(node), 1000);
    const loop = async () => {
      if (document.hidden) return;
      const keepGoing = await pollOnce(node);
      if (!keepGoing) return;
      setTimeout(loop, POLL_MS);
    };
    setTimeout(loop, POLL_MS);
    document.addEventListener("visibilitychange", () => {
      if (!document.hidden) paintElapsed(node);
    });
  };

  document.querySelectorAll(".progress[data-analysis-id]").forEach((node) => {
    if (["queued", "running"].includes(node.dataset.status)) start(node);
  });
})();