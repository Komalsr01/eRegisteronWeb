const views = [...document.querySelectorAll(".view")];
const navLinks = [...document.querySelectorAll("[data-view]")];
const registrationForm = document.querySelector("#registration-form");
const confirmationCard = document.querySelector("#confirmation");
const registrationError = document.querySelector("#registration-error");
const alcoholPreference = document.querySelector("#alcohol-preference");
const travelMode = document.querySelector("#travel-mode");

async function request(path, options = {}) {
  const response = await fetch(path, {
    credentials: "same-origin",
    ...options,
    headers: {
      ...(options.body ? { "Content-Type": "application/json" } : {}),
      ...(options.headers || {}),
    },
  });
  const contentType = response.headers.get("content-type") || "";
  const body = contentType.includes("application/json") ? await response.json() : null;
  if (!response.ok) {
    throw new Error(body?.error || `Request failed (${response.status}).`);
  }
  return body;
}

function showView(viewId) {
  views.forEach((view) => { view.hidden = view.id !== viewId; });
  document.querySelectorAll(".nav-link").forEach((link) => {
    link.classList.toggle("is-active", link.dataset.view === viewId);
  });
  if (viewId === "admin-view") {
    refreshAdminSession();
  }
  window.scrollTo({ top: 0, behavior: "smooth" });
}

navLinks.forEach((link) => {
  link.addEventListener("click", () => showView(link.dataset.view));
});

alcoholPreference.addEventListener("change", () => {
  const plansToDrink = alcoholPreference.value === "yes";
  travelMode.required = plansToDrink;
  if (!plansToDrink) {
    travelMode.setCustomValidity("");
  }
});

travelMode.addEventListener("change", () => {
  const selfDrive = travelMode.value === "self_drive";
  travelMode.setCustomValidity(
    selfDrive && alcoholPreference.value === "yes"
      ? "Self drive is not allowed when you plan to drink."
      : "",
  );
});

registrationForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  registrationError.hidden = true;
  const submitButton = registrationForm.querySelector('button[type="submit"]');
  submitButton.disabled = true;
  const form = new FormData(registrationForm);
  const payload = {
    fullName: form.get("fullName"),
    mobile: form.get("mobile"),
    officialEmail: form.get("officialEmail"),
    emergencyContactName: form.get("emergencyContactName"),
    emergencyContactMobile: form.get("emergencyContactMobile"),
    dietaryPreference: form.get("dietaryPreference"),
    alcoholPreference: form.get("alcoholPreference"),
    travelMode: form.get("travelMode") || null,
    attendanceConfirmed: form.get("attendanceConfirmed") === "on",
    termsAccepted: form.get("termsAccepted") === "on",
  };
  try {
    const result = await request("/api/register", {
      method: "POST",
      body: JSON.stringify(payload),
    });
    document.querySelector("#confirmation-title").textContent =
      `You’re on the list, ${result.registration.full_name}.`;
    document.querySelector("#confirmation-message").textContent = result.message;
    document.querySelector("#entry-code").textContent = result.registration.credential;
    confirmationCard.hidden = false;
    registrationForm.reset();
    travelMode.required = false;
    travelMode.setCustomValidity("");
    confirmationCard.scrollIntoView({ behavior: "smooth", block: "center" });
  } catch (error) {
    registrationError.textContent = error.message;
    registrationError.hidden = false;
  } finally {
    submitButton.disabled = false;
  }
});

async function refreshAdminSession() {
  const loginPanel = document.querySelector("#admin-login-panel");
  const dashboard = document.querySelector("#admin-dashboard");
  try {
    const result = await request("/api/admin/session");
    loginPanel.hidden = result.authenticated;
    dashboard.hidden = !result.authenticated;
    if (result.authenticated) {
      await refreshDashboard();
    }
  } catch (error) {
    loginPanel.hidden = false;
    dashboard.hidden = true;
    showLoginError(error.message);
  }
}

function showLoginError(message) {
  const error = document.querySelector("#admin-login-error");
  error.textContent = message;
  error.hidden = false;
}

document.querySelector("#admin-login-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = event.currentTarget;
  const password = new FormData(form).get("password");
  const error = document.querySelector("#admin-login-error");
  error.hidden = true;
  try {
    await request("/api/admin/login", {
      method: "POST",
      body: JSON.stringify({ password }),
    });
    form.reset();
    document.querySelector("#admin-login-panel").hidden = true;
    document.querySelector("#admin-dashboard").hidden = false;
    try {
      await refreshDashboard();
    } catch (exception) {
      showCheckinResult(`Signed in, but the dashboard could not be refreshed: ${exception.message}`, true);
    }
  } catch (exception) {
    showLoginError(exception.message);
  }
});

document.querySelector("#admin-logout").addEventListener("click", async () => {
  try {
    await request("/api/admin/logout", { method: "POST", body: "{}" });
    document.querySelector("#admin-dashboard").hidden = true;
    document.querySelector("#admin-login-panel").hidden = false;
  } catch (error) {
    showCheckinResult(error.message, true);
  }
});

async function refreshDashboard() {
  await Promise.all([loadRegistrations(), loadStats()]);
}

async function loadStats() {
  const stats = await request("/api/admin/stats");
  document.querySelector("#stat-registered").textContent = stats.registered;
  document.querySelector("#stat-checked-in").textContent = stats.checkedIn;
  document.querySelector("#stat-awaiting").textContent = stats.awaiting;
}

