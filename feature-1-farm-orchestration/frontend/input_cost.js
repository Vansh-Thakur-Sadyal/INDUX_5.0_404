/* ==========================================================
   INPUT COST TRACKER — JavaScript
   Handles: expense ledger (localStorage), summaries, ROI, CSV
========================================================== */

const STORAGE_KEY = "agritech_input_costs";

let expenses = loadExpenses();

window.onload = function () {
  // Set default date to today
  document.getElementById("expDate").valueAsDate = new Date();

  // Auto-calc total when qty or unit price changes
  const qtyEl = document.getElementById("expQty");
  const priceEl = document.getElementById("expUnitPrice");
  const totalEl = document.getElementById("expTotal");
  const previewEl = document.getElementById("calcPreview");

  function updateAutoTotal() {
    const qty = parseFloat(qtyEl.value) || 0;
    const price = parseFloat(priceEl.value) || 0;
    const auto = qty * price;
    if (totalEl.value === "0" || totalEl.dataset.autofill !== "manual") {
      totalEl.value = auto.toFixed(2);
      totalEl.dataset.autofill = "auto";
    }
    previewEl.textContent = `Total: ₹${parseFloat(totalEl.value || 0).toFixed(2)}`;
  }

  totalEl.addEventListener("input", () => {
    totalEl.dataset.autofill = "manual";
    previewEl.textContent = `Total: ₹${parseFloat(totalEl.value || 0).toFixed(2)}`;
  });

  qtyEl.addEventListener("input", updateAutoTotal);
  priceEl.addEventListener("input", updateAutoTotal);

  // Add expense button
  document.getElementById("addExpenseBtn").addEventListener("click", addExpense);

  // Filter listeners
  document.getElementById("filterCategory").addEventListener("change", renderLedger);
  document.getElementById("filterStage").addEventListener("change", renderLedger);

  // ROI calc
  document.getElementById("calcRoiBtn").addEventListener("click", calcROI);

  // ROI acres input → update per-acre display
  document.getElementById("roiAcres").addEventListener("input", updateSummaryCards);

  // Export CSV
  document.getElementById("exportCsvBtn").addEventListener("click", exportCSV);

  // Clear All
  document.getElementById("clearAllBtn").addEventListener("click", () => {
    if (confirm("Clear ALL expense entries? This cannot be undone.")) {
      expenses = [];
      saveExpenses();
      renderAll();
    }
  });

  renderAll();
};

// ─── LOAD / SAVE ──────────────────────────────────────────
function loadExpenses() {
  try {
    return JSON.parse(localStorage.getItem(STORAGE_KEY)) || [];
  } catch { return []; }
}

function saveExpenses() {
  localStorage.setItem(STORAGE_KEY, JSON.stringify(expenses));
}

// ─── ADD EXPENSE ──────────────────────────────────────────
function addExpense() {
  const category = document.getElementById("expCategory").value;
  const item = document.getElementById("expItem").value.trim();
  const qty = parseFloat(document.getElementById("expQty").value) || 1;
  const unit = document.getElementById("expUnit").value;
  const unitPrice = parseFloat(document.getElementById("expUnitPrice").value) || 0;
  const totalCost = parseFloat(document.getElementById("expTotal").value) || (qty * unitPrice);
  const date = document.getElementById("expDate").value;
  const stage = document.getElementById("expStage").value;

  if (!item) {
    alert("Please enter an item name / description.");
    document.getElementById("expItem").focus();
    return;
  }

  const entry = {
    id: Date.now(),
    category, item, qty, unit, unitPrice: unitPrice.toFixed(2),
    totalCost: totalCost.toFixed(2),
    date: date || new Date().toISOString().split("T")[0],
    stage
  };

  expenses.push(entry);
  saveExpenses();

  // Reset form
  document.getElementById("expItem").value = "";
  document.getElementById("expQty").value = "1";
  document.getElementById("expUnitPrice").value = "0";
  document.getElementById("expTotal").value = "0";
  document.getElementById("expTotal").dataset.autofill = "auto";
  document.getElementById("calcPreview").textContent = "Total: ₹0.00";
  document.getElementById("expDate").valueAsDate = new Date();

  renderAll();
}

// ─── DELETE EXPENSE ───────────────────────────────────────
function deleteExpense(id) {
  expenses = expenses.filter(e => e.id !== id);
  saveExpenses();
  renderAll();
}

// ─── RENDER ALL ───────────────────────────────────────────
function renderAll() {
  renderLedger();
  renderBreakdown();
  updateSummaryCards();
}

