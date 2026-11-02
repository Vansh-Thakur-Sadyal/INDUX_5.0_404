window.renderFarmAssistantMarkdown = function (container, source) {
  const inline = (parent, text) => {
    const pattern = /(\[[^\]]+\]\(https?:\/\/[^)\s]+\)|\*\*[^*]+\*\*|__[^_]+__|\*[^*\n]+\*|_[^_\n]+_|`[^`]+`)/g;
    let cursor = 0;
    let match;
    while ((match = pattern.exec(text)) !== null) {
      if (match.index > cursor) parent.appendChild(document.createTextNode(text.slice(cursor, match.index)));
      const token = match[0];
      const linkMatch = token.match(/^\[([^\]]+)\]\((https?:\/\/[^)\s]+)\)$/);
      if (linkMatch) {
        const link = document.createElement("a");
        link.href = linkMatch[2];
        link.textContent = linkMatch[1];
        link.target = "_blank";
        link.rel = "noopener noreferrer";
        parent.appendChild(link);
      } else {
        const code = token.startsWith("`");
        const strong = token.startsWith("**") || token.startsWith("__");
        const element = document.createElement(code ? "code" : strong ? "strong" : "em");
        element.textContent = token.slice(strong || code ? 2 : 1, -(strong || code ? 2 : 1));
        parent.appendChild(element);
      }
      cursor = pattern.lastIndex;
    }
    if (cursor < text.length) parent.appendChild(document.createTextNode(text.slice(cursor)));
  };

  const normalized = String(source).replace(/\\([*_`\[\]()])/g, "$1");
  let paragraph = [];
  let list = null;
  const flush = () => {
    if (!paragraph.length) return;
    const p = document.createElement("p");
    paragraph.forEach((line, index) => {
      if (index) p.appendChild(document.createElement("br"));
      inline(p, line);
    });
    container.appendChild(p);
    paragraph = [];
  };

  for (const line of normalized.replace(/\r\n?/g, "\n").split("\n")) {
    const heading = line.match(/^\s{0,3}(#{1,3})\s+(.+)$/);
    const item = line.match(/^\s*(?:[-*+]\s+|\d+[.)]\s+)(.*)$/);
    if (!line.trim()) {
      flush();
      list = null;
    } else if (heading) {
      flush();
      list = null;
      const h = document.createElement(`h${heading[1].length}`);
      inline(h, heading[2]);
      container.appendChild(h);
    } else if (item) {
      flush();
      const tag = /^\s*\d+[.)]\s+/.test(line) ? "ol" : "ul";
      if (!list || list.tagName.toLowerCase() !== tag) {
        list = document.createElement(tag);
        container.appendChild(list);
      }
      const li = document.createElement("li");
      inline(li, item[1]);
      list.appendChild(li);
    } else {
      list = null;
      paragraph.push(line);
    }
  }
  flush();
};

window.getFarmAssistantContext = function () {
  const context = {};
  const locationRaw = localStorage.getItem("farm_assistant_location");
  if (locationRaw) {
    try {
      const location = JSON.parse(locationRaw);
      if (Number.isFinite(location.lat) && Number.isFinite(location.lon)) {
        context.lat = location.lat;
        context.lon = location.lon;
      }
    } catch (error) {
      console.warn("Saved farm location could not be read.", error);
    }
  }

  const cropInput = document.querySelector(
    "#inpCrop, #npkCrop, #inspectCropSelect, #cropName, #cropSelect"
  );
  if (cropInput && cropInput.value) context.crop = cropInput.value;

  let farmContext = {};
  const savedState = sessionStorage.getItem("lastFarmState") || localStorage.getItem("lastFarmState");
  if (savedState) {
    try {
      const state = JSON.parse(savedState);
      farmContext = {
        soil_moisture: state.moisture,
        temperature: state.temp,
        nitrogen: state.N,
        phosphorus: state.P,
        potassium: state.K,
        soil_ph: state.ph,
        soil_ec: state.ec,
        source: state.source,
        timestamp: state.timestamp
      };
    } catch (error) {
      console.warn("Saved farm sensor context could not be read.", error);
    }
  }

  const readingInputs = {
    nitrogen: "inpN",
    phosphorus: "inpP",
    potassium: "inpK",
    soil_ph: "inpPh",
    soil_ec: "inpEc",
    soil_moisture: "inpMoisture",
    temperature: "inpTemp"
  };
  for (const [key, id] of Object.entries(readingInputs)) {
    const input = document.getElementById(id);
    if (input && input.value.trim() !== "") {
      const value = Number(input.value);
      if (Number.isFinite(value)) farmContext[key] = value;
    }
  }
  const stageInput = document.getElementById("npkStage");
  if (stageInput && stageInput.value) farmContext.crop_stage = stageInput.value;
  if (Object.keys(readingInputs).some(id => document.getElementById(id))) {
    farmContext.input_source = "currently entered dashboard/NPK form values";
  }

  const pipelineInputs = localStorage.getItem("pipeline_inputs");
  if (pipelineInputs) {
    try {
      const inputs = JSON.parse(pipelineInputs);
      for (const key of ["soil_moisture", "temperature", "humidity", "crop"]) {
        if (inputs[key] !== undefined && inputs[key] !== null) farmContext[key] = inputs[key];
      }
      if (!context.crop && inputs.crop) context.crop = inputs.crop;
    } catch (error) {
      console.warn("Saved pipeline context could not be read.", error);
    }
  }

  for (const key of Object.keys(farmContext)) {
    if (farmContext[key] === undefined || farmContext[key] === null || farmContext[key] === "") {
      delete farmContext[key];
    }
  }
  if (Object.keys(farmContext).length) context.farm_context = farmContext;
  return context;
};
