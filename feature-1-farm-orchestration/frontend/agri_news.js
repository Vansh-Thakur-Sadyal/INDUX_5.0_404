/* ==========================================================
   AGRI NEWS & TRENDS — JavaScript
   Handles: Live news feed, category filter, trending topics
========================================================== */

const API_URL = "http://127.0.0.1:8001";

const FALLBACK_NEWS = [
  {
    id: 1,
    category: "policy",
    tag: "MSP & Policy",
    title: "Cabinet Approves MSP Hike for Rabi Crops 2025–26 Season",
    snippet: "The Union Cabinet has approved higher Minimum Support Prices (MSP) for all mandated Rabi crops. Wheat MSP hiked by ₹150/quintal to ₹2,425/quintal, and Mustard MSP increased by ₹300/quintal to ₹5,950/quintal to ensure fair remuneration to growers.",
    date: "Aug 2026",
    source: "PIB / Ministry of Agriculture",
    url: "https://pib.gov.in"
  },
  {
    id: 2,
    category: "tech",
    tag: "AgriTech & AI",
    title: "7-in-1 Bluetooth Soil Probes & Drone Spraying Subsidies Gain Rapid Adoption",
    snippet: "Under the SMAM scheme, Kisan Drone subsidies of up to 50% (₹5 lakh) and precision IoT sensor adoption are helping farmers optimize fertilizer and pesticide use by over 30%, cutting cultivation costs substantially across Punjab, Haryana, and Maharashtra.",
    date: "Aug 2026",
    source: "AgriTech India",
    url: "https://agricoop.nic.in"
  },
  {
    id: 3,
    category: "market",
    tag: "Market Trends",
    title: "Tomato & Onion Mandi Prices Stabilize as Fresh Kharif Harvests Reach APMCs",
    snippet: "Wholesale Mandi arrivals of tomatoes and onions have improved across Lasalgaon, Nashik, and Kolar markets, bringing retail volatility under control. Government buffer stock release through NCCF and NAFED continues to protect consumer and producer interests.",
    date: "Aug 2026",
    source: "Agmarknet Daily Feed",
    url: "https://agmarknet.gov.in"
  },
  {
    id: 4,
    category: "weather",
    tag: "Weather & Crops",
    title: "IMD Predicts Favorable Monsoon Rainfall Across Central and Northern Agri Belts",
    snippet: "The India Meteorological Department reports cumulative monsoon rainfall at 104% of the Long Period Average (LPA). Water reservoir storage levels in major agricultural dams stand at 78% capacity, supporting robust Rabi sowing preparations.",
    date: "Aug 2026",
    source: "India Meteorological Department (IMD)",
    url: "https://mausam.imd.gov.in"
  },
  {
    id: 5,
    category: "export",
    tag: "Export & Trade",
    title: "India Eases Non-Basmati White Rice & Onion Export Curbs as Domestic Stocks Surge",
    snippet: "With record buffer stocks in FCI godowns and promising Kharif crop forecasts, export duties on parboiled rice and minimum export prices on onions have been recalibrated to help farmers capitalize on remunerative international grain rates.",
    date: "Aug 2026",
    source: "Directorate General of Foreign Trade (DGFT)",
    url: "https://dgft.gov.in"
  },
  {
    id: 6,
    category: "policy",
    tag: "Subsidies & Solar",
    title: "PM-KUSUM Component-B Achieves Record 5 Lakh Standalone Solar Pump Installations",
    snippet: "Over 500,000 farmers have transitioned from diesel irrigation engines to 3HP/5HP solar agricultural pumps with 60% combined Central and State financial support, saving an estimated ₹45,000 annually per farmer in fuel expenditures.",
    date: "Aug 2026",
    source: "Ministry of New and Renewable Energy",
    url: "https://pmkusum.mnre.gov.in"
  }
];

const TRENDING_TOPICS = [
  { topic: "#WheatMSP2026", volume: "+28% searches" },
  { topic: "#SolarPumpSubsidy", volume: "+45% interest" },
  { topic: "#DripFertigation", volume: "+18% adoption" },
  { topic: "#NPKSensorIoT", volume: "+62% trending" },
  { topic: "#eNAMTenders", volume: "+15% volume" },
  { topic: "#PMFBYEnrollment", volume: "+34% active" }
];

window.onload = function () {
  const newsFeed = document.getElementById("newsFeed");
  const filterBtns = document.querySelectorAll(".news-filter-btn");
  const lastUpdatedBadge = document.getElementById("lastUpdatedBadge");
  const refreshBtn = document.getElementById("refreshNewsBtn");
  let currentFilter = "all";

  // Update timestamp
  const now = new Date();
  lastUpdatedBadge.textContent = `${now.getHours().toString().padStart(2,'0')}:${now.getMinutes().toString().padStart(2,'0')} Today`;

  // Render trending sidebar
  renderTrending();

  // Load news from API or fallback
  loadNews();

  // Filter button click handlers
  filterBtns.forEach(btn => {
    btn.addEventListener("click", () => {
      filterBtns.forEach(b => b.classList.remove("active"));
      btn.classList.add("active");
      currentFilter = btn.getAttribute("data-filter");
      renderNews(currentFilter);
    });
  });

  // Refresh handler
  if (refreshBtn) {
    refreshBtn.addEventListener("click", () => {
      lastUpdatedBadge.textContent = "Updating...";
      setTimeout(() => {
        const d = new Date();
        lastUpdatedBadge.textContent = `${d.getHours().toString().padStart(2,'0')}:${d.getMinutes().toString().padStart(2,'0')} Today`;
        loadNews();
      }, 500);
    });
  }

  async function loadNews() {
    try {
      const res = await fetch(`${API_URL}/api/agri-news`);
      const data = await res.json();
      if (data.status === "success" && data.news && data.news.length > 0) {
        window.allNews = data.news;
      } else {
        window.allNews = FALLBACK_NEWS;
      }
    } catch {
      window.allNews = FALLBACK_NEWS;
    }
    renderNews(currentFilter);
  }

  function renderNews(filter) {
    const list = window.allNews || FALLBACK_NEWS;
    const filtered = filter === "all" ? list : list.filter(item => item.category === filter);

    if (filtered.length === 0) {
      newsFeed.innerHTML = `<div style="text-align: center; padding: 30px; color: var(--text-muted); font-style: italic;">No news found in this category.</div>`;
      return;
    }

    newsFeed.innerHTML = filtered.map(item => `
      <div class="news-card">
        <div class="nc-top">
          <span class="nc-tag ${item.category}">${item.tag || item.category}</span>
          <span class="nc-date">${item.date}</span>
        </div>
        <div class="nc-title">${item.title}</div>
        <div class="nc-snippet">${item.snippet}</div>
        <div class="nc-bottom">
          <span class="nc-source">📰 ${item.source}</span>
          <a href="${item.url}" target="_blank" rel="noopener" class="nc-read-more">
            Read Full Article <span>→</span>
          </a>
        </div>
      </div>
    `).join("");
  }

  function renderTrending() {
    const trendingList = document.getElementById("trendingList");
    if (!trendingList) return;
    trendingList.innerHTML = TRENDING_TOPICS.map(t => `
      <div class="trend-item">
        <span class="trend-topic">${t.topic}</span>
        <span class="trend-volume">${t.volume}</span>
      </div>
    `).join("");
  }
};
