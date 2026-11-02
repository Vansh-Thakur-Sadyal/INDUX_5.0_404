/* ==========================================================
   FARMER EDUCATION HUB — JavaScript
   Handles: Curated learning links, search, topic filter
========================================================== */

const EDUCATION_RESOURCES = [
  // Soil Health
  {
    id: 1,
    title: "Soil Health Card (SHC) Portal & Testing Guide",
    category: "soil",
    categoryLabel: "🌱 Soil Health",
    desc: "Official Govt of India portal to check soil nutrient status, request testing, and get customized 12-parameter dosage recommendations (N, P, K, S, Zn, Fe, Cu, Mn, Bo, pH, EC, OC).",
    source: "Ministry of Agriculture & Farmers Welfare",
    url: "https://soilhealth.dac.gov.in",
    lang: "Hindi / English / Regional"
  },
  {
    id: 2,
    title: "ICAR Soil Testing & Nutrient Management Manual",
    category: "soil",
    categoryLabel: "🌱 Soil Health",
    desc: "Comprehensive manual by the Indian Institute of Soil Science on organic carbon improvement, composting techniques, and bio-fertilizer usage.",
    source: "ICAR - Indian Institute of Soil Science",
    url: "https://iiss.icar.gov.in",
    lang: "English / Hindi"
  },
  {
    id: 3,
    title: "National Centre for Organic and Natural Farming (NCONF)",
    category: "soil",
    categoryLabel: "🌱 Soil Health",
    desc: "Guides and recipes for Zero Budget Natural Farming (ZBNF), Jeevamrut, Beejamrut, Panchagavya, and vermicomposting preparations.",
    source: "NCONF Govt of India",
    url: "https://ncof.dacnet.nic.in",
    lang: "Hindi / English"
  },

  // Irrigation & Water
  {
    id: 4,
    title: "Pradhan Mantri Krishi Sinchayee Yojana (PMKSY) – Per Drop More Crop",
    category: "irrigation",
    categoryLabel: "💧 Irrigation",
    desc: "Guidelines, subsidy application, and micro-irrigation manuals for drip and sprinkler systems to achieve up to 55% water savings and higher yields.",
    source: "PMKSY Official Portal",
    url: "https://pmksy.gov.in",
    lang: "English / Hindi"
  },
  {
    id: 5,
    title: "Central Ground Water Board (CGWB) Aquifer & Water Management",
    category: "irrigation",
    categoryLabel: "💧 Irrigation",
    desc: "Learn about groundwater levels, rainwater harvesting structures, recharge shafts, and sustainable tube-well management techniques.",
    source: "CGWB Ministry of Jal Shakti",
    url: "http://cgwb.gov.in",
    lang: "English"
  },

  // Crop Management
  {
    id: 6,
    title: "Kisan Suvidha – Comprehensive Crop Advisories & Weather",
    category: "crop",
    categoryLabel: "🌾 Crop Management",
    desc: "All-in-one portal for weather forecasts, dealer locator, mandi prices, plant protection, and extreme weather alerts customized to your pin code.",
    source: "Govt of India Agritech Portal",
    url: "https://kisansuvidha.gov.in",
    lang: "Multi-lingual"
  },
  {
    id: 7,
    title: "ICAR Crop Specific Package of Practices (PoP)",
    category: "crop",
    categoryLabel: "🌾 Crop Management",
    desc: "Scientifically tested sowing times, seed rates, spacing, weed management, and harvest stages for cereals, pulses, oilseeds, spices, and horticulture.",
    source: "Indian Council of Agricultural Research",
    url: "https://icar.org.in",
    lang: "English / Hindi"
  },
  {
    id: 8,
    title: "TNAU Agritech Portal – Expert Crop Guides",
    category: "crop",
    categoryLabel: "🌾 Crop Management",
    desc: "Detailed visual guides on crop stages, nutrient deficiencies, weed identification, and harvesting best practices from Tamil Nadu Agricultural University.",
    source: "TNAU Agritech Portal",
    url: "https://agritech.tnau.ac.in",
    lang: "English / Tamil"
  },

  // Pest & Disease
  {
    id: 9,
    title: "Directorate of Plant Protection, Quarantine & Storage (DPPQS)",
    category: "pest",
    categoryLabel: "🛡️ Pest & Disease",
    desc: "Approved pesticide formulations, maximum residue limits (MRL), banned agro-chemicals list, and locust/fall armyworm control advisories.",
    source: "DPPQS Govt of India",
    url: "https://ppqs.gov.in",
    lang: "English / Hindi"
  },
  {
    id: 10,
    title: "National Centre for Integrated Pest Management (NCIPM)",
    category: "pest",
    categoryLabel: "🛡️ Pest & Disease",
    desc: "Bio-control strategies, pheromone trap deployment, yellow sticky card densities, and economic threshold levels (ETL) to minimize chemical sprays.",
    source: "ICAR - NCIPM",
    url: "https://ncipm.icar.gov.in",
    lang: "English / Hindi"
  },

  // Market & Finance
  {
    id: 11,
    title: "e-NAM – National Agriculture Market e-Trading",
    category: "market",
    categoryLabel: "📈 Market & Finance",
    desc: "Pan-India electronic trading portal networking existing APMC mandis to create a unified national market for agricultural commodities.",
    source: "SFAC / Ministry of Agriculture",
    url: "https://www.enam.gov.in",
    lang: "18 Indian Languages"
  },
  {
    id: 12,
    title: "Agmarknet – Daily Mandi Arrivals and Prices",
    category: "market",
    categoryLabel: "📈 Market & Finance",
    desc: "Real-time daily prices and arrivals of agricultural commodities from over 3,000 markets across all Indian states and UTs.",
    source: "Directorate of Marketing & Inspection",
    url: "https://agmarknet.gov.in",
    lang: "English / Hindi"
  },
  {
    id: 13,
    title: "NABARD Financial Literacy & Farmer Producer Organizations (FPO)",
    category: "market",
    categoryLabel: "📈 Market & Finance",
    desc: "Guides on how to form an FPO, access micro-finance, KCC low-interest credit lines (4% effective rate), and warehouse receipt financing.",
    source: "NABARD",
    url: "https://www.nabard.org",
    lang: "English / Hindi"
  },

  // Government Schemes & Loans
  {
    id: 14,
    title: "PM-KISAN Samman Nidhi Portal",
    category: "scheme",
    categoryLabel: "💰 Schemes & Loans",
    desc: "Direct benefit transfer of ₹6,000/year to farmer families. Check installment status, e-KYC verification, and beneficiary lists.",
    source: "PM-KISAN Central Govt",
    url: "https://pmkisan.gov.in",
    lang: "Multi-lingual"
  },
  {
    id: 15,
    title: "Pradhan Mantri Fasal Bima Yojana (PMFBY) – Crop Insurance",
    category: "scheme",
    categoryLabel: "💰 Schemes & Loans",
    desc: "Comprehensive crop insurance against non-preventable natural risks from pre-sowing to post-harvest. Calculate premium and claim status.",
    source: "Ministry of Agriculture",
    url: "https://pmfby.gov.in",
    lang: "Hindi / English"
  },
  {
    id: 16,
    title: "PM-KUSUM – Solar Pumps & Grid-Connected Solar Plants",
    category: "scheme",
    categoryLabel: "💰 Schemes & Loans",
    desc: "Up to 60% government subsidy for standalone solar agriculture pumps and solarization of existing grid-connected tubewells.",
    source: "Ministry of New and Renewable Energy",
    url: "https://pmkusum.mnre.gov.in",
    lang: "English / Hindi"
  },

  // Video Training
  {
    id: 17,
    title: "DD Kisan 24x7 Broadcast & Online Masterclasses",
    category: "video",
    categoryLabel: "🎬 Video Training",
    desc: "India's first 24x7 television channel dedicated to agriculture. Video series on progressive farmers, modern equipment, and university lectures.",
    source: "Prasar Bharati / DD Kisan",
    url: "https://prasarbharati.gov.in/ddkisan",
    lang: "Hindi"
  },
  {
    id: 18,
    title: "Kisan Portal SMS & Voice Advisories (mKisan)",
    category: "govt",
    categoryLabel: "🏛️ Govt Portals",
    desc: "Register your mobile number to receive timely crop-weather advisories, mandi rates, and pest outbreak alerts in your local language directly from scientists.",
    source: "Department of Agriculture and Cooperation",
    url: "https://mkisan.gov.in",
    lang: "12 Languages"
  }
];

