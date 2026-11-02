const API_URL = "http://127.0.0.1:8001";
let userLat = 30.9010;
let userLon = 75.8573;
let currentCategory = "all";

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
            appendMessage("bot", data.reply || "Processing your request...", data.action_link, data.action_text);
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

    // Check URL parameters for category pre-selection
    const urlParams = new URLSearchParams(window.location.search);
    const paramType = urlParams.get("type");
    if (paramType) {
        currentCategory = paramType;
    }

    // Highlight button
    document.querySelectorAll(".btn-category-req").forEach(btn => {
        if (btn.getAttribute("data-type") === currentCategory) {
            btn.classList.add("active");
        } else {
            btn.classList.remove("active");
        }
        btn.addEventListener("click", () => {
            document.querySelectorAll(".btn-category-req").forEach(b => b.classList.remove("active"));
            btn.classList.add("active");
            currentCategory = btn.getAttribute("data-type");
            fetchFarmSupportData();
        });
    });

    // Detect GPS
    function getGPSLocation() {
        const gpsCoords = document.getElementById("gpsSupportCoords");
        if ("geolocation" in navigator) {
            gpsCoords.textContent = "Acquiring GPS Signal...";
            navigator.geolocation.getCurrentPosition(
                (pos) => {
                    userLat = pos.coords.latitude;
                    userLon = pos.coords.longitude;
                    gpsCoords.textContent = `${userLat.toFixed(4)}° N, ${userLon.toFixed(4)}° E`;
                    fetchFarmSupportData();
                },
                (err) => {
                    console.warn("GPS error:", err.message);
                    gpsCoords.textContent = "30.9010° N, 75.8573° E (Default Regional Hub)";
                    fetchFarmSupportData();
                },
                { timeout: 8000 }
            );
        } else {
            gpsCoords.textContent = "30.9010° N, 75.8573° E";
            fetchFarmSupportData();
        }
    }

    async function fetchFarmSupportData() {
        const grid = document.getElementById("supportGrid");
        grid.innerHTML = '<div class="loading-state">⚡ Locating nearest farm support & fertilizer stores...</div>';

        try {
            const res = await fetch(`${API_URL}/api/farm-support?type=${currentCategory}&lat=${userLat}&lon=${userLon}`);
            if (!res.ok) {
                throw new Error(`Farm support API returned HTTP ${res.status}`);
            }
            const data = await res.json();

            if (data.status !== "success") {
                throw new Error(data.message || data.detail || "Farm support locations are unavailable.");
            }
            renderSupportCards(data.results || []);
        } catch (err) {
            console.error("Support fetch error:", err);
            grid.innerHTML = '<div class="loading-state">Farm support locations could not be loaded. Please try again later.</div>';
        }
    }

    function renderSupportCards(items) {
        const grid = document.getElementById("supportGrid");
        grid.innerHTML = "";

        if (items.length === 0) {
            grid.innerHTML = '<div class="loading-state">No locations found for selected category.</div>';
            return;
        }

        items.forEach(item => {
            const card = document.createElement("div");
            card.className = "support-card";

            card.innerHTML = `
                <div class="support-img-wrap">
                    <img src="${item.image}" alt="${item.name}" loading="lazy"/>
                    <span class="category-badge-pill">${item.category_title}</span>
                    <span class="dist-badge-pill">📍 ${item.distance_km} km away</span>
                </div>
                <div class="support-card-body">
                    <div class="shop-name">${item.name}</div>
                    <div class="shop-category-sub">${item.category_title}</div>

                    <div class="shop-meta-row">
                        <span class="meta-icon">📍</span>
                        <span>${item.address}</span>
                    </div>
                    <div class="shop-meta-row">
                        <span class="meta-icon">📞</span>
                        <span>${item.phone}</span>
                    </div>
                    <div class="shop-meta-row">
                        <span class="meta-icon">🕒</span>
                        <span>${item.open_hours}</span>
                    </div>

                    <div class="shop-rating-bar">
                        <span class="star-rating">★ ${item.rating}</span>
                        <span class="reviews-count">(${item.reviews} verified reviews)</span>
                    </div>

                    <a href="${item.gmaps_url}" target="_blank" rel="noopener noreferrer" class="btn-gmaps-redirect">
                        <svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor">
                            <path d="M12 2C8.13 2 5 5.13 5 9c0 5.25 7 13 7 13s7-7.75 7-13c0-3.87-3.13-7-7-7zm0 9.5c-1.38 0-2.5-1.12-2.5-2.5s1.12-2.5 2.5-2.5 2.5 1.12 2.5 2.5-1.12 2.5-2.5 2.5z"/>
                        </svg>
                        Open in Google Maps
                    </a>
                </div>
            `;
            grid.appendChild(card);
        });
    }

    getGPSLocation();
};