// ─── RENDER LEDGER TABLE ──────────────────────────────────
function renderLedger() {
  const filterCat = document.getElementById("filterCategory").value;
  const filterStage = document.getElementById("filterStage").value;

  const filtered = expenses.filter(e => {
    const catOk = filterCat === "all" || e.category === filterCat;
    const stageOk = filterStage === "all" || e.stage === filterStage;
    return catOk && stageOk;
  });

  const tbody = document.getElementById("ledgerBody");
  if (filtered.length === 0) {
    tbody.innerHTML = `<tr><td colspan="8" class="empty-ledger-msg">No expenses logged yet. Add your first entry above!</td></tr>`;
    document.getElementById("entryCount").textContent = "0 entries";
    document.getElementById("ledgerTotal").textContent = "Total: ₹0.00";
    return;
  }

  let html = "";
  let filteredTotal = 0;

  // Sort by date descending
  const sorted = [...filtered].sort((a, b) => new Date(b.date) - new Date(a.date));

  sorted.forEach(e => {
    const cost = parseFloat(e.totalCost);
    filteredTotal += cost;
    html += `
      <tr>
        <td>${formatDate(e.date)}</td>
        <td style="font-size:10px;color:var(--text-muted)">${e.stage.split(" / ")[0]}</td>
        <td><span class="cat-badge cat-${e.category}">${e.category}</span></td>
        <td>${e.item}</td>
        <td style="font-family:var(--font-mono)">${e.qty} ${e.unit}</td>
        <td style="font-family:var(--font-mono)">₹${parseFloat(e.unitPrice).toFixed(2)}</td>
        <td style="font-family:var(--font-mono);font-weight:700;color:var(--green-dark)">₹${cost.toFixed(2)}</td>
        <td><button class="btn-del-entry" onclick="deleteExpense(${e.id})" title="Delete">✕</button></td>
      </tr>
    `;
  });

  tbody.innerHTML = html;
  document.getElementById("entryCount").textContent = `${filtered.length} ${filtered.length === 1 ? "entry" : "entries"}`;
  document.getElementById("ledgerTotal").textContent = `Total: ₹${filteredTotal.toFixed(2)}`;
}

// ─── RENDER CATEGORY BREAKDOWN ────────────────────────────
function renderBreakdown() {
  const container = document.getElementById("breakdownList");
  if (expenses.length === 0) {
    container.innerHTML = `<div class="empty-text">Add expenses to see category breakdown.</div>`;
    return;
  }

  const totals = {};
  let grand = 0;
  expenses.forEach(e => {
    const c = parseFloat(e.totalCost);
    totals[e.category] = (totals[e.category] || 0) + c;
    grand += c;
  });

  const sorted = Object.entries(totals).sort((a, b) => b[1] - a[1]);
  const colors = {
    Seed: "#ca8a04", Fertilizer: "#059669", Manure: "#16a34a",
    Pesticide: "#dc2626", Irrigation: "#2563eb", Labor: "#7c3aed",
    Machinery: "#d97706", Land: "#db2777", Transport: "#0284c7", Other: "#64748b"
  };

  let html = "";
  sorted.forEach(([cat, amount]) => {
    const pct = grand > 0 ? ((amount / grand) * 100).toFixed(1) : 0;
    const color = colors[cat] || "#64748b";
    html += `
      <div class="breakdown-row">
        <div class="breakdown-label">${cat} (${pct}%)</div>
        <div class="breakdown-bar-wrap">
          <div class="breakdown-bar" style="width:${pct}%;background:${color}"></div>
        </div>
        <div class="breakdown-amount">₹${amount.toFixed(0)}</div>
      </div>
    `;
  });

  container.innerHTML = html;
}

// ─── UPDATE SUMMARY CARDS ─────────────────────────────────
function updateSummaryCards() {
  const total = expenses.reduce((s, e) => s + parseFloat(e.totalCost), 0);
  const acres = parseFloat(document.getElementById("roiAcres").value) || 1;
  const perAcre = acres > 0 ? total / acres : total;

  document.getElementById("sumTotal").textContent = `₹${total.toFixed(2)}`;
  document.getElementById("sumPerAcre").textContent = `₹${perAcre.toFixed(2)}`;
  document.getElementById("sumAcresText").textContent = `Based on ${acres} acre${acres !== 1 ? "s" : ""}`;
  document.getElementById("totalSpentBadge").textContent = `₹${total.toFixed(0)}`;

  // Revenue & Profit are updated by calcROI separately
}

