"use strict";

const toggle = document.querySelector("#holiday-toggle");
const panel = document.querySelector("#holiday-list");
const status = document.querySelector("#holiday-status");
const items = document.querySelector("#holiday-items");
let loadPromise;

const kindLabels = {
  school_break: "School break",
  additional_kpm_holiday: "Additional KPM holiday",
  festival_observance: "Festival observance",
};

function formatDate(value) {
  const parsed = new Date(`${value}T00:00:00Z`);
  if (Number.isNaN(parsed.valueOf())) {
    throw new Error(`Invalid holiday date: ${value}`);
  }
  return new Intl.DateTimeFormat("en-MY", {
    day: "numeric",
    month: "short",
    year: "numeric",
    timeZone: "UTC",
  }).format(parsed);
}

function applicability(event) {
  const scopes = [];
  scopes.push(event.group ? `Group ${event.group}` : "Groups A and B");
  if (event.states_included.length) {
    scopes.push(`${event.states_included.join(", ")} only`);
  }
  if (event.states_excluded.length) {
    scopes.push(`except ${event.states_excluded.join(", ")}`);
  }
  return scopes.join(" — ");
}

function renderHoliday(event) {
  if (
    typeof event.title !== "string"
    || !kindLabels[event.kind]
    || typeof event.start_date !== "string"
    || typeof event.end_date !== "string"
    || !Array.isArray(event.states_included)
    || !Array.isArray(event.states_excluded)
  ) {
    throw new Error("The holiday list contains an invalid event.");
  }

  const item = document.createElement("li");
  const title = document.createElement("h3");
  title.textContent = event.title;
  item.append(title);

  const kind = document.createElement("p");
  kind.textContent = kindLabels[event.kind];
  item.append(kind);

  const dates = document.createElement("p");
  dates.className = "holiday-dates";
  dates.textContent = event.start_date === event.end_date
    ? formatDate(event.start_date)
    : `${formatDate(event.start_date)} – ${formatDate(event.end_date)}`;
  item.append(dates);

  const scope = document.createElement("p");
  scope.textContent = applicability(event);
  item.append(scope);

  if (event.source_note) {
    const note = document.createElement("p");
    note.textContent = `KPM note: ${event.source_note}`;
    item.append(note);
  }

  const sourceUrl = new URL(event.source_url, window.location.href);
  if (sourceUrl.protocol !== "https:" || sourceUrl.hostname !== "www.moe.gov.my") {
    throw new Error("The holiday list contains a non-KPM source URL.");
  }
  const source = document.createElement("a");
  source.href = sourceUrl.href;
  source.rel = "noopener";
  source.textContent = `Official KPM source (PDF, page ${event.page_number})`;
  item.append(source);
  return item;
}

async function loadHolidays() {
  status.textContent = "Loading validated holidays…";
  try {
    const response = await fetch("school-holidays.json", { cache: "no-cache" });
    if (!response.ok) {
      throw new Error(`Holiday list request failed with HTTP ${response.status}.`);
    }
    const data = await response.json();
    if (
      data.status !== "validated"
      || data.academic_year !== "2026"
      || !Array.isArray(data.events)
    ) {
      throw new Error("The published holiday list has an unexpected format.");
    }
    const list = document.createElement("ol");
    list.className = "holiday-list";
    for (const event of data.events) {
      list.append(renderHoliday(event));
    }
    items.replaceChildren(list);
    status.textContent = `${data.events.length} validated holiday and school-break entries for 2026.`;
  } catch (error) {
    console.error("Could not load the KPM holiday list.", error);
    status.textContent = "The holiday list could not be loaded. Please try again later.";
  }
}

toggle.addEventListener("click", async () => {
  const show = toggle.getAttribute("aria-expanded") !== "true";
  toggle.setAttribute("aria-expanded", String(show));
  toggle.textContent = show ? "Hide extracted school holidays" : "Show extracted school holidays";
  panel.hidden = !show;
  if (show) {
    if (!loadPromise) {
      loadPromise = loadHolidays();
    }
    await loadPromise;
    if (status.textContent.startsWith("The holiday list could not be loaded.")) {
      loadPromise = undefined;
    }
  }
});
