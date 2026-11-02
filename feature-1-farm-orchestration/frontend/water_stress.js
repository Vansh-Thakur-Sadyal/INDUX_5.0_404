const API_URL = "http://127.0.0.1:8001";

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

    // Water Stress Form Calculation Listener
    const calcWsBtn = document.getElementById("calcWaterStressBtn");
    if (calcWsBtn) {
        calcWsBtn.addEventListener("click", runWaterStressCalculation);
    }

    async function runWaterStressCalculation() {
        const smVal = parseFloat(document.getElementById("wsMoisture").value) || 28.0;
        const tempVal = parseFloat(document.getElementById("wsTemp").value) || 34.0;
        const humidityVal = parseFloat(document.getElementById("wsHumidity").value) || 45.0;
        const daysVal = parseInt(document.getElementById("wsDays").value) || 5;
        const soilTypeVal = document.getElementById("wsSoilType").value;
        const acresVal = parseFloat(document.getElementById("wsAcres").value) || 2.5;

        const payload = {
            soil_moisture: smVal,
            temperature: tempVal,
            humidity: humidityVal,
            days_since_watering: daysVal,
            soil_type: soilTypeVal,
            acres: acresVal
        };

        try {
            const res = await fetch(`${API_URL}/api/water-stress`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify(payload)
            });

            const data = await res.json();
            if (data.status === "success") {
                displayWaterStressResult(data);
            }
        } catch (err) {
            console.error("Water stress fetch error:", err);
            // Local fallback calculation
            const cwsiVal = smVal < 30 ? 0.68 : smVal < 55 ? 0.35 : 0.10;
            displayWaterStressResult({
                cwsi: cwsiVal,
                stress_level: cwsiVal > 0.5 ? "MODERATE WATER STRESS" : "OPTIMAL MOISTURE BALANCE",
                stress_color: cwsiVal > 0.5 ? "#f59e0b" : "#10b981",
                water_saved_liters: Math.round(35000 * acresVal),
                cost_saved_inr: Math.round(450 * acresVal),
                action_advice: `💧 Precision Irrigation: Apply targeted root-zone watering for ${acresVal} acres. Prevents water wastage and saves electricity pumping costs!`
            });
        }
    }

    function displayWaterStressResult(data) {
        const badge = document.getElementById("wsStatusBadge");
        badge.textContent = data.stress_level;
        badge.style.background = `${data.stress_color}20`;
        badge.style.color = data.stress_color;

        // CWSI meter
        document.getElementById("cwsiValue").textContent = data.cwsi.toFixed(2);
        const fillBar = document.getElementById("cwsiBarFill");
        fillBar.style.width = `${Math.round(data.cwsi * 100)}%`;
        fillBar.style.background = data.stress_color;

        // Savings metrics
        document.getElementById("resWaterSaved").textContent = `${data.water_saved_liters.toLocaleString()} L`;
        document.getElementById("resCostSaved").textContent = `₹${data.cost_saved_inr.toLocaleString()}`;

        // Action Advice Box
        document.getElementById("resActionAdvice").innerHTML = data.action_advice.replace(/\*\*(.*?)\*\*/g, "<strong>$1</strong>");
    }
};
