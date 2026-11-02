/* ==========================================================
   NPK SMART ADVISOR — JavaScript
   Handles: Bluetooth sensor, gauge updates, AI recommendations
========================================================== */

const API_URL = "http://127.0.0.1:8001";
let npkMode = "manual";

// ─── CROP KNOWLEDGE BASE ─────────────────────────────────
// NPK optimal ranges per crop (N, P, K in mg/kg)
const CROP_NPK = {
  "Tomato":             { N: [80, 120], P: [50, 80],  K: [100, 150], pH: [6.0, 7.0], notes: "Heavy feeder. Sensitive to Calcium deficiency." },
  "Wheat":              { N: [60, 100], P: [40, 70],  K: [80, 120],  pH: [6.0, 7.5], notes: "Needs split N application." },
  "Rice (Paddy)":       { N: [60, 100], P: [30, 60],  K: [60, 100],  pH: [5.5, 6.5], notes: "Flooded fields reduce P mobility. Green manure helps." },
  "Cotton":             { N: [70, 110], P: [40, 70],  K: [90, 130],  pH: [6.0, 7.5], notes: "K-deficiency causes premature boll drop." },
  "Maize (Corn)":       { N: [90, 130], P: [50, 80],  K: [90, 130],  pH: [5.8, 7.0], notes: "High N demand. Zinc deficiency common." },
  "Sugarcane":          { N: [80, 130], P: [40, 70],  K: [120, 180], pH: [6.0, 7.5], notes: "Very high K demand for juice quality." },
  "Potato":             { N: [70, 100], P: [50, 80],  K: [120, 160], pH: [5.5, 6.5], notes: "K critical for tuber size & starch content." },
  "Onion":              { N: [60, 90],  P: [40, 70],  K: [80, 120],  pH: [6.0, 7.0], notes: "Sulfur deficiency turns bulbs soft." },
  "Green Chilli":       { N: [70, 100], P: [40, 70],  K: [80, 120],  pH: [6.0, 7.0], notes: "Boron needed for fruit set." },
  "Soybean":            { N: [20, 40],  P: [40, 60],  K: [60, 100],  pH: [6.0, 7.0], notes: "N-fixing legume. Low N requirement. High P & K needed." },
  "Groundnut":          { N: [20, 40],  P: [40, 70],  K: [70, 110],  pH: [5.5, 7.0], notes: "Calcium critical for pod fill. N-fixing crop." },
  "Chickpea (Chana)":   { N: [15, 35],  P: [40, 60],  K: [50, 90],   pH: [6.0, 8.0], notes: "Deep-rooted. Drought tolerant. N-fixer." },
  "Mustard":            { N: [60, 100], P: [30, 60],  K: [50, 90],   pH: [6.0, 7.5], notes: "Sulfur is critical — apply gypsum." },
  "Brinjal (Eggplant)": { N: [70, 100], P: [40, 70],  K: [80, 120],  pH: [5.5, 6.8], notes: "Sensitive to excess N (poor fruit set)." },
  "Capsicum":           { N: [70, 100], P: [40, 70],  K: [90, 130],  pH: [6.0, 7.0], notes: "Calcium & Boron critical for fruit walls." },
};

const CATEGORY_COLORS = {
  N: "#ef4444", P: "#f59e0b", K: "#16a34a", ph: "#3b82f6", ec: "#14b8a6", moisture: "#06b6d4", temp: "#8b5cf6"
};

