const API_URL = "http://127.0.0.1:8001";
let plantLogs = [];
let prevPhotoData = "";
let latestPhotoData = "";

// Sample fallback crop photos if user doesn't upload a custom file
const SAMPLE_CROP_PHOTOS = {
    "Tomato": {
        prev: "https://images.unsplash.com/photo-1592924357228-91a4daadcfea?w=400&auto=format&fit=crop&q=80",
        latest: "https://images.unsplash.com/photo-1592924357228-91a4daadcfea?w=600&auto=format&fit=crop&q=80"
    },
    "Green Chilli": {
        prev: "https://images.unsplash.com/photo-1588252303782-cb80119abd6d?w=400&auto=format&fit=crop&q=80",
        latest: "https://images.unsplash.com/photo-1588252303782-cb80119abd6d?w=600&auto=format&fit=crop&q=80"
    },
    "Potato": {
        prev: "https://images.unsplash.com/photo-1518977676601-b53f82aba655?w=400&auto=format&fit=crop&q=80",
        latest: "https://images.unsplash.com/photo-1518977676601-b53f82aba655?w=600&auto=format&fit=crop&q=80"
    },
    "Wheat": {
        prev: "https://images.unsplash.com/photo-1574323347407-f5e1ad6d020b?w=400&auto=format&fit=crop&q=80",
        latest: "https://images.unsplash.com/photo-1574323347407-f5e1ad6d020b?w=600&auto=format&fit=crop&q=80"
    },
    "default": {
        prev: "https://images.unsplash.com/photo-1500937386664-56d1dfef3854?w=400&auto=format&fit=crop&q=80",
        latest: "https://images.unsplash.com/photo-1500937386664-56d1dfef3854?w=600&auto=format&fit=crop&q=80"
    }
};

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

    // Photo File Reader Handlers
    setupPhotoUploader("prevPhotoInput", "prevPhotoPreview", "prevPlaceholder", (dataUrl) => {
        prevPhotoData = dataUrl;
    });

    setupPhotoUploader("latestPhotoInput", "latestPhotoPreview", "latestPlaceholder", (dataUrl) => {
        latestPhotoData = dataUrl;
    });

    function setupPhotoUploader(inputId, imgId, placeholderId, callback) {
        const input = document.getElementById(inputId);
        const img = document.getElementById(imgId);
        const placeholder = document.getElementById(placeholderId);

        if (!input || !img) return;

        input.addEventListener("change", (e) => {
            const file = e.target.files[0];
            if (file) {
                const reader = new FileReader();
                reader.onload = function (evt) {
                    const result = evt.target.result;
                    img.src = result;
                    img.classList.remove("hidden");
                    if (placeholder) placeholder.style.display = "none";
                    callback(result);
                };
                reader.readAsDataURL(file);
            }
        });
    }

    // Load saved logs from localStorage
    try {
        const saved = localStorage.getItem("agritech_plant_growth_logs");
        if (saved) plantLogs = JSON.parse(saved);
    } catch (e) {
        plantLogs = [];
    }
    renderHistoryTable();

    // Form submit listener
    const analyzeBtn = document.getElementById("analyzeGrowthBtn");
    if (analyzeBtn) {
        analyzeBtn.addEventListener("click", runPlantGrowthAnalysis);
    }

    async function runPlantGrowthAnalysis() {
        const cropNameVal = document.getElementById("cropName").value;
        const prevHVal = parseFloat(document.getElementById("prevHeight").value);
        const latestHVal = parseFloat(document.getElementById("latestHeight").value);
        const daysVal = parseInt(document.getElementById("daysElapsed").value) || 7;
        const stageVal = document.getElementById("growthStage").value;
        const leafCondVal = document.getElementById("leafCondition").value || "Healthy";

        if (isNaN(prevHVal) || isNaN(latestHVal)) {
            alert("Please enter valid numbers for previous height (cm) and latest height (cm).");
            return;
        }

        const samplePhotos = SAMPLE_CROP_PHOTOS[cropNameVal] || SAMPLE_CROP_PHOTOS["default"];
        const activePrevPhoto = prevPhotoData || samplePhotos.prev;
        const activeLatestPhoto = latestPhotoData || samplePhotos.latest;

        const payload = {
            crop_name: cropNameVal,
            previous_height_cm: prevHVal,
            latest_height_cm: latestHVal,
            days_elapsed: daysVal,
            stage: stageVal,
            leaf_condition: leafCondVal
        };

        try {
            const res = await fetch(`${API_URL}/api/plant-growth`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify(payload)
            });

            const data = await res.json();
            if (data.status === "success") {
                displayAnalysisResults(data, activePrevPhoto, activeLatestPhoto);
                saveLogEntry(data, activePrevPhoto, activeLatestPhoto);
            }
        } catch (err) {
            console.error("Plant growth fetch error:", err);
            // Local calculation fallback
            const diff = latestHVal - prevHVal;
            const pct = prevHVal > 0 ? ((diff / prevHVal) * 100).toFixed(1) : 0;
            const rate = (diff / daysVal).toFixed(2);
            const fallbackData = {
                crop_name: cropNameVal,
                previous_height_cm: prevHVal,
                latest_height_cm: latestHVal,
                diff_cm: diff,
                pct_growth: pct,
                days_elapsed: daysVal,
                daily_rate_cm: rate,
                stage: stageVal,
                leaf_condition: leafCondVal,
                status_tag: diff > 0 ? "STABLE HEALTHY GROWTH" : "STUNTED GROWTH WARNING",
                status_color: diff > 0 ? "#10b981" : "#ef4444",
                speed_rating: "Normal",
                insight_summary: `Your ${cropNameVal} grew +${diff.toFixed(1)} cm in ${daysVal} days (${rate} cm/day, +${pct}% growth).`,
                suggestions: [
                    "💧 Irrigation: Water regime is optimal. Maintain root zone moisture.",
                    "🧪 Fertilizer: Apply balanced NPK 20:20:20 dosage.",
                    "🪴 Staking: Support stems with bamboo stakes if needed.",
                    "🛡️ Pest Protection: Inspect leaf undersides for pests."
                ]
            };
            displayAnalysisResults(fallbackData, activePrevPhoto, activeLatestPhoto);
            saveLogEntry(fallbackData, activePrevPhoto, activeLatestPhoto);
        }
    }

    function displayAnalysisResults(data, prevPhoto, latestPhoto) {
        // Status Badge
        const badge = document.getElementById("growthStatusBadge");
        badge.textContent = data.status_tag;
        badge.style.background = `${data.status_color}20`;
        badge.style.color = data.status_color;

        // Side-by-Side Photos & Tags
        document.getElementById("cmpPrevImg").src = prevPhoto;
        document.getElementById("cmpLatestImg").src = latestPhoto;

        document.getElementById("cmpPrevTag").textContent = `${data.previous_height_cm} cm`;
        document.getElementById("cmpLatestTag").textContent = `${data.latest_height_cm} cm`;

        const diffSign = data.diff_cm >= 0 ? "+" : "";
        document.getElementById("cmpDeltaVal").textContent = `${diffSign}${data.diff_cm} cm`;
        document.getElementById("cmpDeltaSub").textContent = `${diffSign}${data.pct_growth}% Growth`;

        // KPI metrics
        document.getElementById("resGainCm").textContent = `${diffSign}${data.diff_cm} cm`;
        document.getElementById("resGainPct").textContent = `${diffSign}${data.pct_growth}% total gain`;

        document.getElementById("resRateCm").textContent = `${data.daily_rate_cm} cm/day`;
        document.getElementById("resSpeedRating").textContent = `Speed: ${data.speed_rating}`;

        document.getElementById("resStage").textContent = data.stage;

        // AI Suggestions List
        document.getElementById("sugSummary").innerHTML = data.insight_summary;

        const sugList = document.getElementById("sugList");
        sugList.innerHTML = "";

        if (data.suggestions && data.suggestions.length > 0) {
            data.suggestions.forEach(item => {
                const li = document.createElement("li");
                li.innerHTML = item.replace(/\*\*(.*?)\*\*/g, "<strong>$1</strong>");
                sugList.appendChild(li);
            });
        }
    }

    function saveLogEntry(data, prevPhoto, latestPhoto) {
        const logItem = {
            date: new Date().toLocaleDateString("en-IN", { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" }),
            crop: data.crop_name,
            prevH: data.previous_height_cm,
            latestH: data.latest_height_cm,
            diff: data.diff_cm,
            rate: data.daily_rate_cm,
            stage: data.stage,
            status: data.status_tag,
            statusColor: data.status_color,
            prevImg: prevPhoto,
            latestImg: latestPhoto
        };

        plantLogs.unshift(logItem);
        if (plantLogs.length > 10) plantLogs.pop();

        try {
            localStorage.setItem("agritech_plant_growth_logs", JSON.stringify(plantLogs));
        } catch (e) {}

        renderHistoryTable();
    }

    function renderHistoryTable() {
        const tbody = document.getElementById("growthHistoryBody");
        tbody.innerHTML = "";

        if (plantLogs.length === 0) {
            tbody.innerHTML = '<tr><td colspan="9" style="text-align:center;color:#888;padding:20px;">No plant growth logs saved yet.</td></tr>';
            return;
        }

        plantLogs.forEach(item => {
            const tr = document.createElement("tr");
            const isPos = item.diff >= 0;
            tr.innerHTML = `
                <td>${item.date}</td>
                <td><strong>${item.crop}</strong></td>
                <td>
                    <div style="display:flex;align-items:center;gap:6px">
                        <img src="${item.prevImg}" class="tbl-thumb" alt="Previous"/>
                        <span style="font-size:10px;color:#888">➔</span>
                        <img src="${item.latestImg}" class="tbl-thumb" alt="Latest"/>
                    </div>
                </td>
                <td>${item.prevH} cm</td>
                <td>${item.latestH} cm</td>
                <td style="color:${isPos ? '#16a34a' : '#dc2626'};font-weight:700">${isPos ? '+' : ''}${item.diff} cm</td>
                <td style="font-weight:600">${item.rate} cm/day</td>
                <td><span style="font-size:10px;font-family:var(--font-mono);background:#f1f5f9;padding:2px 6px;border-radius:4px">${item.stage}</span></td>
                <td><span style="font-size:10px;font-family:var(--font-mono);padding:2px 6px;border-radius:4px;background:${item.statusColor}20;color:${item.statusColor};font-weight:700">${item.status}</span></td>
            `;
            tbody.appendChild(tr);
        });
    }

    const clearLogsBtn = document.getElementById("clearGrowthLogsBtn");
    if (clearLogsBtn) {
        clearLogsBtn.addEventListener("click", () => {
            plantLogs = [];
            localStorage.removeItem("agritech_plant_growth_logs");
            renderHistoryTable();
        });
    }
};