window.onload = function () {
  const searchInput = document.getElementById("eduSearch");
  const filterChips = document.querySelectorAll(".chip");
  let activeFilter = "all";

  renderResources(EDUCATION_RESOURCES);

  // Search input handler
  searchInput.addEventListener("input", () => {
    filterAndRender();
  });

  // Category filter chips handler
  filterChips.forEach(chip => {
    chip.addEventListener("click", () => {
      filterChips.forEach(c => c.classList.remove("active"));
      chip.classList.add("active");
      activeFilter = chip.getAttribute("data-cat");
      filterAndRender();
    });
  });

  function filterAndRender() {
    const q = searchInput.value.toLowerCase().trim();
    const filtered = EDUCATION_RESOURCES.filter(r => {
      const matchCat = (activeFilter === "all") || (r.category === activeFilter);
      const matchQuery = !q || 
        r.title.toLowerCase().includes(q) || 
        r.desc.toLowerCase().includes(q) || 
        r.source.toLowerCase().includes(q) ||
        r.categoryLabel.toLowerCase().includes(q);
      return matchCat && matchQuery;
    });
    renderResources(filtered);
  }

  function renderResources(items) {
    const grid = document.getElementById("eduGrid");
    if (items.length === 0) {
      grid.innerHTML = `<div style="grid-column: 1/-1; text-align: center; padding: 30px; color: var(--text-muted); font-style: italic;">No learning resources matched your search. Try different keywords.</div>`;
      return;
    }

    grid.innerHTML = items.map(item => `
      <div class="edu-card">
        <div>
          <div class="ec-header">
            <div class="ec-title">${item.title}</div>
            <span class="ec-category-tag">${item.categoryLabel}</span>
          </div>
          <div class="ec-desc">${item.desc}</div>
        </div>
        <div class="ec-meta">
          <span class="ec-source">🏛️ ${item.source}</span>
          <a href="${item.url}" target="_blank" rel="noopener" class="ec-link">
            Open Resource <span>↗</span>
          </a>
        </div>
      </div>
    `).join("");
  }
};