window.onload = function () {

  // ─── DATA INPUT MODE SELECTION ─────────────────────────
  const optManual = document.getElementById("optManual");
  const optPrevious = document.getElementById("optPrevious");
  const optSensor = document.getElementById("optSensor");

  function setActiveOption(selectedId) {
    [optManual, optPrevious, optSensor].forEach(opt => opt.classList.remove("active"));
    const selected = document.getElementById(selectedId);
    if (selected) selected.classList.add("active");
  }

  function hideBtModal(disconnectSensor = true) {
    const overlay = document.getElementById("btModalOverlay");
    if (overlay) overlay.classList.add("hidden");
    if (disconnectSensor) window.HM10SoilSensor.disconnect();
  }

  if (optManual) {
    optManual.addEventListener("click", () => {
      npkMode = "manual";
      setActiveOption("optManual");
      const badge = document.getElementById("npkInputModeBadge");
      if (badge) badge.textContent = "📝 Manual Entry";
      hideBtModal();
    });
  }

  if (optPrevious) {
    optPrevious.addEventListener("click", () => {
      npkMode = "previous";
      setActiveOption("optPrevious");
      const badge = document.getElementById("npkInputModeBadge");
      if (badge) badge.textContent = "📂 Previous Data";
      hideBtModal();
      loadPreviousData();
    });
  }

  if (optSensor) {
    optSensor.addEventListener("click", () => {
      npkMode = "sensor";
      setActiveOption("optSensor");
      const badge = document.getElementById("npkInputModeBadge");
      if (badge) badge.textContent = "📶 Sensor Reading";
      startSensorFlow();
    });
  }

  // ─── GAUGE LIVE UPDATE ─────────────────────────────────
  const gaugeMap = {
    inpN: { id: "gN", max: 300 },
    inpP: { id: "gP", max: 300 },
    inpK: { id: "gK", max: 300 },
    inpPh: { id: "gPh", max: 14 },
    inpEc: { id: "gEc", max: 10 },
    inpMoisture: { id: "gMoisture", max: 100 },
    inpTemp: { id: "gTemp", max: 60 },
  };

  Object.entries(gaugeMap).forEach(([inputId, cfg]) => {
    const el = document.getElementById(inputId);
    const bar = document.getElementById(cfg.id);
    if (el && bar) {
      el.addEventListener("input", () => {
        const pct = Math.min(100, Math.max(0, (parseFloat(el.value) / cfg.max) * 100));
        bar.style.width = pct + "%";
      });
    }
  });

  // ─── HM-10 BLE & HARDWARE DATA INTEGRATION ─────────────
  const npkConnectSerialBtn = document.getElementById("npkConnectSerialBtn");
  const npkSyncCsvBtn = document.getElementById("npkSyncCsvBtn");
  const npkToggleTerminalBtn = document.getElementById("npkToggleTerminalBtn");
  const npkTerminalDrawer = document.getElementById("npkTerminalDrawer");
  const npkParseApplyBtn = document.getElementById("npkParseApplyBtn");
  const npkRawInput = document.getElementById("npkRawInput");
  const npkHtdStatus = document.getElementById("npkHtdStatus");

  if (npkToggleTerminalBtn && npkTerminalDrawer) {
    npkToggleTerminalBtn.addEventListener("click", () => {
      npkTerminalDrawer.classList.toggle("hidden");
    });
  }

  if (npkParseApplyBtn && npkRawInput) {
    npkParseApplyBtn.addEventListener("click", () => {
      const raw = npkRawInput.value.trim();
      if (!raw) {
        alert("Paste a complete Arduino telemetry line first.");
        return;
      }
      const parsed = parseHC05SoilData(raw);
      if (parsed && Object.keys(parsed).length > 0) {
        applyParsedDataToAdvisor(parsed);
        if (npkHtdStatus) npkHtdStatus.textContent = "Complete sensor measurement parsed.";
        const dot = document.getElementById("npkBtDot");
        const text = document.getElementById("npkBtText");
        if (dot) dot.className = "bt-status-dot connected";
        if (text) text.textContent = "Arduino telemetry synchronized";
      } else {
        if (npkHtdStatus) npkHtdStatus.textContent = "⚠️ Could not parse sensor values. Check format.";
      }
    });
  }

  // Sync the latest real hardware reading
  if (npkSyncCsvBtn) {
    npkSyncCsvBtn.addEventListener("click", async () => {
      const dot = document.getElementById("npkBtDot");
      const text = document.getElementById("npkBtText");
      if (dot) dot.className = "bt-status-dot scanning";
      if (text) text.textContent = "Loading latest real hardware reading...";

      try {
        const res = await fetch(`${API_URL}/api/hardware-data`);
        if (!res.ok) throw new Error(`Backend returned HTTP ${res.status}`);
        const data = await res.json();
        if (data.status === "success" && data.telemetry) {
          applyParsedDataToAdvisor(data.telemetry);
          if (dot) dot.className = "bt-status-dot connected";
          if (text) text.textContent = `Latest HM-10 reading (${data.telemetry.timestamp})`;
          if (npkHtdStatus) npkHtdStatus.textContent = `Loaded real sensor reading (${data.telemetry.timestamp}).`;
        } else {
          if (dot) dot.className = "bt-status-dot disconnected";
          if (text) text.textContent = "No hardware reading received yet";
          alert("No real HM-10 sensor reading is available yet.");
        }
      } catch (err) {
        console.error("CSV sync error:", err);
        alert("Could not reach backend API. Ensure server is running on http://127.0.0.1:8001");
      }
    });
  }

  if (npkConnectSerialBtn) {
    npkConnectSerialBtn.addEventListener("click", async () => {
      if (!("serial" in navigator)) {
        alert("Web Serial is unavailable in this browser. Use the HM-10 BLE sensor mode or run the serial bridge.");
        if (npkTerminalDrawer) npkTerminalDrawer.classList.remove("hidden");
        return;
      }

      const dot = document.getElementById("npkBtDot");
      const text = document.getElementById("npkBtText");

      try {
        if (dot) dot.className = "bt-status-dot scanning";
        if (text) text.textContent = "Select the serial port connected to the Arduino...";

        const serialPort = await navigator.serial.requestPort();
        await serialPort.open({ baudRate: 9600 });

        if (dot) dot.className = "bt-status-dot connected";
        if (text) text.textContent = "Serial COM: Connected & Listening (9600 baud)...";
        if (npkHtdStatus) npkHtdStatus.textContent = "🟢 Live Serial Stream Connected. Awaiting measurement...";

        readAdvisorSerialStream(serialPort);
      } catch (err) {
        console.warn("Serial connect error:", err);
        if (dot) dot.className = "bt-status-dot disconnected";
        if (text) text.textContent = "Serial COM: Connection Canceled";
      }
    });
  }

  async function readAdvisorSerialStream(port) {
    let textDecoder = new TextDecoderStream();
    let readableStreamClosed = port.readable.pipeTo(textDecoder.writable);
    let reader = textDecoder.readable.getReader();
    let serialBuffer = "";

    try {
      while (true) {
        const { value, done } = await reader.read();
        if (done) break;
        if (value) {
          serialBuffer += value;
          const lines = serialBuffer.split(/\r?\n/);
          serialBuffer = lines.pop() || "";
          if (npkRawInput) {
            npkRawInput.value = (npkRawInput.value + lines.join("\n") + "\n").slice(-2000);
            npkRawInput.scrollTop = npkRawInput.scrollHeight;
          }
          for (const line of lines) {
            const parsed = parseHC05SoilData(line);
            if (parsed) {
              applyParsedDataToAdvisor(parsed);
              publishHardwareTelemetry(parsed);
              if (npkHtdStatus) npkHtdStatus.textContent = "Live Arduino serial reading received and applied.";
            }
          }
        }
      }
    } catch (error) {
      console.error("Advisor serial stream error:", error);
    } finally {
      reader.releaseLock();
    }
  }

  // ─── GET RECOMMENDATIONS BUTTONS ──────────────────────
  document.getElementById("getAdvisoryBtn").addEventListener("click", generateRecommendations);
  document.getElementById("runAdvisorBtn").addEventListener("click", generateRecommendations);

  // ─── OUTPUT TABS ───────────────────────────────────────
  document.querySelectorAll(".npk-tab-btn").forEach(btn => {
    btn.addEventListener("click", () => {
      document.querySelectorAll(".npk-tab-btn").forEach(b => b.classList.remove("active"));
      document.querySelectorAll(".npk-tab-content").forEach(c => c.classList.add("hidden"));
      btn.classList.add("active");
      document.getElementById("tab" + capitalize(btn.getAttribute("data-tab"))).classList.remove("hidden");
    });
  });

  // ─── SENSOR MODAL BUTTONS ──────────────────────────────
  const btnLoadSensorData = document.getElementById("btnLoadSensorData");
  const btModalCancelBtn = document.getElementById("btModalCancelBtn");

  if (btnLoadSensorData) {
    btnLoadSensorData.addEventListener("click", () => {
      if (window.pendingSensorData) {
        clearAllInputs();
        const d = window.pendingSensorData;
        fillInputs(d.N, d.P, d.K, d.ph, d.ec, d.moisture, d.temp);
        applyParsedDataToAdvisor({
          nitrogen: d.N,
          phosphorus: d.P,
          potassium: d.K,
          ph: d.ph,
          ec_us_cm: d.ec * 1000,
          ec: d.ec,
          soil_moisture: d.moisture,
          temperature: d.temp
        });
        hideBtModal(false);
      }
    });
  }

  if (btModalCancelBtn) {
    btModalCancelBtn.addEventListener("click", hideBtModal);
  }

};