// ─── ROI CALCULATOR ───────────────────────────────────────
function calcROI() {
  const total = expenses.reduce((s, e) => s + parseFloat(e.totalCost), 0);
  const acres = parseFloat(document.getElementById("roiAcres").value) || 1;
  const yieldKg = parseFloat(document.getElementById("roiYield").value) || 0;
  const rate = parseFloat(document.getElementById("roiRate").value) || 0;
  const subsidy = parseFloat(document.getElementById("roiSubsidy").value) || 0;

  const revenue = yieldKg * rate;
  const netProfit = revenue + subsidy - total;
  const roi = total > 0 ? ((netProfit / total) * 100) : 0;
  const perAcreRevenue = acres > 0 ? revenue / acres : revenue;
  const perAcreProfit = acres > 0 ? netProfit / acres : netProfit;

  document.getElementById("sumRevenue").textContent = `₹${revenue.toFixed(2)}`;

  const profitEl = document.getElementById("sumProfit");
  profitEl.textContent = `₹${netProfit.toFixed(2)}`;
  profitEl.className = `ics-value ${netProfit >= 0 ? "" : "negative"}`;
  document.getElementById("roiPctText").textContent = `ROI: ${roi.toFixed(1)}%`;

  const isProfit = netProfit >= 0;
  const roiBox = document.getElementById("roiResultBox");
  roiBox.innerHTML = `
    <div class="roi-result-grid">
      <div class="roi-metric">
        <div class="roi-metric-label">GROSS REVENUE</div>
        <div class="roi-metric-val">₹${revenue.toFixed(0)}</div>
      </div>
      <div class="roi-metric">
        <div class="roi-metric-label">TOTAL INPUT COST</div>
        <div class="roi-metric-val">₹${total.toFixed(0)}</div>
      </div>
      <div class="roi-metric">
        <div class="roi-metric-label">GOVT SUBSIDY</div>
        <div class="roi-metric-val">₹${subsidy.toFixed(0)}</div>
      </div>
      <div class="roi-metric">
        <div class="roi-metric-label">NET ${isProfit ? "PROFIT" : "LOSS"}</div>
        <div class="roi-metric-val ${isProfit ? "green" : "red"}">₹${Math.abs(netProfit).toFixed(0)}</div>
      </div>
      <div class="roi-metric">
        <div class="roi-metric-label">ROI</div>
        <div class="roi-metric-val ${isProfit ? "green" : "red"}">${roi.toFixed(1)}%</div>
      </div>
      <div class="roi-metric">
        <div class="roi-metric-label">PROFIT / ACRE</div>
        <div class="roi-metric-val ${isProfit ? "green" : "red"}">₹${Math.abs(perAcreProfit).toFixed(0)}</div>
      </div>
    </div>
    <div style="margin-top:12px;padding:10px 14px;background:${isProfit ? "#f0fdf4" : "#fff1f2"};border:1px solid ${isProfit ? "#bbf7d0" : "#fecdd3"};border-radius:8px;font-size:12px;color:${isProfit ? "#14532d" : "#9f1239"};">
      ${isProfit
        ? `✅ Your farm is <strong>profitable</strong>. You earn ₹${perAcreRevenue.toFixed(0)} per acre. Input costs represent ${((total / revenue) * 100).toFixed(1)}% of revenue.`
        : `⚠️ Your farm shows a <strong>loss of ₹${Math.abs(netProfit).toFixed(0)}</strong>. Consider reducing input costs or targeting a higher Mandi selling rate.`
      }
    </div>
  `;
}

// ─── CSV EXPORT ───────────────────────────────────────────
function exportCSV() {
  if (expenses.length === 0) {
    alert("No expenses to export.");
    return;
  }

  const header = ["Date", "Stage", "Category", "Item", "Quantity", "Unit", "Unit Price (INR)", "Total Cost (INR)"];
  const rows = expenses.map(e => [
    e.date, e.stage, e.category, `"${e.item}"`, e.qty, e.unit, e.unitPrice, e.totalCost
  ]);

  const total = expenses.reduce((s, e) => s + parseFloat(e.totalCost), 0);
  rows.push(["", "", "", "TOTAL", "", "", "", total.toFixed(2)]);

  const csv = [header, ...rows].map(r => r.join(",")).join("\n");
  const blob = new Blob([csv], { type: "text/csv" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `farm_input_costs_${new Date().toISOString().split("T")[0]}.csv`;
  a.click();
  URL.revokeObjectURL(url);
}

// ─── UTILITY ─────────────────────────────────────────────
function formatDate(dateStr) {
  if (!dateStr) return "—";
  const [y, m, d] = dateStr.split("-");
  return `${d}/${m}/${y}`;
}
