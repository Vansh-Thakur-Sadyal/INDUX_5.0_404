/* ==========================================================
   AGRI NEWS & TRENDS — JavaScript
   Handles live news, category filters, and feed-derived topics.
========================================================== */

const API_URL = "http://127.0.0.1:8001";

window.addEventListener("DOMContentLoaded", () => {
  const newsFeed = document.getElementById("newsFeed");
  const filterBtns = document.querySelectorAll(".news-filter-btn");
  const lastUpdatedBadge = document.getElementById("lastUpdatedBadge");
  const refreshBtn = document.getElementById("refreshNewsBtn");
  let allNews = [];
  let currentFilter = "all";

  filterBtns.forEach((button) => {
    button.addEventListener("click", () => {
      filterBtns.forEach((item) => item.classList.remove("active"));
      button.classList.add("active");
      currentFilter = button.getAttribute("data-filter") || "all";
      renderNews();
    });
  });

  if (refreshBtn) {
    refreshBtn.addEventListener("click", loadNews);
  }

  loadNews();

  async function loadNews() {
    if (refreshBtn) {
      refreshBtn.disabled = true;
      refreshBtn.textContent = "Refreshing...";
    }
    lastUpdatedBadge.textContent = "Updating...";

    try {
      const response = await fetch(
        `${API_URL}/api/agri-news?refresh=${Date.now()}`,
        { cache: "no-store" }
      );
      const data = await response.json();
      if (!response.ok || data.status !== "success" || !Array.isArray(data.news)) {
        throw new Error(data.message || "The live news feed could not be loaded.");
      }

      allNews = data.news;
      const fetchedAt = new Date(data.fetched_at);
      lastUpdatedBadge.textContent = Number.isNaN(fetchedAt.getTime())
        ? "Just refreshed"
        : fetchedAt.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
      lastUpdatedBadge.title = `Source: ${data.source || "Live news feed"}`;
      renderNews();
      renderStats();
      renderTopics();
    } catch (error) {
      lastUpdatedBadge.textContent = allNews.length ? "Refresh failed" : "Unavailable";
      lastUpdatedBadge.title = error.message;
      if (!allNews.length) {
        newsFeed.replaceChildren();
        const message = document.createElement("p");
        message.className = "news-feed-message";
        message.textContent = "Live agricultural news is unavailable right now. Check your connection and try refreshing.";
        newsFeed.appendChild(message);
      }
    } finally {
      if (refreshBtn) {
        refreshBtn.disabled = false;
        refreshBtn.textContent = "Refresh News";
      }
    }
  }

  function renderNews() {
    const filtered = currentFilter === "all"
      ? allNews
      : allNews.filter((item) => item.category === currentFilter);
    newsFeed.replaceChildren();

    if (!filtered.length) {
      const message = document.createElement("p");
      message.className = "news-feed-message";
      message.textContent = "No recent headlines found in this category.";
      newsFeed.appendChild(message);
      return;
    }

    filtered.forEach((item) => {
      const card = document.createElement("article");
      card.className = "news-card";

      const top = document.createElement("div");
      top.className = "nc-top";
      const tag = document.createElement("span");
      const category = ["policy", "market", "tech", "weather", "export"].includes(item.category)
        ? item.category
        : "other";
      tag.className = `nc-tag ${category}`;
      tag.textContent = item.tag || "Agriculture";
      const date = document.createElement("span");
      date.className = "nc-date";
      date.textContent = item.date || "";
      top.append(tag, date);

      const title = document.createElement("h2");
      title.className = "nc-title";
      title.textContent = item.title || "Untitled article";

      const snippet = document.createElement("p");
      snippet.className = "nc-snippet";
      snippet.textContent = item.snippet || "";

      const bottom = document.createElement("div");
      bottom.className = "nc-bottom";
      const source = document.createElement("span");
      source.className = "nc-source";
      source.textContent = `Source: ${item.source || "News publisher"}`;
      bottom.appendChild(source);

      try {
        const articleUrl = new URL(item.url);
        if (articleUrl.protocol === "https:" || articleUrl.protocol === "http:") {
          const link = document.createElement("a");
          link.href = articleUrl.href;
          link.target = "_blank";
          link.rel = "noopener noreferrer";
          link.className = "nc-read-more";
          link.textContent = "Read Full Article →";
          bottom.appendChild(link);
        }
      } catch {
        // An invalid source URL is omitted rather than rendered as a link.
      }

      card.append(top, title, snippet, bottom);
      newsFeed.appendChild(card);
    });
  }

  function renderStats() {
    const sources = new Set(allNews.map((item) => item.source).filter(Boolean));
    const newest = allNews
      .map((item) => new Date(item.published_at))
      .filter((date) => !Number.isNaN(date.getTime()))
      .sort((left, right) => right - left)[0];
    document.getElementById("statNewsCount").textContent = String(allNews.length);
    document.getElementById("statSourceCount").textContent = String(sources.size);
    document.getElementById("statLatestDate").textContent = newest
      ? newest.toLocaleDateString([], { day: "numeric", month: "short" })
      : "—";
    document.getElementById("statFeedSource").textContent = "Google News RSS";
  }

  function renderTopics() {
    const topics = document.getElementById("trendingList");
    const categoryLabels = {
      policy: "Policy & MSP",
      market: "Markets & Crops",
      tech: "AgriTech",
      weather: "Weather & Crops",
      export: "Export & Trade",
      other: "Other agriculture",
    };
    const counts = allNews.reduce((result, item) => {
      const category = categoryLabels[item.category] ? item.category : "other";
      result[category] = (result[category] || 0) + 1;
      return result;
    }, {});
    topics.replaceChildren();

    Object.entries(counts)
      .sort((left, right) => right[1] - left[1])
      .forEach(([category, count]) => {
        const row = document.createElement("div");
        row.className = "trend-item";
        const label = document.createElement("span");
        label.className = "trend-topic";
        label.textContent = categoryLabels[category];
        const volume = document.createElement("span");
        volume.className = "trend-volume";
        volume.textContent = `${count} ${count === 1 ? "story" : "stories"}`;
        row.append(label, volume);
        topics.appendChild(row);
      });

    if (!allNews.length) {
      topics.textContent = "Topics will appear when live headlines load.";
    }
  }
});