function parseHC05SoilData(rawText) {
  if (!rawText) return null;
  const fields = {};
  const keyMap = {
    N: "nitrogen",
    P: "phosphorus",
    K: "potassium",
    TEMP_C: "temperature",
    MOISTURE_PCT: "soil_moisture",
    EC_US_CM: "ec_us_cm",
    PH: "ph"
  };
  for (const part of rawText.trim().split(",")) {
    const match = part.trim().match(/^([A-Z_]+)\s*=\s*(-?\d+(?:\.\d+)?)$/i);
    if (!match || !keyMap[match[1].toUpperCase()]) return null;
    const key = keyMap[match[1].toUpperCase()];
    const value = Number(match[2]);
    fields[key] = value === -1 && ["temperature", "soil_moisture", "ph"].includes(key) ? null : value;
  }

  const required = ["nitrogen", "phosphorus", "potassium", "temperature", "soil_moisture", "ec_us_cm", "ph"];
  if (required.some(key => !Object.prototype.hasOwnProperty.call(fields, key))) return null;
  if (Object.values(fields).some(value => value !== null && !Number.isFinite(value))) return null;
  if ((fields.soil_moisture !== null && (fields.soil_moisture < 0 || fields.soil_moisture > 100)) ||
      (fields.ph !== null && (fields.ph < 0 || fields.ph > 14)) ||
      (fields.temperature !== null && (fields.temperature < -20 || fields.temperature > 100)) ||
      ["nitrogen", "phosphorus", "potassium", "ec_us_cm"].some(key => fields[key] < 0)) return null;

  fields.ec_raw = fields.ec_us_cm;
  fields.ec = fields.ec_us_cm / 1000;
  return fields;
}

function applyParsedDataToAdvisor(parsed) {
  const n = parsed.nitrogen;
  const p = parsed.phosphorus;
  const k = parsed.potassium;
  const ph = parsed.ph;
  const ec = parsed.ec;
  const ecDisplay = `${parsed.ec_us_cm} µS/cm`;
  const moisture = parsed.soil_moisture;
  const temp = parsed.temperature;

  fillInputs(n, p, k, ph, ec, moisture, temp);

  const mEl = document.getElementById("npkBtMoisture");
  const tEl = document.getElementById("npkBtTemp");
  const nEl = document.getElementById("npkBtN");
  const pEl = document.getElementById("npkBtP");
  const kEl = document.getElementById("npkBtK");
  const phEl = document.getElementById("npkBtPh");
  const ecEl = document.getElementById("npkBtEc");

  if (mEl) mEl.textContent = moisture === null ? "Not measured" : `${moisture}%`;
  if (tEl) tEl.textContent = temp === null ? "Not measured" : `${temp}°C`;
  if (nEl) nEl.textContent = `${n} mg/kg`;
  if (pEl) pEl.textContent = `${p} mg/kg`;
  if (kEl) kEl.textContent = `${k} mg/kg`;
  if (phEl) phEl.textContent = ph === null ? "Not measured" : `${ph}`;
  if (ecEl) ecEl.textContent = ecDisplay;

  sessionStorage.setItem("lastFarmState", JSON.stringify({
    moisture, temp, N: n, P: p, K: k, ph, ec, ec_display: ecDisplay,
    timestamp: new Date().toISOString(),
    source: "HM-10 BLE Serial Sensor"
  }));
}

