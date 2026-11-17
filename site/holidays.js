"use strict";

const toggle2026 = document.querySelector("#holiday-toggle-2026");
const panel2026 = document.querySelector("#holiday-panel-2026");
const toggle2027 = document.querySelector("#holiday-toggle-2027");
const panel2027 = document.querySelector("#holiday-panel-2027");
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
  scopes.push(
    event.applicable_groups.length === 2
      ? "Groups A and B"
      : `Group ${event.applicable_groups.join(" and ")}`,
  );
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
    || !Array.isArray(event.applicable_groups)
    || event.applicable_groups.length === 0
    || !Array.isArray(event.states_included)
    || !Array.isArray(event.states_excluded)
  ) {
    throw new Error("The holiday list contains an invalid event.");
  }

  const item = document.createElement("tr");
  item.dataset.groups = event.applicable_groups.join(" ");
  const dates = document.createElement("td");
  dates.className = "holiday-dates";
  dates.textContent = event.start_date === event.end_date
    ? formatDate(event.start_date)
    : `${formatDate(event.start_date)} – ${formatDate(event.end_date)}`;
  item.append(dates);

  const title = document.createElement("th");
  title.scope = "row";
  title.textContent = event.title;
  item.append(title);

  const kind = document.createElement("td");
  kind.textContent = kindLabels[event.kind];
  item.append(kind);

  const applicabilityCell = document.createElement("td");
  const scope = document.createElement("p");
  scope.textContent = applicability(event);
  applicabilityCell.append(scope);

  if (event.source_note) {
    const note = document.createElement("p");
    note.textContent = `KPM note: ${event.source_note}`;
    applicabilityCell.append(note);
  }
  item.append(applicabilityCell);

  const sourceUrl = new URL(event.source_url, window.location.href);
  if (sourceUrl.protocol !== "https:" || sourceUrl.hostname !== "www.moe.gov.my") {
    throw new Error("The holiday list contains a non-KPM source URL.");
  }
  const sourceCell = document.createElement("td");
  const source = document.createElement("a");
  source.href = sourceUrl.href;
  source.rel = "noopener";
  source.textContent = `Official KPM source (PDF, page ${event.page_number})`;
  sourceCell.className = "holiday-source";
  sourceCell.append(source);
  item.append(sourceCell);
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
    const table = document.createElement("table");
    table.className = "holiday-table";
    const caption = document.createElement("caption");
    caption.textContent = "Validated 2026 school holidays and breaks";
    table.append(caption);

    const head = document.createElement("thead");
    const headerRow = document.createElement("tr");
    for (const label of ["Date", "Holiday", "Type", "Applies to", "Source"]) {
      const heading = document.createElement("th");
      heading.scope = "col";
      heading.textContent = label;
      headerRow.append(heading);
    }
    head.append(headerRow);
    table.append(head);

    const body = document.createElement("tbody");
    for (const event of data.events) {
      body.append(renderHoliday(event));
    }
    table.append(body);
    const tableWrap = document.createElement("div");
    tableWrap.className = "holiday-table-wrap";
    tableWrap.append(table);
    items.replaceChildren(tableWrap);
    filterHolidayRows();
  } catch (error) {
    console.error("Could not load the KPM holiday list.", error);
    status.textContent = "The holiday list could not be loaded. Please try again later.";
  }
}

function filterHolidayRows() {
  const selectedGroup = document.querySelector('input[name="holiday-group"]:checked').value;
  const rows = items.querySelectorAll("tbody tr");
  let visibleCount = 0;
  for (const row of rows) {
    const appliesToGroup = row.dataset.groups.split(" ").includes(selectedGroup);
    row.hidden = !appliesToGroup;
    if (appliesToGroup) {
      visibleCount += 1;
    }
  }
  status.textContent = `Showing ${visibleCount} validated holiday and school-break entries for 2026 — Group ${selectedGroup}.`;
}

for (const radio of document.querySelectorAll('input[name="holiday-group"]')) {
  radio.addEventListener("change", filterHolidayRows);
}

function togglePanel(button, panel, year) {
  const show = button.getAttribute("aria-expanded") !== "true";
  button.setAttribute("aria-expanded", String(show));
  panel.hidden = !show;
  button.textContent = `${show ? "Hide" : "Show"} ${year} school holidays`;
  return show;
}

toggle2026.addEventListener("click", async () => {
  const show = togglePanel(toggle2026, panel2026, "2026");
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

toggle2027.addEventListener("click", () => {
  togglePanel(toggle2027, panel2027, "2027");
});
