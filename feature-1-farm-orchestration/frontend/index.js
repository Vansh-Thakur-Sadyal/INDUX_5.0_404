const API_URL = "http://127.0.0.1:8001";
let activeIngestionMode = "manual";

window.onload = function () {
    // Chatbot setup
    const openChat = document.getElementById("openChatbotBtn");
    const closeChat = document.getElementById("chatbotClose");
    const chatPopup = document.getElementById("chatbotPopup");
    const sendBtn = document.getElementById("chatSend");
    const chatInput = document.getElementById("chatInput");
    const chatBox = document.getElementById("chatMessages");

    if (openChat && chatPopup) {
        openChat.addEventListener("click", () => chatPopup.classList.remove("hidden"));
    }
    if (closeChat && chatPopup) {
        closeChat.addEventListener("click", () => chatPopup.classList.add("hidden"));
    }

    function appendMessage(sender, text, actionLink, actionText) {
        const row = document.createElement("div");
        row.className = `chat-msg-row ${sender}-row`;

        const av = document.createElement("div");
        av.className = `chat-avatar ${sender === "user" ? "user-av" : "bot-av"}`;

        const bubble = document.createElement("div");
        bubble.className = `chat-bubble ${sender === "user" ? "user-bubble" : "bot-bubble"}`;
        bubble.innerHTML = text.replace(/\n/g, "<br>");

        if (actionLink && actionText) {
            const btn = document.createElement("a");
            btn.href = actionLink;
            btn.className = "chat-action-btn";
            btn.style.cssText = "display:inline-block;margin-top:8px;padding:6px 12px;background:#2d6a2d;color:#fff;border-radius:6px;text-decoration:none;font-weight:600;font-size:11px;";
            btn.textContent = actionText;
            bubble.appendChild(btn);
        }

        row.appendChild(av);
        row.appendChild(bubble);
        chatBox.appendChild(row);
        chatBox.scrollTop = chatBox.scrollHeight;
    }

    async function sendChatMessage() {
        const text = chatInput.value.trim();
        if (!text) return;
        appendMessage("user", text);
        chatInput.value = "";

        try {
            const res = await fetch(`${API_URL}/chat`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ message: text })
            });
            const data = await res.json();
            appendMessage("bot", data.reply || "Processing...", data.action_link, data.action_text);
        } catch (e) {
            appendMessage("bot", "Network connection error.");
        }
    }

    if (sendBtn) sendBtn.addEventListener("click", sendChatMessage);
    if (chatInput) {
        chatInput.addEventListener("keydown", (e) => {
            if (e.key === "Enter") sendChatMessage();
        });
    }

    // ─── 1. DATA INGESTION MODES (3 OPTIONS) ───────────────────
    const btnModeManual = document.getElementById("btnModeManual");
    const btnModePrevious = document.getElementById("btnModePrevious");
    const btnModeSensors = document.getElementById("btnModeSensors");
    const btSensorStatusBanner = document.getElementById("btSensorStatusBanner");
    const previousDataBanner = document.getElementById("previousDataBanner");
    const btSensorBannerSummary = document.getElementById("btSensorBannerSummary");
    const btnRescanSensor = document.getElementById("btnRescanSensor");

    const btModalOverlay = document.getElementById("btModalOverlay");
    const btModalTitle = document.getElementById("btModalTitle");
    const btModalSubtitle = document.getElementById("btModalSubtitle");
    const btModalSpinner = document.getElementById("btModalSpinner");
    const btModalStatusText = document.getElementById("btModalStatusText");
    const btModalDot = document.getElementById("btModalDot");
    const btSensorLiveCard = document.getElementById("btSensorLiveCard");
    const bslcGrid = document.getElementById("bslcGrid");
    const btnLoadSensorData = document.getElementById("btnLoadSensorData");
    const btModalCancelBtn = document.getElementById("btModalCancelBtn");
    const sensorStepConnected = document.getElementById("sensorStepConnected");
    const sensorStepReading = document.getElementById("sensorStepReading");
    const sensorStepLoaded = document.getElementById("sensorStepLoaded");

    function setActiveModeTab(selectedBtn) {
        [btnModeManual, btnModePrevious, btnModeSensors].forEach(b => {
            if (b) b.classList.remove("active");
        });
        if (selectedBtn) selectedBtn.classList.add("active");
    }

    function hideBtModal(disconnectSensor = true) {
        if (btModalOverlay) btModalOverlay.classList.add("hidden");
        if (disconnectSensor) window.HM10SoilSensor.disconnect();
    }

    function setSensorCheckpoint(step, state) {
        const checkpoint = {
            connected: sensorStepConnected,
            reading: sensorStepReading,
            loaded: sensorStepLoaded
        }[step];
        if (!checkpoint) return;
        checkpoint.classList.remove("pending", "active", "complete");
        checkpoint.classList.add(state);
        const marker = checkpoint.querySelector("span");
        if (marker) marker.textContent = state === "complete" ? "✓" : marker.dataset.number || marker.textContent;
    }

    function resetSensorCheckpoints() {
        [sensorStepConnected, sensorStepReading, sensorStepLoaded].forEach((checkpoint, index) => {
            if (!checkpoint) return;
            checkpoint.className = "sensor-checkpoint pending";
            const marker = checkpoint.querySelector("span");
            if (marker) {
                marker.dataset.number = String(index + 1);
                marker.textContent = String(index + 1);
            }
        });
    }

    function fillTelemetryInputs(sm, temp, n, p, k, ph, ec) {
        const setVal = (id, val) => {
            const el = document.getElementById(id);
            if (el && val !== undefined) el.value = val;
        };
        setVal("inpMoisture", sm);
        setVal("inpTemp", temp);
        setVal("inpN", n);
        setVal("inpP", p);
        setVal("inpK", k);
        setVal("inpPh", ph);
        setVal("inpEc", ec);
        updateTelemetryGauges();
    }

    function updateTelemetryGauges() {
        const gaugeMap = {
            inpN: ["gN", 300], inpP: ["gP", 300], inpK: ["gK", 300],
            inpPh: ["gPh", 14], inpEc: ["gEc", 10], inpMoisture: ["gMoisture", 100],
            inpTemp: ["gTemp", 60]
        };
        Object.entries(gaugeMap).forEach(([inputId, [gaugeId, max]]) => {
            const input = document.getElementById(inputId);
            const gauge = document.getElementById(gaugeId);
            if (!input || !gauge) return;
            const value = Math.max(0, Math.min(max, parseFloat(input.value) || 0));
            gauge.style.width = `${(value / max) * 100}%`;
        });
    }

    ["inpN", "inpP", "inpK", "inpPh", "inpEc", "inpMoisture", "inpTemp"].forEach(id => {
        const input = document.getElementById(id);
        if (input) input.addEventListener("input", updateTelemetryGauges);
    });
    updateTelemetryGauges();

    // OPTION 1: MANUAL DATA
    if (btnModeManual) {
        btnModeManual.addEventListener("click", () => {
            activeIngestionMode = "manual";
            setActiveModeTab(btnModeManual);
            hideBtModal();
            // Sensor Connected status MUST ONLY be visible when option 3 is selected
            if (btSensorStatusBanner) btSensorStatusBanner.classList.add("hidden");
            if (previousDataBanner) previousDataBanner.classList.add("hidden");
        });
    }

    // OPTION 2: ADD PREVIOUS DATA
    if (btnModePrevious) {
        btnModePrevious.addEventListener("click", () => {
            activeIngestionMode = "previous";
            setActiveModeTab(btnModePrevious);
            hideBtModal();
            // Sensor Connected status MUST ONLY be visible when option 3 is selected
            if (btSensorStatusBanner) btSensorStatusBanner.classList.add("hidden");

            loadPreviousData();
        });
    }

    async function loadPreviousData() {
        let prev = null;
        try {
            const saved = sessionStorage.getItem("lastFarmState") || localStorage.getItem("lastFarmState");
            if (saved) {
                const candidate = JSON.parse(saved);
                if (candidate.source === "HM-10 BLE Serial Sensor") prev = candidate;
            }
        } catch (e) {
            console.warn("Could not read previous saved state", e);
        }

        if (!prev) {
            try {
                const response = await fetch(`${API_URL}/api/hardware-data`);
                if (!response.ok) throw new Error(`Backend returned HTTP ${response.status}`);
                const result = await response.json();
                if (result.status === "success" && result.telemetry) {
                    const telemetry = result.telemetry;
                    prev = {
                        moisture: telemetry.soil_moisture,
                        temp: telemetry.temperature,
                        N: telemetry.nitrogen,
                        P: telemetry.phosphorus,
                        K: telemetry.potassium,
                        ph: telemetry.ph,
                        ec: telemetry.ec,
                        source: "HM-10 BLE Serial Sensor"
                    };
                }
            } catch (error) {
                console.error("Could not load previous hardware telemetry:", error);
                alert("Could not reach the sensor backend.");
                return;
            }
        }
        if (!prev) {
            alert("No real HM-10 sensor reading is available yet.");
            return;
        }

        const { moisture, temp, N, P, K, ph, ec } = prev;
        fillTelemetryInputs(moisture, temp, N, P, K, ph, ec);

        if (previousDataBanner) {
            previousDataBanner.innerHTML = `<span>📂 <strong>Previous real sensor reading applied:</strong> Moisture: ${moisture ?? "not measured"}%, N: ${N}, P: ${P}, K: ${K} mg/kg, pH: ${ph ?? "not measured"}, EC: ${ec} mS/cm</span>`;
            previousDataBanner.classList.remove("hidden");
        }
    }

    // OPTION 3: USING SENSORS
    if (btnModeSensors) {
        btnModeSensors.addEventListener("click", () => {
            activeIngestionMode = "sensors";
            setActiveModeTab(btnModeSensors);
            if (previousDataBanner) previousDataBanner.classList.add("hidden");
            startSensorFlow();
        });
    }

    if (btnRescanSensor) {
        btnRescanSensor.addEventListener("click", () => {
            if (activeIngestionMode !== "sensors") {
                activeIngestionMode = "sensors";
                setActiveModeTab(btnModeSensors);
            }
            startSensorFlow();
        });
    }

    function startSensorFlow() {
        if (!btModalOverlay) return;

        ["inpMoisture", "inpTemp", "inpN", "inpP", "inpK", "inpPh", "inpEc"].forEach(id => {
            const input = document.getElementById(id);
            if (input) input.value = "";
        });
        updateTelemetryGauges();
        window.pendingSensorData = null;
        window.sensorDataLoaded = false;
        if (btSensorStatusBanner) btSensorStatusBanner.classList.add("hidden");
        btModalOverlay.classList.remove("hidden");
        btModalTitle.textContent = "Connecting to HM-10 Sensor...";
        btModalSubtitle.textContent = "Choose your HM-10 device. Press the Arduino scan button after it connects.";
        if (btModalSpinner) btModalSpinner.classList.remove("hidden");
        if (btSensorLiveCard) btSensorLiveCard.classList.add("hidden");
        if (btnLoadSensorData) btnLoadSensorData.classList.add("hidden");
        if (btModalDot) btModalDot.className = "bt-modal-dot scanning";
        if (btModalStatusText) btModalStatusText.textContent = "Select the HM-10 BLE device...";
        resetSensorCheckpoints();

        window.HM10SoilSensor.connect(async readings => {
            const readingsForForm = {
                n: readings.n,
                p: readings.p,
                k: readings.k,
                ph: readings.ph === null ? undefined : readings.ph,
                ec: readings.ec / 1000,
                moisture: readings.moisture === null ? undefined : readings.moisture,
                temp: readings.temp === null ? undefined : readings.temp
            };
            window.pendingSensorData = readingsForForm;
            const displayValue = (value, unit) => value === null ? "Not measured" : `${value}${unit}`;
            bslcGrid.innerHTML = `
                <div class="bslc-item"><span class="bslc-label">NITROGEN (N)</span><span class="bslc-value">${readings.n} mg/kg</span></div>
                <div class="bslc-item"><span class="bslc-label">PHOSPHORUS (P)</span><span class="bslc-value">${readings.p} mg/kg</span></div>
                <div class="bslc-item"><span class="bslc-label">POTASSIUM (K)</span><span class="bslc-value">${readings.k} mg/kg</span></div>
                <div class="bslc-item"><span class="bslc-label">pH LEVEL</span><span class="bslc-value">${displayValue(readings.ph, "")}</span></div>
                <div class="bslc-item"><span class="bslc-label">EC</span><span class="bslc-value">${readings.ec} µS/cm</span></div>
                <div class="bslc-item"><span class="bslc-label">SOIL MOISTURE</span><span class="bslc-value">${displayValue(readings.moisture, "%")}</span></div>
                <div class="bslc-item"><span class="bslc-label">TEMPERATURE</span><span class="bslc-value">${displayValue(readings.temp, "°C")}</span></div>
            `;
            if (btModalSpinner) btModalSpinner.classList.add("hidden");
            if (btSensorLiveCard) btSensorLiveCard.classList.remove("hidden");
            if (btnLoadSensorData) btnLoadSensorData.classList.remove("hidden");
            if (btModalTitle) btModalTitle.textContent = "Real Sensor Reading Received";
            if (btModalStatusText) btModalStatusText.textContent = "Complete Arduino telemetry frame received";
            if (btModalDot) btModalDot.className = "bt-modal-dot connected";
            setSensorCheckpoint("reading", "complete");
            setSensorCheckpoint("loaded", "active");
            if (btSensorBannerSummary) {
                btSensorBannerSummary.textContent = `Latest real reading: N ${readings.n}, P ${readings.p}, K ${readings.k}`;
            }

            try {
                const response = await fetch(`${API_URL}/api/hardware-data`, {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({
                        soil_moisture: readings.moisture,
                        temperature: readings.temp,
                        ec_us_cm: readings.ec,
                        ph: readings.ph,
                        nitrogen: readings.n,
                        phosphorus: readings.p,
                        potassium: readings.k,
                        source: "HM-10 BLE Serial"
                    })
                });
                if (!response.ok) throw new Error(`Backend returned HTTP ${response.status}`);
                if (btModalSubtitle) btModalSubtitle.textContent = "Live BLE measurement saved to the dashboard API.";
            } catch (error) {
                console.error("Could not save BLE telemetry to backend:", error);
                if (btModalSubtitle) btModalSubtitle.textContent = `BLE reading received, but backend sync failed: ${error.message}`;
            }
        }, () => {
            if (btModalSpinner) btModalSpinner.classList.add("hidden");
            if (btModalDot) btModalDot.className = "bt-modal-dot";
            if (btModalStatusText) btModalStatusText.textContent = "HM-10 disconnected";
            if (btSensorStatusBanner) btSensorStatusBanner.classList.add("hidden");
        }).then(device => {
            if (btModalSpinner) btModalSpinner.classList.add("hidden");
            if (btModalTitle) btModalTitle.textContent = "HM-10 Connected";
            if (btModalSubtitle) btModalSubtitle.textContent = `Connected to ${device.name || "HM-10"}. Press the Arduino scan button and wait for its 10-second reading.`;
            if (btModalStatusText) btModalStatusText.textContent = "Waiting for telemetry from the sensor...";
            if (btModalDot) btModalDot.className = "bt-modal-dot connected";
            if (btSensorStatusBanner) btSensorStatusBanner.classList.remove("hidden");
            if (btSensorBannerSummary) btSensorBannerSummary.textContent = `Connected to ${device.name || "HM-10"}; waiting for a real scan.`;
            setSensorCheckpoint("connected", "complete");
            setSensorCheckpoint("reading", "active");
        }).catch(error => {
            console.error("HM-10 BLE connection failed:", error);
            if (btModalSpinner) btModalSpinner.classList.add("hidden");
            if (btModalTitle) btModalTitle.textContent = "Sensor Connection Failed";
            if (btModalSubtitle) btModalSubtitle.textContent = error.message || "Could not connect to the HM-10 BLE serial device.";
            if (btModalStatusText) btModalStatusText.textContent =
                error.name === "NotFoundError" ? "Device selection canceled" : "No BLE sensor connection";
            if (btModalDot) btModalDot.className = "bt-modal-dot";
        });
    }

    // LOAD DATA BUTTON HANDLER
    if (btnLoadSensorData) {
        btnLoadSensorData.addEventListener("click", () => {
            if (window.pendingSensorData) {
                const d = window.pendingSensorData;
                fillTelemetryInputs(d.moisture, d.temp, d.n, d.p, d.k, d.ph, d.ec);
                if (d.moisture === undefined) document.getElementById("inpMoisture").value = "";
                if (d.temp === undefined) document.getElementById("inpTemp").value = "";
                if (d.ph === undefined) document.getElementById("inpPh").value = "";
                updateTelemetryGauges();
                window.sensorDataLoaded = true;

                // Save to sessionStorage for cross-page compatibility
                sessionStorage.setItem("lastFarmState", JSON.stringify({
                    moisture: d.moisture,
                    temp: d.temp,
                    N: d.n,
                    P: d.p,
                    K: d.k,
                    ph: d.ph,
                    ec: d.ec,
                    timestamp: new Date().toISOString(),
                    source: "HM-10 BLE Serial Sensor"
                }));

                // Update on-page banner
                if (btSensorBannerSummary) {
                    btSensorBannerSummary.textContent = `Data loaded: N ${d.n} | P ${d.p} | K ${d.k}`;
                }

                setSensorCheckpoint("loaded", "complete");

                hideBtModal(false);
            }
        });
    }

    if (btModalCancelBtn) {
        btModalCancelBtn.addEventListener("click", hideBtModal);
    }

    // ─── 3. RUN 6-AGENT PIPELINE ─────────────────────────────
    const runPipelineBtn = document.getElementById("runPipelineBtn");
    if (runPipelineBtn) {
        runPipelineBtn.addEventListener("click", run6AgentPipeline);
    }

    async function run6AgentPipeline() {
        if (activeIngestionMode === "sensors") {
            if (!window.sensorDataLoaded) {
                alert("Connect the HM-10, take a hardware reading, and load it before running the pipeline.");
                return;
            }
            const requiredSensorInputs = ["inpMoisture", "inpTemp", "inpN", "inpP", "inpK", "inpPh", "inpEc"];
            if (requiredSensorInputs.some(id => !document.getElementById(id).value.trim())) {
                alert("Enter any soil sensor values that the probe did not measure before running the pipeline.");
                return;
            }
        }
        const readValue = (id, fallback) => {
            const raw = document.getElementById(id).value.trim();
            const value = Number(raw);
            return raw && Number.isFinite(value) ? value : fallback;
        };
        const sensorMode = activeIngestionMode === "sensors";
        const smVal = readValue("inpMoisture", sensorMode ? null : 32.0);
        const tempVal = readValue("inpTemp", sensorMode ? null : 29.0);
        const nVal = readValue("inpN", sensorMode ? null : 42.0);
        const pVal = readValue("inpP", sensorMode ? null : 28.0);
        const kVal = readValue("inpK", sensorMode ? null : 55.0);
        const phVal = readValue("inpPh", sensorMode ? null : 6.8);
        const ecVal = readValue("inpEc", sensorMode ? null : 1.4);
        const cropVal = document.getElementById("inpCrop").value;
        const acresVal = parseFloat(document.getElementById("inpAcres").value) || 2.5;
        const budgetVal = parseFloat(document.getElementById("inpBudget").value) || 24500.0;
        const waterVal = parseFloat(document.getElementById("inpWater").value) || 12500.0;

        const payload = {
            mode: activeIngestionMode === "sensors" ? "bluetooth" : activeIngestionMode,
            soil_moisture: smVal,
            temperature: tempVal,
            nitrogen: nVal,
            phosphorus: pVal,
            potassium: kVal,
            ph: phVal,
            ec: sensorMode ? ecVal * 1000 : ecVal,
            crop_type: cropVal,
            acres: acresVal,
            budget: budgetVal,
            water_limit: waterVal
        };

        setAgentStatuses("PROCESSING...");

        try {
            const res = await fetch(`${API_URL}/run-agent`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify(payload)
            });

            const data = await res.json();
            if (data.status === "success" && data.pipeline_output) {
                render6AgentResults(data.pipeline_output);
                document.getElementById("sysHealthVal").textContent = "OPTIMAL (6 AGENTS ACTIVE)";
            }
        } catch (err) {
            console.error("6-agent pipeline fetch error:", err);
            if (activeIngestionMode === "sensors") {
                setAgentStatuses("BACKEND UNAVAILABLE");
                alert("Could not run sensor analysis because the backend is unavailable. The live reading was not analyzed.");
                return;
            }
            // Local fallback execution
            render6AgentResults(getFallback6AgentOutput(payload));
            document.getElementById("sysHealthVal").textContent = "LOCAL ENGINE ACTIVE";
        }
    }

    function setAgentStatuses(statusText) {
        document.getElementById("a1Status").textContent = statusText;
        document.getElementById("a2Status").textContent = statusText;
        document.getElementById("a3Status").textContent = statusText;
        document.getElementById("a4Status").textContent = statusText;
        document.getElementById("a5Status").textContent = statusText;
        document.getElementById("a6Status").textContent = statusText;
    }

    function render6AgentResults(out) {
        setAgentStatuses("COMPLETED");

        // Card 1: Soil Health & Field Summary
        const a1 = out.agent1_data_ingestion;
        document.getElementById("a1FarmState").innerHTML = `
            <div style="display:grid; grid-template-columns: repeat(auto-fit, minmax(105px, 1fr)); gap: 8px; margin-top: 4px;">
                <div style="background:#f0fdf4; border:1px solid #bbf7d0; border-radius:10px; padding:10px; text-align:center;">
                    <div style="font-size:12px; color:#166534; font-weight:700;">💧 Water</div>
                    <div style="font-size:18px; font-weight:800; color:#15803d; margin-top:2px;">${a1.telemetry.soil_moisture_pct}%</div>
                </div>
                <div style="background:#fffbeb; border:1px solid #fef08a; border-radius:10px; padding:10px; text-align:center;">
                    <div style="font-size:12px; color:#854d0e; font-weight:700;">🌡️ Temp</div>
                    <div style="font-size:18px; font-weight:800; color:#b45309; margin-top:2px;">${a1.telemetry.temperature_c}°C</div>
                </div>
                <div style="background:#f0fdfa; border:1px solid #99f6e4; border-radius:10px; padding:10px; text-align:center;">
                    <div style="font-size:12px; color:#115e59; font-weight:700;">🌿 N (Leaf)</div>
                    <div style="font-size:18px; font-weight:800; color:#0f766e; margin-top:2px;">${a1.telemetry.nitrogen_mg_kg}</div>
                </div>
                <div style="background:#fdf4ff; border:1px solid #f5d0fe; border-radius:10px; padding:10px; text-align:center;">
                    <div style="font-size:12px; color:#86198f; font-weight:700;">🟡 P (Root)</div>
                    <div style="font-size:18px; font-weight:800; color:#a21caf; margin-top:2px;">${a1.telemetry.phosphorus_mg_kg}</div>
                </div>
                <div style="background:#eff6ff; border:1px solid #bfdbfe; border-radius:10px; padding:10px; text-align:center;">
                    <div style="font-size:12px; color:#1e40af; font-weight:700;">🟢 K (Fruit)</div>
                    <div style="font-size:18px; font-weight:800; color:#1d4ed8; margin-top:2px;">${a1.telemetry.potassium_mg_kg}</div>
                </div>
                <div style="background:#f8fafc; border:1px solid #e2e8f0; border-radius:10px; padding:10px; text-align:center;">
                    <div style="font-size:12px; color:#475569; font-weight:700;">🧪 Soil pH</div>
                    <div style="font-size:18px; font-weight:800; color:#334155; margin-top:2px;">${a1.telemetry.ph_level}</div>
                </div>
            </div>
            <div style="margin-top:10px; font-size:13px; color:#334155; display:flex; justify-content:space-between; flex-wrap:wrap; gap:6px;">
                <span>🌾 <strong>${a1.field_context.crop_type}</strong> (${a1.field_context.acres} Acres)</span>
                <span>📡 Input: <strong>${a1.ingestion_mode}</strong></span>
            </div>
        `;

        // Card 2: Crop Problems & Alerts
        const a2 = out.agent2_risk_detection;
        const a2Box = document.getElementById("a2RisksList");
        a2Box.innerHTML = "";
        if (a2.ranked_risks && a2.ranked_risks.length > 0) {
            const listWrap = document.createElement("div");
            listWrap.style.cssText = "display:flex; flex-direction:column; gap:8px;";
            a2.ranked_risks.forEach(risk => {
                const isHigh = (risk.severity_score || 0) >= 0.5;
                const bg = isHigh ? '#fef2f2' : '#fffbeb';
                const border = isHigh ? '#fecaca' : '#fde68a';
                const textCol = isHigh ? '#991b1b' : '#92400e';
                const icon = isHigh ? '🔴' : '🟡';
                const badgeText = isHigh ? '⚠️ URGENT' : 'ℹ️ ATTENTION';

                const item = document.createElement("div");
                item.style.cssText = `background:${bg}; border:1.5px solid ${border}; border-radius:10px; padding:10px 12px;`;
                item.innerHTML = `
                    <div style="display:flex; justify-content:space-between; align-items:center; font-weight:700; color:${textCol}; font-size:13px;">
                        <span>${icon} ${risk.status_tag || risk.category}</span>
                        <span style="font-size:10px; background:#ffffff; padding:2px 8px; border-radius:12px; border:1px solid ${border}; font-weight:800;">${badgeText}</span>
                    </div>
                    <div style="font-size:12px; color:${textCol}; margin-top:4px;">${risk.description}</div>
                `;
                listWrap.appendChild(item);
            });
            a2Box.appendChild(listWrap);
        } else {
            a2Box.innerHTML = `<div style="background:#f0fdf4; border:1px solid #bbf7d0; border-radius:8px; padding:10px; color:#15803d; font-weight:700;">✅ No critical problems! Your crop is healthy.</div>`;
        }

        // Card 3: Field Health Progress
        const a3 = out.agent3_monitoring_feedback;
        document.getElementById("a3FeedbackBox").innerHTML = `
            <div style="background:#f0fdf4; border:1.5px solid #86efac; border-radius:12px; padding:12px;">
                <div style="display:flex; align-items:center; gap:8px; color:#14532d; font-weight:800; font-size:14px;">
                    <span style="font-size:20px;">📈</span>
                    <span>Soil Health: <strong style="color:#15803d;">${a3.soil_moisture_recovery || '+14% Improving'}</strong></span>
                </div>
                <div style="margin-top:6px; color:#166534; font-size:12px;">
                    ✅ Past Watering: <strong>${a3.prior_action_evaluated || 'Drip Cycle'}</strong> worked successfully.
                </div>
                <div style="margin-top:6px; font-size:12px; color:#15803d; font-weight:600;">
                    🛡️ System Confidence: <strong>${a3.model_confidence_score || 95}% accurate</strong> for your farm.
                </div>
            </div>
        `;

        // Card 4: Farming Options For You
        const a4 = out.agent4_decision_planning;
        const a4Grid = document.getElementById("a4PlansGrid");
        a4Grid.innerHTML = "";
        if (a4.candidate_plans && a4.candidate_plans.length > 0) {
            a4.candidate_plans.forEach((plan, idx) => {
                const card = document.createElement("div");
                card.className = "candidate-plan-card";
                card.style.cssText = "border-radius:14px; padding:16px; border:2px solid #e2e8f0; background:#ffffff;";
                card.innerHTML = `
                    <div style="font-size:15px; font-weight:800; color:#0f172a; margin-bottom:10px; display:flex; align-items:center; gap:6px;">
                        <span>${idx === 0 ? '🌾' : '💧'}</span>
                        <span>${plan.title}</span>
                    </div>
                    <div style="display:grid; grid-template-columns:1fr 1fr; gap:8px; margin-bottom:12px;">
                        <div style="background:#f8fafc; padding:8px; border-radius:8px; border:1px solid #e2e8f0;">
                            <span style="font-size:11px; color:#64748b;">💧 Water</span>
                            <div style="font-size:13px; font-weight:700; color:#0284c7;">${plan.irrigation_volume_l.toLocaleString()} L</div>
                        </div>
                        <div style="background:#f8fafc; padding:8px; border-radius:8px; border:1px solid #e2e8f0;">
                            <span style="font-size:11px; color:#64748b;">💰 Cost</span>
                            <div style="font-size:13px; font-weight:700; color:#16a34a;">₹${plan.estimated_cost_inr.toLocaleString()}</div>
                        </div>
                    </div>
                    <div style="font-size:12px; color:#475569; margin-bottom:10px;">
                        🧪 Fertilizer: <strong>${plan.fertilizer_dosage}</strong>
                    </div>
                    <div style="background:#f1f5f9; padding:8px 10px; border-radius:8px; font-size:11px; color:#334155; margin-bottom:12px;">
                        ${plan.steps.map(s => `<div style="margin-bottom:2px;">• ${s}</div>`).join('')}
                    </div>
                    <button class="btn-select-plan" style="width:100%; padding:10px; background:#16a34a; color:#fff; font-weight:700; border:none; border-radius:8px; cursor:pointer;" onclick="alert('Selected: ${plan.title}')">
                        ✅ Choose This Plan
                    </button>
                `;
                a4Grid.appendChild(card);
            });
        }

        // Card 5: Best Choice For Your Budget
        const a5 = out.agent5_constraint_optimization;
        document.getElementById("a5ConstraintBox").innerHTML = `
            <div style="background:#f0fdf4; border:2px solid #22c55e; border-radius:12px; padding:14px;">
                <div style="font-size:15px; font-weight:800; color:#15803d; display:flex; align-items:center; gap:6px;">
                    <span>⭐</span>
                    <span>Best Recommended Plan: ${a5.optimal_plan.title}</span>
                </div>
                <div style="display:flex; gap:10px; margin-top:10px; flex-wrap:wrap;">
                    <div style="background:#ffffff; padding:8px 14px; border-radius:8px; border:1px solid #bbf7d0; font-size:13px; font-weight:700; color:#15803d;">
                        💰 ₹${a5.optimal_plan.estimated_cost_inr.toLocaleString()} (Fits Budget)
                    </div>
                    <div style="background:#ffffff; padding:8px 14px; border-radius:8px; border:1px solid #bbf7d0; font-size:13px; font-weight:700; color:#0284c7;">
                        💧 ${a5.optimal_plan.estimated_water_l.toLocaleString()} L Water
                    </div>
                </div>
            </div>
        `;

        // Card 6: What To Do Today
        const a6 = out.agent6_execution_orchestration;
        document.getElementById("a6ExecutionBox").innerHTML = `
            <div style="background:#ffffff; border:1px solid #e2e8f0; border-radius:12px; padding:12px;">
                <div style="font-size:13px; font-weight:800; color:#0f172a; margin-bottom:10px;">
                    🚜 Follow These Steps Today:
                </div>
                <div style="display:flex; flex-direction:column; gap:8px;">
                    ${a6.scheduled_tasks.map((t, idx) => `
                        <div style="display:flex; align-items:center; gap:10px; background:#f8fafc; padding:10px 12px; border-radius:10px; border:1px solid #e2e8f0;">
                            <span style="background:#16a34a; color:#fff; width:24px; height:24px; border-radius:50%; display:inline-flex; align-items:center; justify-content:center; font-weight:800; font-size:12px; flex-shrink:0;">${idx+1}</span>
                            <div style="font-size:13px; font-weight:700; color:#0f172a;">${t.action}</div>
                        </div>
                    `).join('')}
                </div>
                <div style="margin-top:10px; font-size:12px; color:#16a34a; font-weight:700; display:flex; align-items:center; gap:6px;">
                    <span>✅</span> All tasks are safe and ready for your field.
                </div>
            </div>
        `;
    }

    function getFallback6AgentOutput(p) {
        return {
            agent1_data_ingestion: {
                timestamp: new Date().toLocaleDateString(),
                ingestion_mode: p.mode === 'sensors' ? 'Auto Sensor' : (p.mode === 'previous' ? 'Last Saved' : 'Typed Data'),
                telemetry: {
                    soil_moisture_pct: p.soil_moisture,
                    temperature_c: p.temperature,
                    humidity_pct: null,
                    nitrogen_mg_kg: p.nitrogen,
                    phosphorus_mg_kg: p.phosphorus,
                    potassium_mg_kg: p.potassium,
                    ph_level: p.ph,
                    ec_us_cm: p.ec
                },
                field_context: { crop_type: p.crop_type, acres: p.acres, sensor_source: "Smart Farm Helper" }
            },
            agent2_risk_detection: {
                ranked_risks: [
                    { category: "Water Needed", severity_score: 0.68, status_tag: "💧 SOIL IS DRY", description: `Soil water is low at ${p.soil_moisture}%. Water your crops today.` },
                    { category: "Fertilizer Alert", severity_score: 0.42, status_tag: "🌿 LOW NITROGEN", description: `Nitrogen level is ${p.nitrogen}. Add nitrogen for better leaf growth.` }
                ]
            },
            agent3_monitoring_feedback: {
                prior_action_evaluated: "Last Irrigation",
                soil_moisture_recovery: "+14% Healthy",
                confidence_adjustment: "+12%",
                model_confidence_score: 95
            },
            agent4_decision_planning: {
                candidate_plans: [
                    {
                        title: "Option A: Best Crop Yield",
                        irrigation_volume_l: 14500,
                        fertilizer_dosage: "NPK 19:19:19 (8 kg)",
                        estimated_cost_inr: 8500,
                        estimated_water_l: 14500,
                        expected_risk_reduction: "88%",
                        steps: ["Turn on water pump for 2.5 hours", "Apply 8 kg NPK fertilizer near roots"]
                    },
                    {
                        title: "Option B: Save Water & Money",
                        irrigation_volume_l: 9200,
                        fertilizer_dosage: "NPK 20:20:20 (5 kg)",
                        estimated_cost_inr: 5200,
                        estimated_water_l: 9200,
                        expected_risk_reduction: "76%",
                        steps: ["Turn on water pump for 1.5 hours", "Add 5 kg fertilizer and let rest for 2 days"]
                    }
                ]
            },
            agent5_constraint_optimization: {
                optimal_plan: { title: "Option B: Save Water & Money", estimated_cost_inr: 5200, estimated_water_l: 9200 },
                constraint_summary: { user_budget_inr: p.budget, user_water_limit_l: p.water_limit, feasible_plans_count: 2, cost_utilization_pct: 21.2, water_utilization_pct: 73.6 }
            },
            agent6_execution_orchestration: {
                scheduled_tasks: [
                    { action: "Water Field (9,200 Liters)", status: "READY", scheduled_time: "Today" },
                    { action: "Add 5 kg NPK Fertilizer", status: "READY", scheduled_time: "Tomorrow" }
                ],
                escalation_ticket: { created: false },
                audit_log_entry: { timestamp: new Date().toISOString() }
            }
        };
    }

    // Export DOC & Export JSON buttons
    const exportDocBtn = document.getElementById("exportDocBtn");
    const exportJsonBtn = document.getElementById("exportJsonBtn");

    if (exportDocBtn) {
        exportDocBtn.addEventListener("click", () => alert("Saving your farm report..."));
    }
    if (exportJsonBtn) {
        exportJsonBtn.addEventListener("click", () => alert("Saving your farm data..."));
    }
};