function publishHardwareTelemetry(parsed) {
  fetch(`${API_URL}/api/hardware-data`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      soil_moisture: parsed.soil_moisture,
      temperature: parsed.temperature,
      ec_us_cm: parsed.ec_us_cm,
      ph: parsed.ph,
      nitrogen: parsed.nitrogen,
      phosphorus: parsed.phosphorus,
      potassium: parsed.potassium,
      source: "HM-10 BLE Serial"
    })
  }).then(response => {
    if (!response.ok) throw new Error(`Backend returned HTTP ${response.status}`);
  }).catch(error => {
    console.error("Could not save sensor telemetry:", error);
    const status = document.getElementById("npkHtdStatus");
    if (status) status.textContent = `Sensor reading received, but backend sync failed: ${error.message}`;
  });
}

function fillInputs(n, p, k, ph, ec, moisture, temp) {
  setValue("inpN", n, "gN", 300);
  setValue("inpP", p, "gP", 300);
  setValue("inpK", k, "gK", 300);
  setValue("inpPh", ph, "gPh", 14);
  setValue("inpEc", ec, "gEc", 10);
  setValue("inpMoisture", moisture, "gMoisture", 100);
  setValue("inpTemp", temp, "gTemp", 60);
}

function setValue(inputId, val, gaugeId, max) {
  const el = document.getElementById(inputId);
  const bar = document.getElementById(gaugeId);
  if (val === null || val === undefined) {
    if (el) el.value = "";
    if (bar) bar.style.width = "0%";
    return;
  }
  if (el) el.value = val;
  if (bar) bar.style.width = Math.min(100, (val / max) * 100) + "%";
}

function loadFromLastAgentRun() {
  // Check if index.js stored any last run data in sessionStorage
  try {
    const last = JSON.parse(sessionStorage.getItem("lastFarmState") || "{}");
    if (last.N) {
      fillInputs(last.N, last.P, last.K, last.ph, last.ec, last.moisture, last.temp);
      alert("✅ Loaded sensor data from the last 6-Agent pipeline run.");
    } else {
      alert("No previous 6-Agent run data found in this session. Please run the pipeline on the Fleet Dashboard first.");
    }
  } catch { /* no prior data */ }
}

// ─── GENERATE RECOMMENDATIONS ─────────────────────────────
async function generateRecommendations() {
  const telemetryInputs = ["inpN", "inpP", "inpK", "inpPh", "inpEc", "inpMoisture", "inpTemp"];
  if (npkMode === "sensor" && telemetryInputs.some(id => !document.getElementById(id).value.trim())) {
    alert("Wait for the HM-10 reading, then enter any parameters the probe did not measure before requesting advice.");
    return;
  }
  const readValue = (id, fallback) => {
    const raw = document.getElementById(id).value.trim();
    const value = Number(raw);
    return raw && Number.isFinite(value) ? value : fallback;
  };
  const n = readValue("inpN", 42);
  const p = readValue("inpP", 28);
  const k = readValue("inpK", 55);
  const ph = readValue("inpPh", 6.8);
  const ec = readValue("inpEc", 1.4);
  const moisture = readValue("inpMoisture", 32);
  const temp = readValue("inpTemp", 29);
  const crop = document.getElementById("npkCrop").value;
  const stage = document.getElementById("npkStage").value;
  const acres = parseFloat(document.getElementById("npkAcres").value) || 2.5;
  const irrigation = document.getElementById("npkIrrigation").value;
  const soilType = document.getElementById("npkSoilType").value;
  const prevCrop = document.getElementById("npkPrevCrop").value;

  document.getElementById("advisorStatusBadge").textContent = "ANALYZING...";

  const payload = { n, p, k, ph, ec, moisture, temp, crop, stage, acres, irrigation, soil_type: soilType, prev_crop: prevCrop };

  let recs;
  try {
    const res = await fetch(`${API_URL}/api/npk-advisor`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    });
    const data = await res.json();
    if (data.status === "success") {
      recs = data.recommendations;
    } else {
      recs = buildLocalRecs(payload);
    }
  } catch {
    recs = buildLocalRecs(payload);
  }

  renderRecommendations(recs, payload);
  document.getElementById("npkOutputCard").classList.remove("hidden");
  document.getElementById("advisorStatusBadge").textContent = "COMPLETE";
  document.getElementById("npkOutputCard").scrollIntoView({ behavior: "smooth", block: "start" });
}

