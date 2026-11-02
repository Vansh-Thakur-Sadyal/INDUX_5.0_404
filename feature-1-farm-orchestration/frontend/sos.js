(() => {
  const API_URL = "http://127.0.0.1:8001";
  const RADIUS_KM = 5;
  const MAX_AUDIO_BYTES = 5 * 1024 * 1024;
  const MAX_RECORDING_MS = 2 * 60 * 1000;

  function addSosLauncher() {
    if (!location.pathname.toLowerCase().endsWith("/hardware_share.html")
      && !document.getElementById("farmGlobalHardwareShareLink")) {
      const hardwareLink = document.createElement("a");
      hardwareLink.id = "farmGlobalHardwareShareLink";
      hardwareLink.href = "hardware_share.html";
      hardwareLink.textContent = "📡 Book Hardware";
      hardwareLink.setAttribute("aria-label", "Book shared farm hardware");
      hardwareLink.style.cssText = [
        "position:fixed",
        "right:20px",
        "bottom:80px",
        "z-index:9998",
        "display:inline-flex",
        "align-items:center",
        "justify-content:center",
        "min-height:42px",
        "padding:0 10px",
        "border-radius:26px",
        "background:#317348",
        "color:#fff",
        "font:700 12px sans-serif",
        "text-decoration:none",
        "box-shadow:0 4px 16px #0004",
      ].join(";");
      document.body.appendChild(hardwareLink);
    }

    if (location.pathname.toLowerCase().endsWith("/sos.html")) return;
    if (document.getElementById("farmGlobalSosLink")) return;

    const link = document.createElement("a");
    link.id = "farmGlobalSosLink";
    link.href = "sos.html";
    link.textContent = "🚨 SOS";
    link.setAttribute("aria-label", "Open Emergency SOS");
    link.style.cssText = [
      "position:fixed",
      "right:20px",
      "bottom:22px",
      "z-index:9998",
      "display:inline-flex",
      "align-items:center",
      "justify-content:center",
      "min-height:42px",
      "padding:0 10px",
      "border-radius:26px",
      "background:#b42318",
      "color:#fff",
      "font:700 13px sans-serif",
      "text-decoration:none",
      "box-shadow:0 4px 16px #0004",
    ].join(";");
    document.body.appendChild(link);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", addSosLauncher, { once: true });
  } else {
    addSosLauncher();
  }

  const form = document.getElementById("sosForm");
  if (!form) return;

  const locationStatus = document.getElementById("locationStatus");
  const audioPreview = document.getElementById("voicePreview");
  const recordingStatus = document.getElementById("recordingStatus");
  const submitStatus = document.getElementById("submitStatus");
  const nearbyReports = document.getElementById("nearbyReports");
  const submitButton = document.getElementById("submitSosButton");
  const consent = document.getElementById("shareConsent");
  let currentLocation = null;
  let voiceBlob = null;
  let voiceType = "";
  let previewUrl = null;
  let mediaRecorder = null;
  let mediaStream = null;
  let recordingChunks = [];
  let recordingTimeout = null;
  let hasLoadedFeed = false;
  const knownIncidentIds = new Set();
  const deviceId = getDeviceId();

  document.getElementById("syncLocationButton").addEventListener("click", syncLocation);
  document.getElementById("refreshReportsButton").addEventListener("click", refreshReports);
  document.getElementById("enableNotificationsButton").addEventListener("click", enableNotifications);
  document.getElementById("startRecordingButton").addEventListener("click", startRecording);
  document.getElementById("stopRecordingButton").addEventListener("click", stopRecording);
  document.getElementById("voiceFile").addEventListener("change", selectAudioFile);
  consent.addEventListener("change", updateSubmitState);
  form.addEventListener("submit", sendSos);

  setInterval(() => {
    if (currentLocation) refreshReports();
  }, 15000);

  window.addEventListener("beforeunload", () => {
    if (previewUrl) URL.revokeObjectURL(previewUrl);
    nearbyReports.querySelectorAll("audio").forEach((audio) => {
      if (audio.src.startsWith("blob:")) URL.revokeObjectURL(audio.src);
    });
    if (mediaStream) mediaStream.getTracks().forEach((track) => track.stop());
  });

  function getDeviceId() {
    try {
      let id = localStorage.getItem("farm_sos_device_id");
      if (!id) {
        id = crypto.randomUUID();
        localStorage.setItem("farm_sos_device_id", id);
      }
      return id;
    } catch (error) {
      console.error("Could not save an SOS device identifier.", error);
      return crypto.randomUUID();
    }
  }

  function updateSubmitState() {
    submitButton.disabled = !currentLocation || !voiceBlob || !consent.checked;
  }

  function syncLocation() {
    if (!navigator.geolocation) {
      locationStatus.textContent = "This browser does not support GPS. SOS sharing needs a location.";
      return;
    }
    locationStatus.textContent = "Getting your current location...";
    navigator.geolocation.getCurrentPosition(
      async (position) => {
        currentLocation = {
          latitude: position.coords.latitude,
          longitude: position.coords.longitude,
        };
        locationStatus.textContent =
          `Location ready: ${currentLocation.latitude.toFixed(5)}, ${currentLocation.longitude.toFixed(5)} ` +
          `(GPS accuracy about ${Math.round(position.coords.accuracy)} m). Exact coordinates are shared with nearby users.`;
        updateSubmitState();
        await refreshReports();
      },
      (error) => {
        currentLocation = null;
        const messages = {
          1: "Location permission was denied. Allow GPS access in browser settings to continue.",
          2: "Your location could not be determined. Try again where GPS reception is better.",
          3: "GPS request timed out. Please try again.",
        };
        locationStatus.textContent = messages[error.code] || "GPS is unavailable. Please try again.";
        updateSubmitState();
      },
      { enableHighAccuracy: true, timeout: 15000, maximumAge: 0 }
    );
  }

  async function startRecording() {
    const startButton = document.getElementById("startRecordingButton");
    const stopButton = document.getElementById("stopRecordingButton");
    if (!navigator.mediaDevices?.getUserMedia || typeof MediaRecorder === "undefined") {
      recordingStatus.textContent = "Recording is not supported here. Select an audio file instead.";
      return;
    }
    try {
      mediaStream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const preferredType = [
        "audio/webm;codecs=opus",
        "audio/webm",
        "audio/mp4",
        "audio/ogg;codecs=opus",
      ].find((type) => MediaRecorder.isTypeSupported(type));
      mediaRecorder = new MediaRecorder(mediaStream, preferredType ? { mimeType: preferredType } : {});
      recordingChunks = [];
      voiceBlob = null;
      mediaRecorder.addEventListener("dataavailable", (event) => {
        if (event.data.size) recordingChunks.push(event.data);
      });
      mediaRecorder.addEventListener("stop", finishRecording, { once: true });
      mediaRecorder.start();
      startButton.disabled = true;
      startButton.classList.add("recording");
      stopButton.disabled = false;
      recordingStatus.textContent = "Recording… Maximum 2 minutes.";
      recordingTimeout = setTimeout(stopRecording, MAX_RECORDING_MS);
    } catch (error) {
      console.error("Could not start SOS voice recording.", error);
      recordingStatus.textContent = "Microphone access failed. Allow microphone access or select an audio file.";
      if (mediaStream) mediaStream.getTracks().forEach((track) => track.stop());
      mediaStream = null;
    }
  }

  function stopRecording() {
    if (recordingTimeout) clearTimeout(recordingTimeout);
    recordingTimeout = null;
    if (mediaRecorder && mediaRecorder.state !== "inactive") mediaRecorder.stop();
  }

  function finishRecording() {
    const startButton = document.getElementById("startRecordingButton");
    const stopButton = document.getElementById("stopRecordingButton");
    if (mediaStream) mediaStream.getTracks().forEach((track) => track.stop());
    mediaStream = null;
    startButton.disabled = false;
    startButton.classList.remove("recording");
    stopButton.disabled = true;

    const blob = new Blob(recordingChunks, { type: mediaRecorder.mimeType || "audio/webm" });
    recordingChunks = [];
    if (!blob.size || blob.size > MAX_AUDIO_BYTES) {
      voiceBlob = null;
      recordingStatus.textContent = "Recording is empty or over 5 MB. Record a shorter note.";
      updateSubmitState();
      return;
    }
    setVoiceNote(blob, blob.type);
    recordingStatus.textContent = `Recording ready (${(blob.size / 1024 / 1024).toFixed(1)} MB).`;
  }

  function selectAudioFile(event) {
    const file = event.target.files?.[0];
    if (!file) return;
    if (!file.type.startsWith("audio/")) {
      voiceBlob = null;
      recordingStatus.textContent = "Choose an audio file.";
      updateSubmitState();
      return;
    }
    if (file.size > MAX_AUDIO_BYTES) {
      voiceBlob = null;
      recordingStatus.textContent = "Voice notes must be 5 MB or smaller.";
      updateSubmitState();
      return;
    }
    setVoiceNote(file, file.type);
    recordingStatus.textContent = `Selected: ${file.name}`;
  }

  function setVoiceNote(blob, type) {
    voiceBlob = blob;
    voiceType = type.split(";", 1)[0].toLowerCase();
    if (previewUrl) URL.revokeObjectURL(previewUrl);
    previewUrl = URL.createObjectURL(blob);
    audioPreview.src = previewUrl;
    audioPreview.hidden = false;
    updateSubmitState();
  }

  async function blobToBase64(blob) {
    return new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.addEventListener("load", () => {
        const dataUrl = String(reader.result || "");
        const comma = dataUrl.indexOf(",");
        if (comma < 0) {
          reject(new Error("Could not encode the voice note."));
          return;
        }
        resolve(dataUrl.slice(comma + 1));
      });
      reader.addEventListener("error", () => reject(new Error("Could not read the voice note.")));
      reader.readAsDataURL(blob);
    });
  }

  async function sendSos(event) {
    event.preventDefault();
    if (!currentLocation || !voiceBlob || !consent.checked) {
      submitStatus.textContent = "Sync GPS, add a voice note, and confirm the sharing notice.";
      return;
    }
    submitButton.disabled = true;
    submitStatus.textContent = "Sending your SOS report...";
    try {
      const response = await fetch(`${API_URL}/api/sos`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          incident_type: document.getElementById("incidentType").value,
          description: document.getElementById("incidentDescription").value.trim(),
          latitude: currentLocation.latitude,
          longitude: currentLocation.longitude,
          share_consent: consent.checked,
          audio_type: voiceType,
          audio_base64: await blobToBase64(voiceBlob),
        }),
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || "Could not send this SOS report.");
      submitStatus.textContent = "SOS saved on this computer's app instance. Other devices cannot receive it in this local prototype. Call 112 directly if emergency services are needed.";
      form.reset();
      audioPreview.hidden = true;
      audioPreview.removeAttribute("src");
      if (previewUrl) URL.revokeObjectURL(previewUrl);
      previewUrl = null;
      voiceBlob = null;
      voiceType = "";
      updateSubmitState();
      await refreshReports(true);
    } catch (error) {
      console.error("SOS report could not be sent.", error);
      submitStatus.textContent = error.message || "SOS service is unavailable. Call 112 directly if there is immediate danger.";
      updateSubmitState();
    }
  }

  async function refreshReports(suppressNotifications = false) {
    if (!currentLocation) {
      nearbyReports.replaceChildren(createMessage("Sync your GPS to see nearby community alerts."));
      return;
    }
    try {
      const response = await fetch(`${API_URL}/api/sos/nearby`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        cache: "no-store",
        body: JSON.stringify(currentLocation),
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || "Could not load nearby reports.");
      const reports = Array.isArray(data.reports) ? data.reports : [];
      const newReports = reports.filter((report) => !knownIncidentIds.has(report.id));
      reports.forEach((report) => knownIncidentIds.add(report.id));
      renderReports(reports);
      if (hasLoadedFeed && !suppressNotifications) notifyNewReports(newReports);
      hasLoadedFeed = true;
    } catch (error) {
      console.error("Nearby SOS reports could not be refreshed.", error);
      if (!nearbyReports.querySelector(".sos-report-card")) {
        nearbyReports.replaceChildren(createMessage("Nearby alerts could not be loaded. Check your connection and refresh."));
      }
    }
  }

  function createMessage(text) {
    const message = document.createElement("p");
    message.className = "sos-empty-state";
    message.textContent = text;
    return message;
  }

  function renderReports(reports) {
    if (!reports.length) {
      nearbyReports.querySelectorAll("audio").forEach((audio) => {
        if (audio.src.startsWith("blob:")) URL.revokeObjectURL(audio.src);
      });
      nearbyReports.replaceChildren(createMessage("No active SOS reports in your 5 km range."));
      return;
    }

    nearbyReports.querySelectorAll(".sos-empty-state").forEach((message) => message.remove());
    const activeIds = new Set(reports.map((report) => report.id));
    nearbyReports.querySelectorAll(".sos-report-card").forEach((card) => {
      if (!activeIds.has(card.dataset.incidentId)) {
        const audio = card.querySelector("audio");
        if (audio?.src.startsWith("blob:")) URL.revokeObjectURL(audio.src);
        card.remove();
      }
    });

    reports.forEach((report) => {
      const existing = Array.from(nearbyReports.querySelectorAll(".sos-report-card"))
        .find((card) => card.dataset.incidentId === report.id);
      if (existing) {
        updateReportCard(existing, report);
        return;
      }

      const card = createReportCard(report);
      nearbyReports.appendChild(card);
    });
  }

  function createReportCard(report) {
    const card = document.createElement("article");
    card.className = "sos-report-card";
    card.dataset.incidentId = report.id;

    const top = document.createElement("div");
    top.className = "sos-report-top";
    const type = document.createElement("strong");
    type.className = "sos-report-type";
    type.textContent = report.incident_type;
    const meta = document.createElement("span");
    meta.className = "sos-report-meta";
    const created = new Date(report.created_at);
    meta.textContent = `${Number.isNaN(created.getTime()) ? "" : created.toLocaleString()} · ${report.distance_km} km away`;
    top.append(type, meta);

    const description = document.createElement("p");
    description.className = "sos-report-description";
    description.textContent = report.description;

    const audio = document.createElement("audio");
    audio.className = "sos-report-audio";
    audio.controls = true;
    audio.preload = "none";
    audio.hidden = true;
    audio.setAttribute("aria-label", `Voice report for ${report.incident_type}`);
    const listenButton = document.createElement("button");
    listenButton.type = "button";
    listenButton.className = "sos-reaction-button";
    listenButton.textContent = "Load and listen to voice report";
    listenButton.addEventListener("click", async () => {
      listenButton.disabled = true;
      listenButton.textContent = "Loading voice report...";
      try {
        const response = await fetch(`${API_URL}${report.audio_url}`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(currentLocation),
        });
        if (!response.ok) {
          const error = await response.json();
          throw new Error(error.detail || "Could not load this voice report.");
        }
        const audioBlob = await response.blob();
        audio.src = URL.createObjectURL(audioBlob);
        audio.hidden = false;
        listenButton.hidden = true;
      } catch (error) {
        console.error("SOS voice report could not be loaded.", error);
        listenButton.disabled = false;
        listenButton.textContent = error.message || "Could not load voice report. Try again.";
      }
    });

    const coords = `${Number(report.latitude).toFixed(5)}, ${Number(report.longitude).toFixed(5)}`;
    const mapLink = document.createElement("a");
    mapLink.href = `https://www.google.com/maps?q=${encodeURIComponent(`${report.latitude},${report.longitude}`)}`;
    mapLink.target = "_blank";
    mapLink.rel = "noopener noreferrer";
    mapLink.textContent = "Open exact location";
    const locationText = document.createElement("div");
    locationText.className = "sos-report-location";
    locationText.append(document.createTextNode(`Exact incident coordinates: ${coords} · `), mapLink);

    const actions = document.createElement("div");
    actions.className = "sos-report-actions";
    actions.append(
      createReactionButton(report, "seen", "Seen"),
      createReactionButton(report, "can_help", "I can help safely")
    );
    const counts = document.createElement("span");
    counts.className = "sos-reaction-count";
    counts.textContent = `${report.reactions?.seen || 0} seen · ${report.reactions?.can_help || 0} can help`;
    actions.appendChild(counts);

    const caution = document.createElement("small");
    caution.className = "sos-report-meta";
    caution.textContent = "Do not enter a dangerous area. Coordinate with emergency services.";
    card.append(top, description, listenButton, audio, locationText, actions, caution);
    return card;
  }

  function updateReportCard(card, report) {
    const meta = card.querySelector(".sos-report-meta");
    const created = new Date(report.created_at);
    meta.textContent = `${Number.isNaN(created.getTime()) ? "" : created.toLocaleString()} · ${report.distance_km} km away`;
    card.querySelector(".sos-reaction-count").textContent =
      `${report.reactions?.seen || 0} seen · ${report.reactions?.can_help || 0} can help`;
  }

  function createReactionButton(report, reaction, label) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "sos-reaction-button";
    button.textContent = label;
    button.addEventListener("click", async () => {
      if (!currentLocation) return;
      button.disabled = true;
      try {
        const response = await fetch(`${API_URL}/api/sos/${encodeURIComponent(report.id)}/reaction`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            latitude: currentLocation.latitude,
            longitude: currentLocation.longitude,
            device_id: deviceId,
            reaction,
          }),
        });
        const data = await response.json();
        if (!response.ok) throw new Error(data.detail || "Could not save reaction.");
        await refreshReports(true);
      } catch (error) {
        console.error("SOS reaction could not be saved.", error);
        submitStatus.textContent = error.message || "Could not save reaction.";
      } finally {
        button.disabled = false;
      }
    });
    return button;
  }

  async function enableNotifications() {
    if (!("Notification" in window)) {
      submitStatus.textContent = "Browser notifications are not supported here. Keep this page open to see alerts.";
      return;
    }
    const permission = await Notification.requestPermission();
    submitStatus.textContent = permission === "granted"
      ? "Browser alerts enabled while this page is open."
      : "Browser notifications were not enabled. Keep this page open and refresh to see alerts.";
  }

  function notifyNewReports(reports) {
    if (!reports.length || !("Notification" in window) || Notification.permission !== "granted" || document.hasFocus()) {
      return;
    }
    reports.forEach((report) => {
      const notification = new Notification(`Nearby SOS: ${report.incident_type}`, {
        body: `${report.distance_km} km away on this app instance. Open the SOS page to listen to the voice report.`,
        tag: `farm-sos-${report.id}`,
      });
      notification.onclick = () => {
        window.focus();
        notification.close();
      };
    });
  }
})();
