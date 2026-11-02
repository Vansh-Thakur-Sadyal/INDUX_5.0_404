// ============================================================
//  dashboard2.js  —  FULL FIXED VERSION
//
//  Reads from localStorage "pipeline_result" (set by dashboard1.js).
//  Exact backend structure (from orchestrator.py via server.py):
//  {
//    status: "success",
//    pipeline_output: {
//      total_time: N,
//      agent_outputs: {
//        agent1: [ ...16 field records ],
//        agent2: {
//          agent2_output: { risks:[...], disease, risk_score, status },
//          input_used: { crop, temperature, humidity, soil_moisture, ndvi }
//        },
//        agent3: {
//          agent, strategy_used, priority, budget, water, llm_reason,
//          plan: { actions:[{type,cost_inr,water_liters,start,end,notes},...],
//                  status, total_cost, total_risk_reduction }
//        }
//      },
//      final_output: <same as agent_outputs.agent3>
//    }
//  }
//  Additionally "pipeline_inputs" stores { soil_moisture, temperature, humidity }
// ============================================================

// ── Helpers ──────────────────────────────────────────────────
function setText(el, val) {
    if (el && val !== undefined && val !== null) el.textContent = String(val);
}
function setHTML(el, html) {
    if (el && html !== undefined && html !== null) el.innerHTML = String(html);
}
// LLM insights arrive as markdown — strip the syntax for plain display
function plainText(s) {
    return String(s).replace(/[#*`_>]/g, "").replace(/\n+/g, " ").replace(/\s{2,}/g, " ").trim();
}

// ── Last-Updated ticker ───────────────────────────────────────
(function () {
    const el = document.getElementById("lastUpdated");
    if (!el) return;
    let secs = 0;
    setInterval(() => {
        secs++;
        el.textContent = secs < 60
            ? secs + "s ago"
            : Math.floor(secs / 60) + "m " + (secs % 60) + "s ago";
    }, 1000);
})();

// ── Run history shared with dashboard1 (real chart data) ─────
function getRunHistory() {
    try { return JSON.parse(localStorage.getItem("run_history")) || []; }
    catch { return []; }
}
function drawEmpty(canvas, msg) {
    const ctx = canvas.getContext("2d");
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    ctx.fillStyle = "#9aa89a";
    ctx.font = "10px 'IBM Plex Mono',monospace";
    ctx.textAlign = "center";
    ctx.fillText(msg, canvas.width / 2, canvas.height / 2);
}

// ── Risk Chart (Agent 2) — readable rows: name, bar, severity ─
function drawRiskChart(risks) {
    const box = document.getElementById("riskBars");
    if (!box) return;
    if (!risks || risks.length === 0) {
        box.innerHTML = '<div style="font-size:11px;color:#4caf50">✅ No risks detected in this run.</div>';
        return;
    }
    const sevMap = { LOW: 0.33, MEDIUM: 0.66, HIGH: 1 };
    box.innerHTML = risks.map(r => {
        const name  = String(r[0] || "risk");
        // r[2] = real computed severity (0-1); fall back to the level
        const sev   = (r.length > 2 && !isNaN(r[2])) ? Number(r[2]) : (sevMap[r[1]] || 0.33);
        const pct   = Math.round(sev * 100);
        const color = r[1] === "HIGH" ? "#e53935" : r[1] === "MEDIUM" ? "#f9a825" : "#66bb6a";
        return `
        <div style="display:flex;align-items:center;gap:8px;margin:5px 0">
          <span style="flex:0 0 140px;font-size:11px;color:#444;white-space:nowrap;
                       overflow:hidden;text-overflow:ellipsis" title="${name}">${name}</span>
          <div style="flex:1;background:#eee;border-radius:4px;height:10px">
            <div style="width:${pct}%;background:${color};height:10px;border-radius:4px"></div>
          </div>
          <span style="flex:0 0 82px;font-size:10px;font-weight:bold;color:${color}">${r[1]} · sev ${pct}%</span>
        </div>`;
    }).join("");
}

// ── Performance Chart (Agent 4) — success % across runs ──────
function drawPerfChart() {
    const canvas = document.getElementById("perfChart");
    if (!canvas) return;
    let runs = getRunHistory();
    if (runs.length === 0) { drawEmpty(canvas, "no runs yet"); return; }
    if (runs.length === 1) runs = [runs[0], runs[0]]; // flat line for a single run
    const ctx = canvas.getContext("2d");
    const W = canvas.width, H = canvas.height;
    const perf = runs.map(r => r.success ?? 0);
    const maxV = 100, minV = 0;

    // Growth tag: change between the last two runs
    const tag = document.getElementById("growthTag");
    if (tag && perf.length >= 2) {
        const d = perf[perf.length - 1] - perf[perf.length - 2];
        tag.textContent = (d >= 0 ? "+" : "") + d + "% vs last run";
    }
    const pad  = { l:24, r:6, t:6, b:6 };
    const cW = W - pad.l - pad.r, cH = H - pad.t - pad.b;
    const sx = i => pad.l + (i / (perf.length - 1)) * cW;
    const sy = v => pad.t + cH - ((v - minV) / (maxV - minV)) * cH;
    ctx.clearRect(0, 0, W, H);

    // Y axis: success % values so the scale is explicit
    ctx.fillStyle = "#999";
    ctx.font = "8px 'IBM Plex Mono',monospace";
    ctx.textAlign = "left";
    [0, 50, 100].forEach(v => ctx.fillText(v + "%", 0, sy(v) + 3));

    // Show each run's success value at its point
    ctx.textAlign = "center";
    perf.forEach((v, i) => ctx.fillText(v + "%", sx(i), sy(v) - 7));

    // X axis: real run times instead of the fake DAY 1..TODAY labels
    const lblRow = document.querySelector(".perf-labels");
    if (lblRow) lblRow.innerHTML =
        runs.map(r => `<span>${r.t || ""}</span>`).join("");
    const g = ctx.createLinearGradient(0, 0, 0, H);
    g.addColorStop(0, "rgba(45,106,45,0.25)");
    g.addColorStop(1, "rgba(45,106,45,0)");
    ctx.beginPath();
    ctx.moveTo(sx(0), sy(perf[0]));
    perf.forEach((v, i) => { if (i > 0) ctx.lineTo(sx(i), sy(v)); });
    ctx.lineTo(sx(perf.length - 1), H); ctx.lineTo(sx(0), H); ctx.closePath();
    ctx.fillStyle = g; ctx.fill();
    ctx.beginPath();
    ctx.moveTo(sx(0), sy(perf[0]));
    perf.forEach((v, i) => { if (i > 0) ctx.lineTo(sx(i), sy(v)); });
    ctx.strokeStyle = "#2d6a2d"; ctx.lineWidth = 2.5; ctx.stroke();
    perf.forEach((v, i) => {
        const isLast = i === perf.length - 1;
        ctx.beginPath();
        ctx.arc(sx(i), sy(v), isLast ? 5 : 3, 0, Math.PI * 2);
        ctx.fillStyle = "#2d6a2d"; ctx.fill();
        if (isLast) {
            ctx.beginPath();
            ctx.arc(sx(i), sy(v), 5, 0, Math.PI * 2);
            ctx.strokeStyle = "#fff"; ctx.lineWidth = 2; ctx.stroke();
        }
    });
}

// ── Main dashboard update ─────────────────────────────────────
function updateDashboard(serverResp, inputs) {
    const po    = serverResp.pipeline_output || {};
    const ao    = po.agent_outputs           || {};

    // ── Agent 1 data (sensor inputs the user set) ─────────────
    // We use pipeline_inputs (saved separately) for exact user values
    const soil = inputs?.soil_moisture ?? null;
    const temp = inputs?.temperature   ?? null;
    const hum  = inputs?.humidity      ?? null;

    const envVals = document.querySelectorAll(".env-value");
    // env-value order in HTML: soil, temperature, humidity, reliability
    if (envVals[0] && soil !== null) envVals[0].textContent = soil + "%";
    if (envVals[1] && temp !== null) envVals[1].textContent = temp + "°C";
    if (envVals[2] && hum  !== null) envVals[2].textContent = hum  + "%";
    // envVals[3] is reliability — leave as-is

    // ── Agent 2 ────────────────────────────────────────────────
    const a2wrap    = ao.agent2         || {};
    const a2out     = a2wrap.agent2_output || a2wrap;
    const riskScore = Number(a2out.risk_score ?? 2);
    const riskLabel = a2out.status || (riskScore > 5 ? "HIGH" : riskScore > 2 ? "ALERT" : "LOW");

    // ── Agent 3 / final ───────────────────────────────────────
    const a3    = ao.agent3         || {};
    const final = po.final_output   || a3;

    const budget       = final.budget         || a3.budget         || "—";
    const water        = final.water          || a3.water          || "—";
    const stratUsed    = final.strategy_used  || a3.strategy_used  || "—";
    const priority     = final.priority       || a3.priority       || "balanced";
    let   llmReason    = final.llm_reason     || a3.llm_reason     || "";
    const planActions  = final.plan?.actions  || a3.plan?.actions  || [];
    const planStatus   = final.plan?.status   || a3.plan?.status   || "—";
    // Real keys from OptimizedPlan.to_dict(): total_cost_inr,
    // total_water_liters, expected_risk_reduction (0-1)
    const totalCost    = final.plan?.total_cost_inr ?? a3.plan?.total_cost_inr;
    const riskReduc    = final.plan?.expected_risk_reduction ?? a3.plan?.expected_risk_reduction;

    // llm_reason must render as text even if the model returned an object
    if (llmReason && typeof llmReason !== "string") {
        llmReason = JSON.stringify(llmReason);
    }

    // ── Real risk bars for Agent 2 chart ──────────────────────
    drawRiskChart(a2out.risks || []);

    // ── Session meta: real run timestamp ──────────────────────
    const runTs = po.pipeline_log?.timestamp;
    if (runTs) setText(document.getElementById("sessionMeta"),
        "Run: " + new Date(runTs + "Z").toLocaleString());

    // ── Reliability: real score from Agent 1 records ──────────
    const rec0 = Array.isArray(ao.agent1) ? ao.agent1[0] : null;
    const envVals2 = document.querySelectorAll(".env-value");
    if (envVals2[3]) {
        envVals2[3].textContent = (rec0 && rec0.reliability_score !== undefined)
            ? "✅ " + (rec0.reliability_score * 10).toFixed(1) + "/10"
            : "— no records";
    }

    // ── Future Strategy: real Agent 5 + Agent 4 insights ──────
    const learning = final.learning || a3.learning || "";
    if (learning) {
        setText(document.getElementById("futureDesc"),
            plainText(learning).slice(0, 400));
    }
    const monitoring = final.monitoring_feedback || a3.monitoring_feedback || {};
    const insight = monitoring.llm_insight;
    if (insight && insight !== "No AI insight available") {
        setText(document.getElementById("futureRecTitle"), "From this run's monitoring");
        setText(document.getElementById("futureRecDesc"), plainText(insight).slice(0, 400));
    } else if (monitoring.feedback) {
        setText(document.getElementById("futureRecTitle"), "Monitoring feedback");
        setText(document.getElementById("futureRecDesc"),
            [].concat(monitoring.feedback).join(" · "));
    }

    // ── KPI Cards ─────────────────────────────────────────────
    // Overall Success = 100 - (risk_score * 8), clamped 0-100
    // (same formula as the system-health badge on the main page)
    const successPct = Math.max(0, Math.min(100, Math.round(100 - riskScore * 8)));
    setText(document.querySelector(".kpi-value.green"), successPct + "%");
    setText(document.querySelector(".kpi-value.blue"),
        riskScore > 5 ? "HIGH" : riskScore > 2 ? "MODERATE" : "MILD");
    if (riskReduc !== undefined) {
        setText(document.querySelector(".kpi-value.gold"),
            "-" + Math.round(riskReduc * 100) + "%");
    }

    // ── Action Cards (all three, from the real plan) ──────────
    const actionNames = document.querySelectorAll(".action-name");
    for (let i = 0; i < 3; i++) {
        const nameEl = actionNames[i];
        const metaEl = document.getElementById("action-meta-" + i);
        const tagEl  = document.getElementById("action-tag-" + i);
        const act    = planActions[i];

        if (act && typeof act === "object") {
            const name = act.name || act.type || "Action";
            if (nameEl) nameEl.textContent =
                name.charAt(0).toUpperCase() + name.slice(1);
            if (metaEl) metaEl.innerHTML = act.notes
                ? "NOTES &nbsp; <strong>" + act.notes + "</strong>"
                : "COST &nbsp; <strong>₹" + (act.cost_inr ?? "—") + "</strong>";
            if (tagEl) tagEl.textContent = "QUEUED";
        } else if (act) {
            if (nameEl) nameEl.textContent = String(act);
            if (tagEl)  tagEl.textContent = "QUEUED";
        } else {
            if (nameEl) nameEl.textContent = "No Action";
            if (metaEl) metaEl.innerHTML = "STATUS &nbsp; <strong>Not required by plan</strong>";
            if (tagEl)  tagEl.textContent = "—";
        }
    }

    // ── Reasoning Bar ─────────────────────────────────────────
    if (llmReason) {
        const rb = document.querySelector(".reasoning-bar span:last-child");
        if (rb) setHTML(rb,
            "<strong>Agent 3 Reasoning:</strong> " + llmReason);
    }

    // ── System Status Badge (matches risk level + icon) ───────
    const statusVal  = document.querySelector(".status-value");
    const statusIcon = document.querySelector(".status-icon-wrap");
    const level = riskScore > 8 ? "CRITICAL" : riskScore > 4 ? "CAUTION" : "OPTIMAL";
    if (statusVal)  statusVal.textContent = level;
    if (statusIcon) statusIcon.textContent =
        level === "CRITICAL" ? "🚨" : level === "CAUTION" ? "⚠️" : "✅";

    // ── Show total pipeline time in meta if available ─────────
    const totalTime = po.total_time;
    if (totalTime) {
        const metaEl = document.querySelector(".report-meta");
        if (metaEl) {
            // Append pipeline time without breaking the "last updated" span
            const existing = metaEl.innerHTML;
            if (!existing.includes("Pipeline")) {
                metaEl.innerHTML += ` &bull; Pipeline: ${totalTime}s`;
            }
        }
    }
}

// ── Apply / Dismiss buttons ───────────────────────────────────
const API_URL    = "http://127.0.0.1:8001";
const applyBtn   = document.getElementById("applyBtn");
const dismissBtn = document.getElementById("dismissBtn");
if (applyBtn) {
    applyBtn.addEventListener("click", async () => {
        // Save the recommendation into Agent 5's memory on the backend,
        // so the next pipeline run learns from it.
        const insight = (document.getElementById("futureRecDesc")?.textContent || "") +
                        " | " + (document.getElementById("futureDesc")?.textContent || "");
        applyBtn.textContent = "Saving…";
        applyBtn.disabled = true;
        try {
            const r = await fetch(API_URL + "/apply-strategy", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ strategy: insight.trim().slice(0, 1000) })
            });
            const res = await r.json();
            if (res.status === "saved") {
                applyBtn.textContent = "✔ Applied to Agent 5 memory";
                applyBtn.style.background = "#1b5e20";
            } else {
                applyBtn.textContent = "⚠ Failed — try again";
                applyBtn.disabled = false;
            }
        } catch {
            applyBtn.textContent = "⚠ Backend unreachable";
            applyBtn.disabled = false;
        }
    });
}
if (dismissBtn) {
    dismissBtn.addEventListener("click", () => {
        dismissBtn.closest(".future-card")?.remove();
    });
}

// ── Boot ─────────────────────────────────────────────────────
// (risk bars keep their "run the pipeline" placeholder until data loads)
drawPerfChart();

// Load and apply stored pipeline result
try {
    const raw    = localStorage.getItem("pipeline_result");
    const rawIn  = localStorage.getItem("pipeline_inputs");
    if (raw) {
        const data   = JSON.parse(raw);
        const inputs = rawIn ? JSON.parse(rawIn) : null;
        updateDashboard(data, inputs);
    }
} catch (e) {
    console.error("dashboard2: localStorage parse error", e);
}