// ─── LOCAL RULES ENGINE ───────────────────────────────────
function buildLocalRecs(p) {
  const { n, ph, ec, k, crop, stage, acres } = p;
  const cropData = CROP_NPK[crop] || CROP_NPK["Tomato"];
  const pVal = p.p;

  const deficiencies = [];
  if (n < cropData.N[0]) deficiencies.push({ nutrient: "Nitrogen (N)", gap: cropData.N[0] - n, severity: n < cropData.N[0] * 0.6 ? "high" : "medium" });
  if (pVal < cropData.P[0]) deficiencies.push({ nutrient: "Phosphorus (P)", gap: cropData.P[0] - pVal, severity: pVal < cropData.P[0] * 0.6 ? "high" : "medium" });
  if (k < cropData.K[0]) deficiencies.push({ nutrient: "Potassium (K)", gap: cropData.K[0] - k, severity: k < cropData.K[0] * 0.6 ? "high" : "medium" });
  if (ph < cropData.pH[0]) deficiencies.push({ nutrient: "pH Too Low (Acidic)", gap: cropData.pH[0] - ph, severity: "medium" });
  if (ph > cropData.pH[1]) deficiencies.push({ nutrient: "pH Too High (Alkaline)", gap: ph - cropData.pH[1], severity: "medium" });
  if (ec > 3.0) deficiencies.push({ nutrient: "High Salinity (EC)", gap: ec - 3.0, severity: "high" });

  // Build fertilizer recs
  const fertRecs = [];
  if (n < cropData.N[0]) {
    const nGap = cropData.N[0] - n;
    const ureaKg = ((nGap * acres * 2.17) / 1000).toFixed(1);
    const dacKg = ((nGap * acres * 2.2) / 1000).toFixed(1);
    fertRecs.push({
      product: "Urea (46% N)", dose: `${ureaKg} kg/acre`, timing: `Apply in ${stage === "Pre-Sowing" ? "3 splits: basal + top-dress" : "top-dress immediately"}`,
      reason: `N deficit: ${nGap.toFixed(0)} mg/kg below optimal. Risk of chlorosis and stunted growth.`,
      cost: `~₹${(parseFloat(ureaKg) * 18).toFixed(0)}`, priority: "high"
    });
    fertRecs.push({
      product: "DAP (Di-ammonium Phosphate 18-46-0)", dose: `${dacKg} kg/acre as basal`,
      timing: "At sowing or transplanting",
      reason: "Provides both N and P — ideal as a basal dose for N+P correction.",
      cost: `~₹${(parseFloat(dacKg) * 55).toFixed(0)}`, priority: "medium"
    });
  }
  if (pVal < cropData.P[0]) {
    const pGap = cropData.P[0] - pVal;
    const sspKg = ((pGap * acres * 6.1) / 1000).toFixed(1);
    fertRecs.push({
      product: "SSP (Single Super Phosphate 16% P)", dose: `${sspKg} kg/acre as basal`,
      timing: "Mix into soil before transplanting",
      reason: `P deficit: ${pGap.toFixed(0)} mg/kg below optimal. Root development and flower initiation affected.`,
      cost: `~₹${(parseFloat(sspKg) * 12).toFixed(0)}`, priority: "high"
    });
  }
  if (k < cropData.K[0]) {
    const kGap = cropData.K[0] - k;
    const mopKg = ((kGap * acres * 1.7) / 1000).toFixed(1);
    fertRecs.push({
      product: "MOP (Muriate of Potash 60% K₂O)", dose: `${mopKg} kg/acre`,
      timing: "Split: 50% basal, 50% at flowering",
      reason: `K deficit: ${kGap.toFixed(0)} mg/kg below optimal. Reduces disease resistance and fruit quality.`,
      cost: `~₹${(parseFloat(mopKg) * 35).toFixed(0)}`, priority: n < cropData.N[0] || pVal < cropData.P[0] ? "medium" : "high"
    });
  }
  if (fertRecs.length === 0) {
    fertRecs.push({
      product: `NPK 19:19:19 (Balanced Maintenance Dose for ${crop})`,
      dose: `${(acres * 2.5).toFixed(1)} kg/acre`,
      timing: "Monthly fertigated maintenance", reason: "All major nutrients within optimal range. Apply balanced NPK for sustained crop vigor.", cost: `~₹${(acres * 2.5 * 80).toFixed(0)}`, priority: "low"
    });
  }

  // Manure Recs
  const manureRecs = [
    {
      product: "Well-Decomposed FYM (Farm Yard Manure)",
      dose: `${(acres * 5).toFixed(0)} cartloads (10 tonnes/acre)`,
      timing: "2–3 weeks before sowing",
      reason: "Builds organic matter, improves water retention, introduces beneficial microbes.",
      cost: `~₹${(acres * 5 * 300).toFixed(0)}`
    },
    {
      product: "Vermicompost",
      dose: `${(acres * 500).toFixed(0)} kg/acre`,
      timing: "At transplanting / as side-dress",
      reason: "Concentrated plant-available nutrients + enzymes. Ideal for vegetable crops.",
      cost: `~₹${(acres * 500 * 8).toFixed(0)}`
    },
    {
      product: "Jeevamrut (Bio-stimulant Solution)",
      dose: "200L/acre, diluted 1:10 with water",
      timing: "Every 15 days via drip or spray",
      reason: "Activates native soil microbiome. Improves nutrient uptake efficiency by 20–35%.",
      cost: "~₹120 per preparation"
    }
  ];

  if (prevCrop === "Legume") {
    manureRecs.unshift({
      product: "Rhizobium + PSB Biofertilizer", dose: "250g/acre (seed treatment)",
      timing: "Before sowing", reason: "Legume previous crop enriched residual N. Biofertilizer enhances N-fixation carry-forward.",
      cost: "~₹80"
    });
  }

  // Pesticide Recs
  const pestRecs = buildPesticideRecs(crop, stage, temp, p.moisture);

  // Technique Recs
  const techniqueRecs = buildTechniqueRecs(p);

  return { deficiencies, fertilizer: fertRecs, manure: manureRecs, pesticide: pestRecs, technique: techniqueRecs, crop_notes: cropData.notes };
}

