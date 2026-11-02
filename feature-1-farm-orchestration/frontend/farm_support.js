const API_URL = "http://127.0.0.1:8001";
let userLat = null;
let userLon = null;
let currentCategory = "all";

window.onload = function () {
    // Chatbot setup
    const openChat = document.getElementById("openChatbotBtn");
    const closeChat = document.getElementById("chatbotClose");
    const chatPopup = document.getElementById("chatbotPopup");
    const sendBtn = document.getElementById("chatSend");
    const chatInput = document.getElementById("chatInput");
    const chatBox = document.getElementById("chatMessages");
    const gpsCoords = document.getElementById("gpsSupportCoords");
    const refreshGpsBtn = document.getElementById("refreshSupportGpsBtn");

    try {
        const savedLocation = JSON.parse(localStorage.getItem("farm_assistant_location") || "null");
        if (Number.isFinite(savedLocation?.lat) && Number.isFinite(savedLocation?.lon)) {
            userLat = savedLocation.lat;
            userLon = savedLocation.lon;
            gpsCoords.textContent = `Saved location: ${userLat.toFixed(4)}°, ${userLon.toFixed(4)}°`;
        }
    } catch (error) {
        console.warn("Saved farm location could not be read.", error);
    }

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
        bubble.textContent = text;

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
                body: JSON.stringify({ message: text, ...window.getFarmAssistantContext() })
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

    function showLocationUnavailable(message) {
        gpsCoords.textContent = message;
        document.getElementById("supportGrid").textContent =
            "Allow location access and select GPS Sync to search using your current location.";
    }

    // Detect GPS on demand and allow farmers to refresh it after moving.
    function getGPSLocation() {
        if (!navigator.geolocation) {
            showLocationUnavailable("Location is not supported by this browser.");
            return;
        }
        gpsCoords.textContent = "Requesting current GPS location...";
        if (refreshGpsBtn) refreshGpsBtn.disabled = true;
        navigator.geolocation.getCurrentPosition(
            (pos) => {
                userLat = pos.coords.latitude;
                userLon = pos.coords.longitude;
                try {
                    localStorage.setItem("farm_assistant_location", JSON.stringify({ lat: userLat, lon: userLon }));
                } catch (error) {
                    console.warn("Current farm location could not be saved.", error);
                }
                gpsCoords.textContent = `Current: ${userLat.toFixed(4)}°, ${userLon.toFixed(4)}°`;
                fetchFarmSupportData();
                if (refreshGpsBtn) refreshGpsBtn.disabled = false;
            },
            (error) => {
                console.warn("GPS error:", error.message);
                const messages = {
                    1: "Location permission denied. Allow it in browser settings, then sync again.",
                    2: "Current location is unavailable. Try GPS Sync again outdoors.",
                    3: "GPS request timed out. Try GPS Sync again."
                };
                const errorMessage = messages[error.code] || "Could not read the current location. Try GPS Sync again.";
                if (userLat !== null && userLon !== null) {
                    gpsCoords.textContent = `Using saved location: ${userLat.toFixed(4)}°, ${userLon.toFixed(4)}° · ${errorMessage}`;
                    fetchFarmSupportData();
                } else {
                    showLocationUnavailable(errorMessage);
                }
                if (refreshGpsBtn) refreshGpsBtn.disabled = false;
            },
            { enableHighAccuracy: true, maximumAge: 0, timeout: 15000 }
        );
    }
    if (refreshGpsBtn) refreshGpsBtn.addEventListener("click", getGPSLocation);

    async function fetchFarmSupportData() {
        if (!Number.isFinite(userLat) || !Number.isFinite(userLon)) {
            showLocationUnavailable("No GPS location available yet.");
            return;
        }
        const grid = document.getElementById("supportGrid");
        grid.innerHTML = '<div class="loading-state">⚡ Locating nearest farm support & fertilizer stores...</div>';

        try {
            const params = new URLSearchParams({
                type: currentCategory,
                lat: userLat,
                lon: userLon
            });
            let res;
            let lastError;
            for (let attempt = 0; attempt < 2; attempt += 1) {
                try {
                    res = await fetch(`${API_URL}/api/farm-support?${params}`, {
                        signal: AbortSignal.timeout(15000)
                    });
                    if (res.ok || res.status < 500 || attempt === 1) break;
                } catch (error) {
                    lastError = error;
                    if (attempt === 1) throw error;
                }
                await new Promise(resolve => setTimeout(resolve, 750));
            }
            if (!res && lastError) throw lastError;
            if (!res.ok) {
                throw new Error(`Farm support API returned HTTP ${res.status}`);
            }
            const data = await res.json();

            if (data.status !== "success" || !Array.isArray(data.results)) {
                throw new Error(data.message || data.detail || "Farm support locations are unavailable.");
            }
            renderSupportCards(data.results);
        } catch (err) {
            console.error("Support fetch error:", err);
            grid.textContent = `Farm support locations could not be loaded: ${err.message}. Check that the backend is running, then retry.`;
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

    if (userLat !== null && userLon !== null) {
        fetchFarmSupportData();
    } else {
        gpsCoords.textContent = "Location not synced yet.";
        showLocationUnavailable("Location not synced yet.");
    }
};
