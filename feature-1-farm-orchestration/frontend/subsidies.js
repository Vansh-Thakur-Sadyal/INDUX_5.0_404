const API_URL = "http://127.0.0.1:8001";
let allSubsidiesData = [];

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

    // Fetch Subsidies from Backend API
    async function fetchSubsidies() {
        const grid = document.getElementById("subsidiesGrid");
        grid.innerHTML = '<div class="loading-state">⚡ Loading verified government subsidy schemes...</div>';

        try {
            const res = await fetch(`${API_URL}/api/farmer-subsidies`);
            const data = await res.json();
            if (data.status === "success") {
                allSubsidiesData = data.schemes || [];
                renderSubsidies(allSubsidiesData);
            }
        } catch (err) {
            console.error("Subsidies fetch error:", err);
            grid.innerHTML = '<div class="loading-state">Error loading government subsidies. Ensure backend API is running.</div>';
        }
    }

    function renderSubsidies(items) {
        const grid = document.getElementById("subsidiesGrid");
        grid.innerHTML = "";

        if (items.length === 0) {
            grid.innerHTML = '<div class="loading-state">No government schemes found matching your search.</div>';
            return;
        }

        items.forEach(item => {
            const card = document.createElement("div");
            card.className = "subsidy-card";

            card.innerHTML = `
                <div class="sub-card-header">
                    <span class="sub-cat-badge">${item.category_tag || item.category}</span>
                    <div class="sub-title">${item.title}</div>
                    <span class="sub-percent-badge">${item.subsidy_percent}</span>
                </div>
                <div class="sub-card-body">
                    <div class="sub-benefit-box">
                        <div class="sbb-label">MAX FINANCIAL BENEFIT</div>
                        <div class="sbb-val">${item.financial_benefit}</div>
                    </div>
                    
                    <div class="sub-section-block">
                        <div class="ssb-title">PURPOSE & OBJECTIVE</div>
                        <div class="ssb-text">${item.objective}</div>
                    </div>

                    <div class="sub-section-block">
                        <div class="ssb-title">ELIGIBILITY CRITERIA</div>
                        <div class="ssb-text">${item.eligibility}</div>
                    </div>

                    <div class="sub-section-block">
                        <div class="ssb-title">REQUIRED DOCUMENTS</div>
                        <div class="ssb-text">${item.documents}</div>
                    </div>

                    <a href="${item.portal_url}" target="_blank" rel="noopener noreferrer" class="btn-apply-portal">
                        <span>Apply on ${item.portal_name}</span>
                        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5">
                            <path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"></path>
                            <polyline points="15 3 21 3 21 9"></polyline>
                            <line x1="10" y1="14" x2="21" y2="3"></line>
                        </svg>
                    </a>
                </div>
            `;
            grid.appendChild(card);
        });
    }

    // Category Pills & Search Listeners
    const subSearchInput = document.getElementById("subSearchInput");
    let activeSubCategory = "all";

    document.querySelectorAll(".btn-sub-pill").forEach(pill => {
        pill.addEventListener("click", () => {
            document.querySelectorAll(".btn-sub-pill").forEach(p => p.classList.remove("active"));
            pill.classList.add("active");
            activeSubCategory = pill.getAttribute("data-cat");
            filterSubsidies();
        });
    });

    if (subSearchInput) {
        subSearchInput.addEventListener("input", filterSubsidies);
    }

    function filterSubsidies() {
        const query = subSearchInput ? subSearchInput.value.toLowerCase().trim() : "";

        const filtered = allSubsidiesData.filter(item => {
            const matchesCat = activeSubCategory === "all" || item.category.toLowerCase().includes(activeSubCategory.toLowerCase()) || item.category_tag.toLowerCase().includes(activeSubCategory.toLowerCase());
            const matchesQuery = item.title.toLowerCase().includes(query) || item.objective.toLowerCase().includes(query) || item.category.toLowerCase().includes(query);
            return matchesCat && matchesQuery;
        });

        renderSubsidies(filtered);
    }

    fetchSubsidies();
};