function buildPesticideRecs(crop, stage, temp, moisture) {
  const recs = [];

  // Universal preventive
  recs.push({
    name: "Neem Oil (3000 ppm Azadirachtin)",
    category: "Organic Pesticide",
    icon: "🌿",
    details: "Mix 5ml/L + 2ml/L liquid soap. Spray on leaf undersides. Repels aphids, mites, and thrips. Repeat every 7 days.", timing: "Preventive — start at early vegetative stage"
  });

  if (["Tomato", "Capsicum", "Green Chilli", "Brinjal (Eggplant)"].includes(crop)) {
    recs.push({ name: "Imidacloprid 17.8% SL", category: "Systemic Insecticide", icon: "🛡️", details: "0.3ml/L for sucking pests (aphids, whiteflies, jassids). Do NOT apply during flowering to protect pollinators.", timing: "Vegetative to pre-flowering stage" });
    recs.push({ name: "Mancozeb 75% WP", category: "Broad-spectrum Fungicide", icon: "🍄", details: "2.5g/L for early blight, late blight, and leaf spot. Repeat every 10 days in humid conditions.", timing: "Monsoon & post-monsoon risk period" });
  }

  if (["Wheat", "Rice (Paddy)"].includes(crop)) {
    recs.push({ name: "Propiconazole 25% EC", category: "Fungicide", icon: "🍄", details: "1ml/L for rust (yellow/brown) and powdery mildew. Best applied at flag leaf stage.", timing: "Tillering to heading stage" });
    recs.push({ name: "Chlorpyrifos 20% EC", category: "Insecticide", icon: "🛡️", details: "2ml/L for stem borers and leaf folders in rice. Soil application for termites in wheat.", timing: "Vegetative stage on pest observation" });
  }

  if (temp > 32) {
    recs.push({ name: "Kaolin Clay Particle Film", category: "Heat Stress & Pest Barrier", icon: "🌡️", details: "25kg/acre foliar spray. Creates a heat-reflective particle film that deters mites and reduces heat stress on fruit.", timing: "When temperature exceeds 32°C" });
  }

  if (moisture > 70) {
    recs.push({ name: "Trichoderma harzianum (Bio-fungicide)", category: "Bio-control", icon: "🌱", details: "5g/L soil drench or 2.5g/L foliar. Prevents damping-off and root rot in high-moisture conditions.", timing: "Immediately in waterlogged / high-moisture conditions" });
  }

  return recs;
}

function buildTechniqueRecs(p) {
  const { irrigation, soilType, crop, stage, moisture, n } = p;
  const recs = [];

  if (irrigation === "Flood") {
    recs.push({ title: "Switch to Drip / Furrow Fertigation", impact: "💧 Save 35–40% water & reduce disease pressure", detail: "Flood irrigation leads to nutrient leaching and fungal root diseases. Drip irrigation targets root zone with precise water and dissolved fertilizers." });
  }

  recs.push({ title: "Split Nitrogen Application (3-Pass Method)", impact: "🌾 Increase N-use efficiency by 25–30%", detail: `Apply N in 3 splits: 30% basal at sowing, 40% at active vegetative stage, 30% at ${stage === "Flowering" || stage === "Fruiting" ? "fruit set" : "pre-flowering"}. Reduces N-loss via leaching.` });

  if (soilType === "Sandy") {
    recs.push({ title: "Organic Mulching (Wheat Straw / Dry Grass)", impact: "🌡️ Reduce moisture loss by 40% in sandy soil", detail: "Apply 5–8 cm layer of straw mulch. Reduces soil evaporation, maintains root-zone temperature, and gradually enriches organic matter." });
  }

  if (["Tomato", "Green Chilli", "Capsicum", "Brinjal (Eggplant)"].includes(crop)) {
    recs.push({ title: "Calcium + Boron Foliar Spray", impact: "🍅 Prevent blossom end rot & fruit cracking", detail: "Spray 0.5% CaNO3 + 0.1% Borax at early flowering. Calcium deficiency in tomato/capsicum causes blossom end rot and hollow fruit." });
  }

  recs.push({ title: "Soil pH Correction (Lime / Gypsum)", impact: "🌱 Unlock unavailable nutrients locked by pH extremes", detail: p.ph < 6.0 ? `pH ${p.ph} is acidic — apply agricultural lime (CaCO3) at 400kg/acre to raise pH to optimal range (6.0–7.0).` : p.ph > 7.5 ? `pH ${p.ph} is alkaline — apply gypsum (CaSO4) at 200–300kg/acre and incorporate sulfur to lower pH.` : `pH ${p.ph} is in optimal range — maintain with organic matter additions.` });

  recs.push({ title: "Integrated Pest Management (IPM) Calendar", impact: "🛡️ Reduce pesticide cost by 50%", detail: "Deploy yellow sticky traps for whitefly/thrip monitoring. Scout weekly. Spray only when pest population crosses economic threshold — avoid calendar-based blanket sprays." });

  return recs;
}

// ─── RENDER RECOMMENDATIONS ───────────────────────────────
function renderRecommendations(recs, p) {
  renderFertilizer(recs, p);
  renderManure(recs, p);
  renderPesticide(recs, p);
  renderTechnique(recs, p);
}

