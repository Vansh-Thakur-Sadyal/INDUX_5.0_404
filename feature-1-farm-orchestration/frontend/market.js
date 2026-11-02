const API_URL = "http://127.0.0.1:8001";
let allMarketData = [];
let userLat = 30.9010;
let userLon = 75.8573;

window.onload = function () {
    // Chatbot toggles
    const openChat = document.getElementById("openChatbotBtn");
    const closeChat = document.getElementById("chatbotClose");
    const chatPopup = document.getElementById("chatbotPopup");
    const sendBtn = document.getElementById("chatSend");
    const chatInput = document.getElementById("chatInput");
    const chatBox = document.getElementById("chatMessages");
    const refreshGpsBtn = document.getElementById("refreshGpsBtn");

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
            appendMessage("bot", data.reply || "Sorry, I couldn't process that.", data.action_link, data.action_text);
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

    // Geolocation API
    function getGPSLocation() {
        const gpsCoords = document.getElementById("gpsCoords");
        if ("geolocation" in navigator) {
            gpsCoords.textContent = "Requesting GPS signal...";
            navigator.geolocation.getCurrentPosition(
                (pos) => {
                    userLat = pos.coords.latitude;
                    userLon = pos.coords.longitude;
                    gpsCoords.textContent = `${userLat.toFixed(4)}° N, ${userLon.toFixed(4)}° E`;
                    fetchMarketData();
                },
                (err) => {
                    console.warn("GPS Permission denied or unavailable:", err.message);
                    gpsCoords.textContent = "30.9010° N, 75.8573° E (Default Punjab Mandi)";
                    fetchMarketData();
                },
                { timeout: 8000 }
            );
        } else {
            gpsCoords.textContent = "GPS Unavailable (Using Regional Mandi)";
            fetchMarketData();
        }
    }

    if (refreshGpsBtn) {
        refreshGpsBtn.addEventListener("click", getGPSLocation);
    }

    // Fetch Market Data from Backend API
    async function fetchMarketData() {
        const grid = document.getElementById("marketGrid");
        grid.innerHTML = '<div class="loading-state">⚡ Synchronizing Agmarknet Mandi Prices...</div>';

        try {
            const url = `${API_URL}/api/market-prices?lat=${userLat}&lon=${userLon}`;
            const res = await fetch(url);
            const data = await res.json();

            if (data.status === "success") {
                allMarketData = data.data || [];
                const kpiSource = document.getElementById("kpiSource");
                const kpiSourceSub = document.getElementById("kpiSourceSub");
                if (kpiSource) kpiSource.textContent = data.source;
                if (kpiSourceSub) {
                    kpiSourceSub.textContent = data.api_key_configured ? "Live Key Configured" : "Paste Key in api_keys.txt";
                }
                const cropSuggestions = document.getElementById("cropSuggestions");
                if (cropSuggestions) {
                    const crops = [...new Set(allMarketData.map(item => item.commodity).filter(Boolean))].sort((a, b) => a.localeCompare(b));
                    cropSuggestions.innerHTML = crops.map(crop => `<option value="${crop}"></option>`).join("");
                }
                const marketRecordNote = document.getElementById("marketRecordNote");
                if (marketRecordNote) marketRecordNote.textContent = `Showing all ${allMarketData.length.toLocaleString()} crop and commodity records returned by ${data.source}.`;
                updateKPIs(allMarketData);
                renderCards(allMarketData);
            }
        } catch (err) {
            console.error("Market fetch error:", err);
            grid.innerHTML = '<div class="loading-state">Error loading market data. Ensure backend is running.</div>';
        }
    }

    function updateKPIs(data) {
        if (!data || data.length === 0) return;

        // Calculate average expected price
        const totalExpected = data.reduce((acc, curr) => acc + curr.modal_price, 0);
        const avg = totalExpected / data.length;
        document.getElementById("kpiAvgPrice").textContent = `₹${avg.toFixed(2)} / kg`;

        // Find top gainer
        const top = data[0] || {};
        document.getElementById("kpiTopGainer").textContent = `${top.commodity} (${top.trend || "+3.5%"})`;
        document.getElementById("kpiTopGainer").nextElementSibling.textContent = `${top.market}`;

        // Find lowest min price
        const lowest = [...data].sort((a, b) => a.min_price - b.min_price)[0];
        if (lowest) {
            document.getElementById("kpiLowestMin").textContent = `${lowest.commodity} (₹${lowest.min_price.toFixed(2)}/kg)`;
            document.getElementById("kpiLowestMin").nextElementSibling.textContent = `${lowest.market}`;
        }
    }

    function renderCards(items) {
        const grid = document.getElementById("marketGrid");
        grid.innerHTML = "";

        if (items.length === 0) {
            grid.innerHTML = '<div class="loading-state">No commodity prices found matching your filter.</div>';
            return;
        }

        items.forEach(item => {
            const card = document.createElement("div");
            card.className = "market-card";

            const trendClass = item.trend_type || (item.trend && item.trend.includes("-") ? "down" : "up");

            card.innerHTML = `
                <div class="card-img-wrap">
                    <img src="${item.image}" alt="${item.commodity}" loading="lazy"/>
                    <span class="trend-badge ${trendClass}">${item.trend || "Stable"}</span>
                    <span class="distance-badge">📍 ${item.distance_km} km away</span>
                </div>
                <div class="card-body">
                    <div style="display:flex;justify-content:space-between;align-items:center;">
                        <div class="commodity-title">${item.commodity}</div>
                        <span style="font-size:9px;font-family:var(--font-mono);background:#f1f5f9;color:#475569;padding:2px 6px;border-radius:4px;font-weight:600">${item.category || 'Commodity'}</span>
                    </div>
                    <div class="mandi-name">🏛️ ${item.market}, ${item.district} (${item.state})</div>

                    <div class="price-breakdown-box">
                        <div class="expected-price-row">
                            <span class="ep-label">EXPECTED PRICE</span>
                            <span class="ep-value">₹${item.modal_price.toFixed(2)} <span style="font-size:12px;font-weight:600">/kg</span></span>
                        </div>
                        <div class="min-max-row">
                            <div class="min-col">
                                <span class="mm-label">MIN PRICE</span>
                                <span class="mm-val">₹${item.min_price.toFixed(2)} / kg</span>
                            </div>
                            <div class="max-col">
                                <span class="mm-label">MAX PRICE</span>
                                <span class="mm-val">₹${item.max_price.toFixed(2)} / kg</span>
                            </div>
                        </div>
                        <div class="quintal-note">Modal (Quintal): ₹${item.modal_price_quintal || (item.modal_price * 100).toFixed(0)}</div>
                    </div>
                </div>
            `;
            grid.appendChild(card);
        });
    }

    // Search and Filter Listeners
    const searchInput = document.getElementById("searchInput");
    const categorySelect = document.getElementById("categorySelect");
    const stateSelect = document.getElementById("stateSelect");
    const sortSelect = document.getElementById("sortSelect");

    function applyFilters() {
        const query = searchInput ? searchInput.value.toLowerCase().trim() : "";
        const categoryVal = categorySelect ? categorySelect.value.toLowerCase() : "all";
        const stateVal = stateSelect ? stateSelect.value.toLowerCase() : "all";
        const sortVal = sortSelect ? sortSelect.value : "distance";

        let filtered = allMarketData.filter(item => {
            const matchesQuery = item.commodity.toLowerCase().includes(query) || item.market.toLowerCase().includes(query) || (item.category && item.category.toLowerCase().includes(query));
            const matchesCategory = categoryVal === "all" || (item.category && item.category.toLowerCase().includes(categoryVal));
            const matchesState = stateVal === "all" || item.state.toLowerCase().includes(stateVal);
            return matchesQuery && matchesCategory && matchesState;
        });

        if (sortVal === "distance") {
            filtered.sort((a, b) => a.distance_km - b.distance_km);
        } else if (sortVal === "expected_high") {
            filtered.sort((a, b) => b.modal_price - a.modal_price);
        } else if (sortVal === "expected_low") {
            filtered.sort((a, b) => a.modal_price - b.modal_price);
        } else if (sortVal === "name") {
            filtered.sort((a, b) => a.commodity.localeCompare(b.commodity));
        }

        renderCards(filtered);
        const marketRecordNote = document.getElementById("marketRecordNote");
        if (marketRecordNote) marketRecordNote.textContent = `Showing ${filtered.length.toLocaleString()} of ${allMarketData.length.toLocaleString()} crop and commodity records.`;
    }

    if (searchInput) searchInput.addEventListener("input", applyFilters);
    if (categorySelect) categorySelect.addEventListener("change", applyFilters);
    if (stateSelect) stateSelect.addEventListener("change", applyFilters);
    if (sortSelect) sortSelect.addEventListener("change", applyFilters);

    // Initial GPS load
    getGPSLocation();
};
