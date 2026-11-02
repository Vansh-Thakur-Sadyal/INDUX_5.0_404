(() => {
  const API_URL = "http://127.0.0.1:8001";
  const BOOKING_KEY = "farm_hardware_share_booking";
  const USER_KEY = "farm_hardware_share_user_id";

  const unitList = document.getElementById("unitList");
  if (!unitList) return;

  const unitStatus = document.getElementById("unitStatus");
  const unitSelect = document.getElementById("hardwareUnitSelect");
  const locationStatus = document.getElementById("locationStatus");
  const latitudeInput = document.getElementById("userLatitude");
  const longitudeInput = document.getElementById("userLongitude");
  const bookingPanel = document.getElementById("bookingPanel");
  const joinButton = document.getElementById("joinQueueButton");
  let units = [];
  let booking = loadBooking();
  const userId = getUserId();

  document.getElementById("refreshUnitsButton").addEventListener("click", loadUnits);
  document.getElementById("syncLocationButton").addEventListener("click", syncLocation);
  document.getElementById("hardwareBookingForm").addEventListener("submit", joinQueue);

  renderBooking();
  loadUnits();
  window.setInterval(() => {
    updateCountdown();
    if (booking && booking.kind === "queue") refreshQueueStatus();
  }, 15000);
  window.setInterval(updateCountdown, 1000);

  function getUserId() {
    try {
      let id = localStorage.getItem(USER_KEY);
      if (!id) {
        id = makeId();
        localStorage.setItem(USER_KEY, id);
      }
      return id;
    } catch (error) {
      console.error("Could not save the prototype hardware-share identity.", error);
      return makeId();
    }
  }

  function makeId() {
    if (window.crypto && typeof window.crypto.randomUUID === "function") {
      return window.crypto.randomUUID();
    }
    return `device-${Date.now()}-${Math.random().toString(36).slice(2)}`;
  }

  function optionalCoordinate(input) {
    if (!input.value.trim()) return null;
    const value = Number(input.value);
    return Number.isFinite(value) ? value : null;
  }

  function loadBooking() {
    try {
      const saved = localStorage.getItem(BOOKING_KEY);
      return saved ? JSON.parse(saved) : null;
    } catch (error) {
      console.error("Could not restore the hardware-share booking.", error);
      return null;
    }
  }

  function saveBooking() {
    try {
      if (booking) localStorage.setItem(BOOKING_KEY, JSON.stringify(booking));
      else localStorage.removeItem(BOOKING_KEY);
    } catch (error) {
      console.error("Could not persist the hardware-share booking in this browser.", error);
      showMessage("Booking is active, but this browser could not save it for page reloads.", true);
    }
  }

  async function apiRequest(path, options = {}) {
    let response;
    try {
      response = await fetch(`${API_URL}${path}`, {
        ...options,
        headers: {
          "X-User-Id": userId,
          ...(options.body ? { "Content-Type": "application/json" } : {}),
          ...options.headers,
        },
      });
    } catch (error) {
      throw new Error("Cannot reach the app API. Make sure the local backend is running.");
    }

    let payload;
    try {
      payload = await response.json();
    } catch (error) {
      throw new Error(`The API returned an unreadable response (${response.status}).`);
    }
    if (!response.ok) {
      const detail = payload.detail;
      const requestError = new Error(
        typeof detail === "string"
          ? detail
          : detail && detail.message
            ? detail.message
            : `Request failed (${response.status}).`,
      );
      requestError.status = response.status;
      throw requestError;
    }
    return payload;
  }

  async function loadUnits() {
    unitStatus.textContent = "Loading shared hardware units…";
    try {
      const result = await apiRequest("/api/hardware-share/units");
      units = result.units || [];
      renderUnits();
      unitStatus.textContent = units.length
        ? `Loaded ${units.length} unit${units.length === 1 ? "" : "s"}. ${result.storage || ""}`
        : "No shared hardware units are registered.";
      if (booking && !units.some((unit) => unit.id === booking.hardware_id)) {
        booking = null;
        saveBooking();
        renderBooking();
      }
    } catch (error) {
      unitStatus.textContent = error.message;
      unitStatus.classList.add("hardware-message-error");
    }
  }

  function renderUnits() {
    const previousSelection = unitSelect.value;
    unitSelect.replaceChildren(new Option("Choose a hardware unit", ""));
    unitList.replaceChildren();

    units.forEach((unit) => {
      const option = new Option(`${unit.unit_code} — ${unit.status}`, unit.id);
      unitSelect.add(option);

      const card = document.createElement("article");
      card.className = "hardware-unit-card";
      if (unit.id === unitSelect.value) card.classList.add("selected");

      const heading = document.createElement("div");
      heading.className = "hardware-unit-title";
      const code = document.createElement("span");
      code.textContent = unit.unit_code;
      const status = document.createElement("span");
      status.className = `hardware-unit-status ${unit.status.toLowerCase()}`;
      status.textContent = unit.status.replace("_", " ");
      heading.append(code, status);

      const details = document.createElement("p");
      details.className = "hardware-unit-meta";
      details.textContent = unit.is_sample
        ? `Demo unit · Hub ${Number(unit.hub_latitude).toFixed(4)}, ${Number(unit.hub_longitude).toFixed(4)}`
        : `Hub ${Number(unit.hub_latitude).toFixed(4)}, ${Number(unit.hub_longitude).toFixed(4)}`;

      const chooseButton = document.createElement("button");
      chooseButton.type = "button";
      chooseButton.className = "hardware-button secondary";
      chooseButton.textContent = "Select unit";
      chooseButton.disabled = unit.status === "MAINTENANCE";
      chooseButton.addEventListener("click", () => {
        unitSelect.value = unit.id;
        renderUnits();
      });
      card.append(heading, details, chooseButton);
      if (unit.id === previousSelection) card.classList.add("selected");
      unitList.append(card);
    });

    if (previousSelection && units.some((unit) => unit.id === previousSelection)) {
      unitSelect.value = previousSelection;
    }
    if (!booking) joinButton.disabled = units.length === 0;
  }

  function syncLocation() {
    if (!navigator.geolocation) {
      locationStatus.textContent = "GPS is unavailable in this browser. Enter coordinates manually.";
      return;
    }
    locationStatus.textContent = "Requesting your current GPS location…";
    navigator.geolocation.getCurrentPosition(
      (position) => {
        latitudeInput.value = position.coords.latitude.toFixed(6);
        longitudeInput.value = position.coords.longitude.toFixed(6);
        locationStatus.textContent = `GPS synced: ${Number(latitudeInput.value).toFixed(4)}, ${Number(longitudeInput.value).toFixed(4)}.`;
      },
      (error) => {
        locationStatus.textContent =
          error.code === error.PERMISSION_DENIED
            ? "Location permission was denied. Allow location access or enter coordinates manually."
            : "Could not read GPS location. Try again or enter coordinates manually.";
      },
      { enableHighAccuracy: true, timeout: 12000, maximumAge: 60000 },
    );
  }

  async function joinQueue(event) {
    event.preventDefault();
    if (booking) {
      showMessage("You already have an active queue booking or session. Finish or leave it first.");
      return;
    }
    const hardwareId = unitSelect.value;
    if (!hardwareId) {
      showMessage("Select a hardware unit before joining the queue.");
      return;
    }

    const buttonText = joinButton.textContent;
    joinButton.disabled = true;
    joinButton.textContent = "Joining queue…";
    try {
      const result = await apiRequest("/api/hardware-share/queue/join", {
        method: "POST",
        body: JSON.stringify({
          hardware_id: hardwareId,
          user_lat: optionalCoordinate(latitudeInput),
          user_lng: optionalCoordinate(longitudeInput),
        }),
      });
      booking = { kind: "queue", ...result.queue };
      saveBooking();
      renderBooking();
      showMessage(`Queue joined at position ${result.queue.queue_position}. Location distance is guidance only and is not checked.`);
      await loadUnits();
    } catch (error) {
      showMessage(error.message, true);
      joinButton.disabled = false;
    } finally {
      joinButton.textContent = buttonText;
    }
  }

  async function leaveQueue() {
    if (!booking || booking.kind !== "queue") return;
    const leaveButton = bookingPanel.querySelector("[data-action='leave']");
    if (leaveButton) leaveButton.disabled = true;
    try {
      await apiRequest("/api/hardware-share/queue/leave", {
        method: "POST",
        body: JSON.stringify({ queue_id: booking.id }),
      });
      booking = null;
      saveBooking();
      renderBooking();
      showMessage("You left the hardware queue.");
      await loadUnits();
    } catch (error) {
      if (error.status === 404) {
        booking = null;
        saveBooking();
        renderBooking();
        showMessage("This queue booking is no longer active. Your saved booking was cleared.", true);
      } else {
        showMessage(error.message, true);
      }
      if (leaveButton) leaveButton.disabled = false;
    }
  }

  async function refreshQueueStatus() {
    if (!booking || booking.kind !== "queue") return;
    try {
      const result = await apiRequest(
        `/api/hardware-share/queue/${encodeURIComponent(booking.id)}`,
      );
      if (
        result.queue.status !== booking.status
        || result.queue.queue_position !== booking.queue_position
      ) {
        booking = { kind: "queue", ...result.queue };
        saveBooking();
        renderBooking();
        if (booking.status === "ALLOCATED") {
          showMessage("Your turn is ready. Start your two-hour session when you are at the hub.");
        }
      }
    } catch (error) {
      if (error.status === 404) {
        booking = null;
        saveBooking();
        renderBooking();
        showMessage("This queue booking is no longer active. Your saved booking was cleared.", true);
      } else {
        showMessage(error.message, true);
      }
    }
  }

  async function startSession() {
    const startButton = bookingPanel.querySelector("[data-action='start']");
    if (startButton) startButton.disabled = true;
    const selectedUnit = units.find((unit) => unit.id === booking.hardware_id);
    const latitude = optionalCoordinate(latitudeInput);
    const longitude = optionalCoordinate(longitudeInput);
    try {
      const result = await apiRequest("/api/hardware-share/session/start", {
        method: "POST",
        body: JSON.stringify({
          hardware_id: booking.hardware_id,
          start_lat: latitude,
          start_lng: longitude,
        }),
      });
      booking = { kind: "session", ...result.session };
      saveBooking();
      renderBooking();
      showMessage(`Session started${selectedUnit ? ` for ${selectedUnit.unit_code}` : ""}. Return the unit by the displayed expiry time.`);
      await loadUnits();
    } catch (error) {
      showMessage(error.message, true);
      if (startButton) startButton.disabled = false;
    }
  }

  async function returnSession() {
    if (!booking || booking.kind !== "session") return;
    const returnButton = bookingPanel.querySelector("[data-action='return']");
    if (returnButton) returnButton.disabled = true;

    const latitude = optionalCoordinate(latitudeInput);
    const longitude = optionalCoordinate(longitudeInput);

    try {
      const result = await apiRequest("/api/hardware-share/session/return", {
        method: "POST",
        body: JSON.stringify({
          session_id: booking.id,
          session_token: booking.session_token,
          end_lat: latitude,
          end_lng: longitude,
        }),
      });
      const sessionResult = result.session;
      booking = null;
      saveBooking();
      renderBooking();
      showMessage(
        sessionResult.status === "OVERDUE"
          ? `Hardware returned late. Your access is suspended until ${new Date(sessionResult.blacklisted_until).toLocaleString()}.`
          : "Hardware returned successfully. The next queued farmer has been allocated.",
        sessionResult.status === "OVERDUE",
      );
      await loadUnits();
    } catch (error) {
      showMessage(error.message, true);
      if (returnButton) returnButton.disabled = false;
    }
  }

  function renderBooking() {
    bookingPanel.replaceChildren();
    joinButton.disabled = Boolean(booking) || units.length === 0;

    if (!booking) {
      const empty = document.createElement("p");
      empty.className = "hardware-empty-state";
      empty.textContent = "Your queue position or active session will appear here.";
      bookingPanel.append(empty);
      return;
    }

    const card = document.createElement("div");
    card.className = "hardware-booking-card";
    const heading = document.createElement("h3");
    const detail = document.createElement("p");
    const meta = document.createElement("p");
    const actions = document.createElement("div");
    actions.className = "hardware-booking-actions";

    if (booking.kind === "session") {
      heading.textContent = "Hardware session in progress";
      detail.textContent = `Return by ${new Date(booking.expires_at).toLocaleString()}. Suggested hub distance is 100 m, but it is not checked.`;
      meta.id = "sessionCountdown";
      meta.setAttribute("role", "timer");
      const button = makeButton("Return hardware", "primary", "return", returnSession);
      actions.append(button);
    } else {
      const unit = units.find((item) => item.id === booking.hardware_id);
      heading.textContent = booking.status === "ALLOCATED" ? "You are allocated" : "You are in the queue";
      detail.textContent = `${unit ? unit.unit_code : "Selected unit"} · position ${booking.queue_position} · ${booking.status.toLowerCase()}.`;
      meta.textContent = booking.status === "ALLOCATED"
        ? "You are next. Start your two-hour session when you are ready at the hub."
        : "Wait for the current booking to finish. Your position may change as the queue moves.";
      if (booking.status === "ALLOCATED") {
        actions.append(makeButton("Start 2-hour session", "primary", "start", startSession));
      }
      actions.append(makeButton("Leave queue", "secondary", "leave", leaveQueue));
    }

    card.append(heading, detail, meta, actions);
    bookingPanel.append(card);
    updateCountdown();
  }

  function makeButton(text, style, action, callback) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = `hardware-button ${style}`;
    button.dataset.action = action;
    button.textContent = text;
    button.addEventListener("click", callback);
    return button;
  }

  function updateCountdown() {
    if (!booking || booking.kind !== "session") return;
    const countdown = document.getElementById("sessionCountdown");
    if (!countdown) return;
    const remaining = new Date(booking.expires_at).getTime() - Date.now();
    if (remaining <= 0) {
      countdown.textContent = "Time is up. Return the unit to its hub now; overdue sessions trigger a 7-day access suspension.";
      countdown.classList.add("hardware-message-error");
      return;
    }
    const seconds = Math.floor(remaining / 1000);
    const hours = Math.floor(seconds / 3600);
    const minutes = Math.floor((seconds % 3600) / 60);
    const remainder = seconds % 60;
    countdown.textContent = `Time remaining: ${hours}h ${minutes}m ${remainder}s`;
  }

  function showMessage(message, isError = false) {
    let status = document.getElementById("bookingMessage");
    if (!status) {
      status = document.createElement("p");
      status.id = "bookingMessage";
      status.setAttribute("role", "status");
      status.setAttribute("aria-live", "polite");
      bookingPanel.before(status);
    }
    status.className = isError ? "hardware-message-error" : "hardware-message-success";
    status.textContent = message;
  }
})();