function renderFertilizer(recs, p) {
  const crop = p.crop;
  const deficiencies = recs.deficiencies || [];
  const fertilizers = recs.fertilizer || [];
  const cropNotes = recs.crop_notes || "";

  let html = "";

  if (deficiencies.length > 0) {
    html += `<div class="deficiency-banner"><strong>⚠️ Soil needs more:</strong> `;
    deficiencies.forEach(d => { html += `${d.nutrient} `; });
    html += `</div>`;
  } else {
    html += `<div class="deficiency-banner" style="background:#f0fdf4;border-color:#bbf7d0;color:#14532d;"><strong>✅ Soil is good! Use maintenance dose.</strong></div>`;
  }

  html += `<div class="rec-section-title">🧪 What to add (${p.acres} acres)</div>`;
  html += `<div class="rec-grid">`;
  fertilizers.forEach(f => {
    html += `
      <div class="rec-card priority-${f.priority}">
        <div class="rec-product">${f.product}</div>
        <div class="rec-dose">📦 ${f.dose}</div>
        <div class="rec-reason">${f.reason}</div>
        <span class="rec-timing">⏰ ${f.timing}</span>
        ${f.cost ? `<span class="rec-cost">💰 ${f.cost}</span>` : ""}
      </div>
    `;
  });
  html += `</div>`;
  document.getElementById("fertilizerContent").outerHTML = `<div id="fertilizerContent">${html}</div>`;
}

function renderManure(recs, p) {
  const manures = recs.manure || [];
  let html = `<div class="rec-section-title">🌿 Natural fertilizers</div><div class="rec-grid">`;
  manures.forEach(m => {
    html += `
      <div class="rec-card priority-low">
        <div class="rec-product">${m.product}</div>
        <div class="rec-dose">📦 ${m.dose}</div>
        <div class="rec-reason">${m.reason}</div>
        <span class="rec-timing">⏰ ${m.timing}</span>
        ${m.cost ? `<span class="rec-cost">💰 ${m.cost}</span>` : ""}
      </div>
    `;
  });
  html += `</div>`;
  document.getElementById("manureContent").outerHTML = `<div id="manureContent">${html}</div>`;
}

function renderPesticide(recs, p) {
  const pests = recs.pesticide || [];
  let html = `<div class="rec-section-title">🛡️ Protect your crop</div><ul class="pest-list">`;
  pests.forEach(pest => {
    html += `
      <li class="pest-item">
        <div class="pest-icon">${pest.icon}</div>
        <div>
          <div class="pest-name">${pest.name} <span style="font-size:10px;font-weight:400;color:var(--text-muted)">(${pest.category})</span></div>
          <div class="pest-info">${pest.details}</div>
          <div style="margin-top:4px;font-size:10px;color:#2563eb;font-weight:600">📅 ${pest.timing}</div>
        </div>
      </li>
    `;
  });
  html += `</ul>`;
  document.getElementById("pesticideContent").outerHTML = `<div id="pesticideContent">${html}</div>`;
}

function renderTechnique(recs, p) {
  const techniques = recs.technique || [];
  let html = `<div class="rec-section-title">🔬 Simple farming tips</div><div class="technique-cards">`;
  techniques.forEach(t => {
    html += `
      <div class="tech-card">
        <div class="tech-title">${t.title}</div>
        <div class="tech-impact">${t.impact}</div>
        <div class="tech-detail">${t.detail}</div>
      </div>
    `;
  });
  html += `</div>`;
  document.getElementById("techniqueContent").outerHTML = `<div id="techniqueContent">${html}</div>`;
}

function capitalize(s) { return s.charAt(0).toUpperCase() + s.slice(1); }

