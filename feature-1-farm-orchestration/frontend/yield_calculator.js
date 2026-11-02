const API_URL = "http://127.0.0.1:8001";
let yieldHistory = [];

window.onload = function () {
    // Chatbot Setup
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

    // Load saved history from localStorage
    try {
        const saved = localStorage.getItem("agritech_yield_history");
        if (saved) yieldHistory = JSON.parse(saved);
    } catch (e) {
        yieldHistory = [];
    }
    renderHistoryTable();

    // Form Submit Handler
    const calcYieldBtn = document.getElementById("calcYieldBtn");
    if (calcYieldBtn) {
        calcYieldBtn.addEventListener("click", runYieldAnalysis);
    }

    async function runYieldAnalysis() {
        const prevYieldVal = parseFloat(document.getElementById("prevYield").value);
        const latestYieldVal = parseFloat(document.getElementById("latestYield").value);
        const cropNameVal = document.getElementById("cropSelect").value;
        const acresVal = parseFloat(document.getElementById("landAcres").value) || 1.0;
        const priceVal = parseFloat(document.getElementById("pricePerKg").value) || 25.0;

        if (isNaN(prevYieldVal) || isNaN(latestYieldVal)) {
            alert("Please enter valid numbers for previous and latest yield (in kgs).");
            return;
        }

        const payload = {
            previous_yield: prevYieldVal,
            latest_yield: latestYieldVal,
            crop_name: cropNameVal,
            acres: acresVal,
            price_per_kg: priceVal
        };

        try {
            const res = await fetch(`${API_URL}/api/yield-analysis`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify(payload)
            });

            const data = await res.json();

            if (data.status === "success") {
                displayYieldResult(data);
                saveToHistory(data);
            }
        } catch (err) {
            console.error("Yield analysis error:", err);
            // Local calculation fallback if backend server isn't reached
            const diff = latestYieldVal - prevYieldVal;
            const pct = ((diff / prevYieldVal) * 100).toFixed(2);
            displayYieldResult({
                status_tag: pct > 0 ? "HARVEST GAIN DETECTED" : "YIELD DEFICIT",
                status_color: pct > 0 ? "#10b981" : "#ef4444",
                previous_yield_kg: prevYieldVal,
                latest_yield_kg: latestYieldVal,
                difference_kg: diff,
                percentage_change: pct,
                previous_per_acre: (prevYieldVal / acresVal).toFixed(1),
                latest_per_acre: (latestYieldVal / acresVal).toFixed(1),
                latest_revenue_inr: latestYieldVal * priceVal,
                previous_revenue_inr: prevYieldVal * priceVal,
                revenue_diff_inr: diff * priceVal,
                ai_insight: `Local Calculation: Net change is ${diff >= 0 ? '+' : ''}${diff} kg (${pct}%). Recommend tracking irrigation and soil health.`
            });
        }
    }

    function displayYieldResult(data) {
        const badge = document.getElementById("statusBadge");
        badge.textContent = data.status_tag;
        badge.style.background = `${data.status_color}20`;
        badge.style.color = data.status_color;

        document.getElementById("resPrevKg").textContent = `${data.previous_yield_kg.toLocaleString()} kg`;
        document.getElementById("resPrevPerAcre").textContent = `${data.previous_per_acre} kg/acre`;

        document.getElementById("resLatestKg").textContent = `${data.latest_yield_kg.toLocaleString()} kg`;
        document.getElementById("resLatestPerAcre").textContent = `${data.latest_per_acre} kg/acre`;

        const diffSymbol = data.difference_kg >= 0 ? "+" : "";
        document.getElementById("resDiffKg").textContent = `${diffSymbol}${data.difference_kg.toLocaleString()} kg`;
        document.getElementById("resPctChange").textContent = `${diffSymbol}${data.percentage_change}% growth`;

        const revSign = data.revenue_diff_inr >= 0 ? "+" : "";
        document.getElementById("resRevDiff").textContent = `${revSign}₹${Math.abs(data.revenue_diff_inr).toLocaleString()}`;
        document.getElementById("resRevSub").textContent = `Prev: ₹${data.previous_revenue_inr.toLocaleString()}  |  Latest: ₹${data.latest_revenue_inr.toLocaleString()}`;

        document.getElementById("resAiInsight").textContent = data.ai_insight;
    }

    function saveToHistory(data) {
        const entry = {
            date: new Date().toLocaleDateString("en-IN", { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" }),
            crop: data.crop_name,
            prev: data.previous_yield_kg,
            latest: data.latest_yield_kg,
            diff: data.difference_kg,
            pct: data.percentage_change,
            revDiff: data.revenue_diff_inr,
            status: data.status_tag
        };

        yieldHistory.unshift(entry);
        if (yieldHistory.length > 10) yieldHistory.pop();

        try {
            localStorage.setItem("agritech_yield_history", JSON.stringify(yieldHistory));
        } catch (e) {}

        renderHistoryTable();
    }

    function renderHistoryTable() {
        const tbody = document.getElementById("historyTableBody");
        tbody.innerHTML = "";

        if (yieldHistory.length === 0) {
            tbody.innerHTML = '<tr><td colspan="8" style="text-align:center;color:#888;padding:20px;">No yield calculations saved yet.</td></tr>';
            return;
        }

        yieldHistory.forEach(item => {
            const tr = document.createElement("tr");
            const isPos = item.diff >= 0;
            tr.innerHTML = `
                <td>${item.date}</td>
                <td><strong>${item.crop}</strong></td>
                <td>${item.prev} kg</td>
                <td>${item.latest} kg</td>
                <td style="color:${isPos ? '#16a34a' : '#dc2626'};font-weight:700">${isPos ? '+' : ''}${item.diff} kg</td>
                <td style="color:${isPos ? '#16a34a' : '#dc2626'};font-weight:700">${isPos ? '+' : ''}${item.pct}%</td>
                <td style="font-weight:600">${isPos ? '+' : ''}₹${Math.abs(item.revDiff).toLocaleString()}</td>
                <td><span style="font-size:10px;font-family:var(--font-mono);padding:2px 6px;border-radius:4px;background:${isPos ? '#dcfce7' : '#fee2e2'};color:${isPos ? '#15803d' : '#991b1b'}">${item.status}</span></td>
            `;
            tbody.appendChild(tr);
        });
    }

    const clearBtn = document.getElementById("clearHistoryBtn");
    if (clearBtn) {
        clearBtn.addEventListener("click", () => {
            yieldHistory = [];
            localStorage.removeItem("agritech_yield_history");
            renderHistoryTable();
        });
    }
};
