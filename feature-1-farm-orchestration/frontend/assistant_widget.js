(function () {
  function mountAssistant() {
    if (document.getElementById("chatbotPopup")) return;

    const style = document.createElement("style");
    style.textContent = `
      #farmAssistantLauncher{position:fixed;right:24px;bottom:24px;z-index:10000;border:0;border-radius:24px;padding:12px 18px;background:#1a3a1a;color:#fff;font:600 14px sans-serif;cursor:pointer;box-shadow:0 4px 16px #0003}
      #farmAssistantPanel{position:fixed;right:24px;bottom:82px;z-index:10000;width:min(360px,calc(100vw - 32px));height:430px;background:#fff;border:1px solid #d8ddd4;border-radius:14px;box-shadow:0 8px 30px #0003;display:flex;flex-direction:column;font:14px sans-serif;color:#172117}
      #farmAssistantPanel[hidden]{display:none}
      #farmAssistantHeader{padding:14px;background:#1a3a1a;color:#fff;border-radius:13px 13px 0 0;display:flex;justify-content:space-between}
      #farmAssistantClose,#farmAssistantSend,#farmAssistantLocation{border:0;cursor:pointer;border-radius:6px;padding:8px}
      #farmAssistantMessages{padding:12px;flex:1;overflow:auto;display:flex;flex-direction:column;gap:10px}
      .farm-assistant-message{padding:10px 12px;border-radius:12px;max-width:90%;line-height:1.5;overflow-wrap:anywhere}
      .farm-assistant-message p{margin:0 0 8px}.farm-assistant-message p:last-child{margin-bottom:0}
      .farm-assistant-message ul,.farm-assistant-message ol{margin:4px 0 8px;padding-left:22px}
      .farm-assistant-message li{margin:2px 0}.farm-assistant-message h1,.farm-assistant-message h2,.farm-assistant-message h3{font-size:1em;margin:8px 0 4px}
      .farm-assistant-bot{background:#f0f4ee;align-self:flex-start}.farm-assistant-user{background:#dcefd9;align-self:flex-end}
      #farmAssistantForm{padding:10px;border-top:1px solid #e1e6df;display:flex;gap:6px}
      #farmAssistantInput{min-width:0;flex:1;border:1px solid #ccd5c9;border-radius:8px;padding:9px}
      #farmAssistantLocation{background:#eef3eb;color:#23452a}#farmAssistantSend{background:#2d6a2d;color:white}
    `;
    document.head.appendChild(style);

    const launcher = document.createElement("button");
    launcher.id = "farmAssistantLauncher";
    launcher.type = "button";
    launcher.textContent = "Ask Farm Assistant";

    const panel = document.createElement("section");
    panel.id = "farmAssistantPanel";
    panel.hidden = true;
    panel.innerHTML = `
      <header id="farmAssistantHeader"><strong>Farm Assistant</strong><button id="farmAssistantClose" type="button" aria-label="Close">Close</button></header>
      <div id="farmAssistantMessages" aria-live="polite"></div>
      <form id="farmAssistantForm"><input id="farmAssistantInput" aria-label="Message" placeholder="Ask about any farm feature…" autocomplete="off"/><button id="farmAssistantLocation" type="button" title="Share current location">GPS</button><button id="farmAssistantSend" type="submit">Send</button></form>
    `;
    document.body.append(launcher, panel);

    const messages = panel.querySelector("#farmAssistantMessages");
    const input = panel.querySelector("#farmAssistantInput");

    function renderInlineMarkdown(parent, source) {
      const pattern = /(\[[^\]]+\]\(https?:\/\/[^)\s]+\)|\*\*[^*]+\*\*|__[^_]+__|\*[^*\n]+\*|_[^_\n]+_|`[^`]+`)/g;
      let cursor = 0;
      let match;
      while ((match = pattern.exec(source)) !== null) {
        if (match.index > cursor) parent.appendChild(document.createTextNode(source.slice(cursor, match.index)));
        const token = match[0];
        const linkMatch = token.match(/^\[([^\]]+)\]\((https?:\/\/[^)\s]+)\)$/);
        if (linkMatch) {
          const link = document.createElement("a");
          link.href = linkMatch[2];
          link.textContent = linkMatch[1];
          link.target = "_blank";
          link.rel = "noopener noreferrer";
          parent.appendChild(link);
          cursor = pattern.lastIndex;
          continue;
        }
        const code = token.startsWith("`");
        const strong = token.startsWith("**") || token.startsWith("__");
        const wrapper = document.createElement(code ? "code" : strong ? "strong" : "em");
        wrapper.textContent = token.slice(strong || code ? 2 : 1, -(strong || code ? 2 : 1));
        parent.appendChild(wrapper);
        cursor = pattern.lastIndex;
      }
      if (cursor < source.length) parent.appendChild(document.createTextNode(source.slice(cursor)));
    }

    function renderMarkdown(container, source) {
      const lines = String(source).replace(/\r\n?/g, "\n").split("\n");
      let paragraph = [];
      let list = null;
      const flushParagraph = () => {
        if (!paragraph.length) return;
        const p = document.createElement("p");
        paragraph.forEach((line, index) => {
          if (index) p.appendChild(document.createElement("br"));
          renderInlineMarkdown(p, line);
        });
        container.appendChild(p);
        paragraph = [];
      };
      const closeList = () => {
        list = null;
      };

      for (const line of lines) {
        const heading = line.match(/^\s{0,3}(#{1,3})\s+(.+)$/);
        const item = line.match(/^\s*(?:[-*+]\s+|\d+[.)]\s+)(.*)$/);
        if (!line.trim()) {
          flushParagraph();
          closeList();
          continue;
        }
        if (heading) {
          flushParagraph();
          closeList();
          const h = document.createElement(`h${heading[1].length}`);
          renderInlineMarkdown(h, heading[2]);
          container.appendChild(h);
          continue;
        }
        if (item) {
          flushParagraph();
          const ordered = /^\s*\d+[.)]\s+/.test(line);
          const tag = ordered ? "ol" : "ul";
          if (!list || list.tagName.toLowerCase() !== tag) {
            closeList();
            list = document.createElement(tag);
            container.appendChild(list);
          }
          const li = document.createElement("li");
          renderInlineMarkdown(li, item[1]);
          list.appendChild(li);
          continue;
        }
        closeList();
        paragraph.push(line);
      }
      flushParagraph();
    }

    function appendMessage(sender, text, actionLink, actionText) {
      const bubble = document.createElement("div");
      bubble.className = `farm-assistant-message farm-assistant-${sender}`;
      renderMarkdown(bubble, text);
      if (actionLink && actionText) {
        const link = document.createElement("a");
        link.href = actionLink;
        link.textContent = actionText;
        link.style.cssText = "display:block;margin-top:8px;color:#236b2a;font-weight:600";
        bubble.appendChild(link);
      }
      messages.appendChild(bubble);
      messages.scrollTop = messages.scrollHeight;
    }

    appendMessage("bot", "Ask about mandi prices, crops, soil, weather, or any of the farm tools.");
    launcher.addEventListener("click", () => {
      panel.hidden = false;
      input.focus();
    });
    panel.querySelector("#farmAssistantClose").addEventListener("click", () => {
      panel.hidden = true;
    });
    panel.querySelector("#farmAssistantLocation").addEventListener("click", () => {
      if (!navigator.geolocation) {
        appendMessage("bot", "Location access is not available in this browser. You can type your town or district instead.");
        return;
      }
      navigator.geolocation.getCurrentPosition(
        (position) => {
          localStorage.setItem("farm_assistant_location", JSON.stringify({
            lat: position.coords.latitude,
            lon: position.coords.longitude
          }));
          appendMessage("bot", "Location saved in this browser for nearby mandi questions.");
        },
        () => appendMessage("bot", "Could not get your location. You can type your town or district instead."),
        { timeout: 8000 }
      );
    });

    panel.querySelector("#farmAssistantForm").addEventListener("submit", async (event) => {
      event.preventDefault();
      const message = input.value.trim();
      if (!message) return;
      appendMessage("user", message);
      input.value = "";
      try {
        const response = await fetch("http://127.0.0.1:8001/chat", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ message, ...window.getFarmAssistantContext() })
        });
        if (!response.ok) throw new Error(`Assistant returned HTTP ${response.status}`);
        const data = await response.json();
        appendMessage("bot", data.reply || "The assistant returned an empty answer.", data.action_link, data.action_text);
      } catch (error) {
        console.error("Farm assistant request failed.", error);
        appendMessage("bot", "The assistant is unavailable right now. Please try again later.");
      }
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", mountAssistant, { once: true });
  } else {
    mountAssistant();
  }
})();