// ─── HM-10 BLE SENSOR READING ────────────────────────────
function startSensorFlow() {
  const overlay = document.getElementById("btModalOverlay");
  const title = document.getElementById("btModalTitle");
  const subtitle = document.getElementById("btModalSubtitle");
  const spinner = document.getElementById("btModalSpinner");
  const statusText = document.getElementById("btModalStatusText");
  const dot = document.getElementById("btModalDot");
  const liveCard = document.getElementById("btSensorLiveCard");
  const loadBtn = document.getElementById("btnLoadSensorData");

  overlay.classList.remove("hidden");
  clearAllInputs();
  title.textContent = "Connecting to HM-10 Sensor...";
  subtitle.textContent = "Choose your HM-10 device. Press the Arduino scan button after it connects.";
  spinner.classList.remove("hidden");
  liveCard.classList.add("hidden");
  loadBtn.classList.add("hidden");
  dot.className = "bt-modal-dot scanning";
  statusText.textContent = "Select the HM-10 BLE device...";
  window.pendingSensorData = null;

  window.HM10SoilSensor.connect(reading => {
    const parsed = {
      nitrogen: reading.n,
      phosphorus: reading.p,
      potassium: reading.k,
      ph: reading.ph,
      ec_us_cm: reading.ec,
      ec_raw: reading.ec,
      ec: reading.ec / 1000,
      soil_moisture: reading.moisture,
      temperature: reading.temp
    };
    window.pendingSensorData = {
      N: reading.n,
      P: reading.p,
      K: reading.k,
      ph: reading.ph,
      ec: reading.ec / 1000,
      ec_display: `${reading.ec} µS/cm`,
      moisture: reading.moisture,
      temp: reading.temp
    };
    publishHardwareTelemetry(parsed);
    const grid = document.getElementById("bslcGrid");
    grid.innerHTML = `
      <div class="bslc-item"><span class="bslc-label">NITROGEN (N)</span><span class="bslc-value">${reading.n} mg/kg</span></div>
      <div class="bslc-item"><span class="bslc-label">PHOSPHORUS (P)</span><span class="bslc-value">${reading.p} mg/kg</span></div>
      <div class="bslc-item"><span class="bslc-label">POTASSIUM (K)</span><span class="bslc-value">${reading.k} mg/kg</span></div>
      <div class="bslc-item"><span class="bslc-label">pH</span><span class="bslc-value">${reading.ph === null ? "Not measured" : reading.ph}</span></div>
      <div class="bslc-item"><span class="bslc-label">EC</span><span class="bslc-value">${reading.ec} µS/cm</span></div>
      <div class="bslc-item"><span class="bslc-label">SOIL MOISTURE</span><span class="bslc-value">${reading.moisture === null ? "Not measured" : `${reading.moisture}%`}</span></div>
      <div class="bslc-item"><span class="bslc-label">TEMPERATURE</span><span class="bslc-value">${reading.temp === null ? "Not measured" : `${reading.temp}°C`}</span></div>
    `;
    liveCard.classList.remove("hidden");
    loadBtn.classList.remove("hidden");
    title.textContent = "Real Sensor Reading Received";
    statusText.textContent = "Complete Arduino telemetry frame received";
    dot.className = "bt-modal-dot connected";
    const status = document.getElementById("npkHtdStatus");
    if (status) status.textContent = "Real HM-10 BLE reading received; select Use This Data to populate the form.";
  }, () => {
    spinner.classList.add("hidden");
    dot.className = "bt-modal-dot";
    statusText.textContent = "HM-10 disconnected";
  }).then(device => {
    spinner.classList.add("hidden");
    title.textContent = "HM-10 Connected";
    subtitle.textContent = `Connected to ${device.name || "HM-10"}. Press the Arduino scan button and wait for its 10-second reading.`;
    dot.className = "bt-modal-dot connected";
    statusText.textContent = "Waiting for telemetry from the sensor...";
  }).catch(error => {
    console.error("HM-10 BLE connection failed:", error);
    spinner.classList.add("hidden");
    title.textContent = "Sensor Connection Failed";
    subtitle.textContent = error.message || "Could not connect to the HM-10 BLE serial device.";
    statusText.textContent = error.name === "NotFoundError" ? "Device selection canceled" : "No BLE sensor connection";
    dot.className = "bt-modal-dot";
  });
}

function loadPreviousData() {
  try {
    const last = JSON.parse(sessionStorage.getItem("lastFarmState") || "{}");
    if (last.N !== undefined && last.source === "HM-10 BLE Serial Sensor") {
      clearAllInputs();
      fillInputs(last.N, last.P, last.K, last.ph, last.ec, last.moisture, last.temp);
      updateSensorDisplay(last);
      return;
    }
  } catch (error) {
    console.warn("Could not read the previous sensor record:", error);
  }

  fetch(`${API_URL}/api/hardware-data`)
    .then(res => res.json())
    .then(data => {
      if (data.status === "success" && data.telemetry) {
        const telemetry = data.telemetry;
        const parsed = {
          nitrogen: telemetry.nitrogen,
          phosphorus: telemetry.phosphorus,
          potassium: telemetry.potassium,
          ph: telemetry.ph,
          ec_us_cm: telemetry.ec_us_cm,
          ec_raw: telemetry.ec_raw,
          ec: telemetry.ec,
          soil_moisture: telemetry.soil_moisture,
          temperature: telemetry.temperature
        };
        applyParsedDataToAdvisor(parsed);
        clearAllInputs();
        fillInputs(telemetry.nitrogen, telemetry.phosphorus, telemetry.potassium, telemetry.ph, telemetry.ec, telemetry.soil_moisture, telemetry.temperature);
      } else {
        alert("No real HM-10 sensor reading is available yet.");
      }
    })
    .catch(error => {
      console.error("Could not load previous hardware reading:", error);
      alert("Could not reach backend API.");
    });
}

function updateSensorDisplay(data) {
  const mEl = document.getElementById("npkBtMoisture");
  const tEl = document.getElementById("npkBtTemp");
  const nEl = document.getElementById("npkBtN");
  const pEl = document.getElementById("npkBtP");
  const kEl = document.getElementById("npkBtK");
  const phEl = document.getElementById("npkBtPh");
  const ecEl = document.getElementById("npkBtEc");

  if (mEl) mEl.textContent = data.moisture === null ? "Not measured" : `${data.moisture}%`;
  if (tEl) tEl.textContent = data.temp === null ? "Not measured" : `${data.temp}°C`;
  if (nEl) nEl.textContent = `${data.N} mg/kg`;
  if (pEl) pEl.textContent = `${data.P} mg/kg`;
  if (kEl) kEl.textContent = `${data.K} mg/kg`;
  if (phEl) phEl.textContent = data.ph === null ? "Not measured" : data.ph;
  if (ecEl) ecEl.textContent = data.ec_display || `${data.ec} uS/cm`;
}

function clearAllInputs() {
  ["inpN", "inpP", "inpK", "inpPh", "inpEc", "inpMoisture", "inpTemp"].forEach(id => {
    const el = document.getElementById(id);
    if (el) el.value = "";
  });
  ["gN", "gP", "gK", "gPh", "gEc", "gMoisture", "gTemp"].forEach(id => {
    const el = document.getElementById(id);
    if (el) el.style.width = "0%";
  });
}