async function loadRegistrations() {
  const form = new FormData(document.querySelector("#filter-form"));
  const params = new URLSearchParams();
  for (const key of ["search", "diet", "alcohol", "attendance"]) {
    const value = form.get(key);
    if (value) params.set(key, value);
  }
  const result = await request(`/api/admin/registrations?${params.toString()}`);
  const tableBody = document.querySelector("#registrations-body");
  tableBody.replaceChildren();
  document.querySelector("#empty-registrations").hidden = result.registrations.length !== 0;
  result.registrations.forEach((registration) => {
    tableBody.append(createRegistrationRow(registration));
  });
}

function cell(primary, secondary) {
  const element = document.createElement("td");
  const strong = document.createElement("strong");
  strong.textContent = primary || "—";
  element.append(strong);
  if (secondary) {
    const small = document.createElement("small");
    small.textContent = secondary;
    element.append(small);
  }
  return element;
}

function createRegistrationRow(registration) {
  const row = document.createElement("tr");
  row.append(
    cell(registration.full_name, registration.official_email),
    cell(registration.mobile, `${registration.emergency_contact_name} · ${registration.emergency_contact_mobile}`),
    cell(
      registration.dietary_preference,
      `${registration.alcohol_preference === "yes" ? "Drinking" : "No alcohol"}${registration.travel_mode ? ` · ${registration.travel_mode.replaceAll("_", " ")}` : ""}`,
    ),
  );
  const attendance = document.createElement("td");
  const badge = document.createElement("span");
  badge.className = `attendance-badge${registration.attendance_at ? " checked-in" : ""}`;
  badge.textContent = registration.attendance_at ? "Checked in" : "Awaiting entry";
  attendance.append(badge);
  if (registration.attendance_at) {
    const timestamp = document.createElement("small");
    timestamp.textContent = new Date(registration.attendance_at).toLocaleString();
    attendance.append(timestamp);
  }
  const entry = cell(registration.credential, `Email: ${registration.email_status}`);
  const resendButton = document.createElement("button");
  resendButton.type = "button";
  resendButton.className = "inline-link email-resend";
  resendButton.dataset.resendId = registration.id;
  resendButton.textContent = registration.email_status === "sent" ? "Resend via EmailJS" : "Send via EmailJS";
  entry.append(resendButton);
  row.append(attendance, entry);
  return row;
}

document.querySelector("#registrations-body").addEventListener("click", async (event) => {
  if (!(event.target instanceof Element)) return;
  const button = event.target.closest("[data-resend-id]");
  if (!button) return;
  button.disabled = true;
  let result;
  try {
    result = await request("/api/admin/email/resend", {
      method: "POST",
      body: JSON.stringify({ registrationId: button.dataset.resendId }),
    });
  } catch (error) {
    showCheckinResult(error.message, true);
    button.disabled = false;
    return;
  }
  showCheckinResult(result.message, result.emailStatus !== "sent");
  try {
    await loadRegistrations();
  } catch (error) {
    showCheckinResult(
      `${result.message} The registration list could not be refreshed: ${error.message}`,
      true,
    );
  }
  button.disabled = false;
});

document.querySelector("#filter-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  try {
    await loadRegistrations();
  } catch (error) {
    showCheckinResult(error.message, true);
  }
});

function showCheckinResult(message, isError = false) {
  const result = document.querySelector("#checkin-result");
  result.textContent = message;
  result.classList.toggle("is-error", isError);
  result.hidden = false;
}

async function submitCheckin(path, payload) {
  let result;
  try {
    result = await request(path, {
      method: "POST",
      body: JSON.stringify(payload),
    });
  } catch (error) {
    showCheckinResult(error.message, true);
    return;
  }
  const attendee = result.registration;
  showCheckinResult(
    `${attendee.full_name} is verified and checked in at ${new Date(attendee.attendance_at).toLocaleString()}.`,
  );
  try {
    await refreshDashboard();
  } catch (error) {
    showCheckinResult(
      `${attendee.full_name} is checked in, but the dashboard could not be refreshed: ${error.message}`,
      true,
    );
  }
}

document.querySelector("#code-checkin-form").addEventListener("submit", (event) => {
  event.preventDefault();
  const credential = new FormData(event.currentTarget).get("credential");
  submitCheckin("/api/admin/attendance", { credential });
  event.currentTarget.reset();
});

document.querySelector("#manual-checkin-form").addEventListener("submit", (event) => {
  event.preventDefault();
  const form = new FormData(event.currentTarget);
  submitCheckin("/api/admin/attendance/manual", {
    fullName: form.get("fullName"),
    officialEmail: form.get("officialEmail"),
  });
  event.currentTarget.reset();
});

async function loadEventInformation() {
  try {
    const event = await request("/api/public/event");
    const demoNotice = document.querySelector("#demo-notice");
    demoNotice.hidden = !event.demoMode;
    if (event.demoMode) {
      document.querySelector('[data-view="admin-view"]').hidden = true;
    }
    document.querySelector("#event-date").textContent = event.date;
    document.querySelector("#event-venue").textContent = event.venue;
    document.querySelector("#event-gates").textContent = event.gates;
    document.querySelector("#event-emergency").textContent = event.emergency;
  } catch (error) {
    document.querySelector("#event-emergency").textContent =
      `Event information could not be loaded: ${error.message}`;
  }
}

loadEventInformation();
