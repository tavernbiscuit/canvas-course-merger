document.addEventListener("submit", (event) => {
  const message = event.target.dataset.confirm;
  if (message && !window.confirm(message)) {
    event.preventDefault();
  }
});

document.addEventListener("click", (event) => {
  const dismissButton = event.target.closest("[data-dismiss-notice]");
  if (dismissButton) {
    dismissButton.closest("[data-dismissible]")?.remove();
    return;
  }

  const row = event.target.closest("[data-row-href]");
  if (row && !event.target.closest("a, button, input, select, textarea, summary")) {
    window.location.assign(row.dataset.rowHref);
  }
});

const progressRoot = document.querySelector("[data-request-progress]");
if (progressRoot) {
  const trackers = [...progressRoot.querySelectorAll("[data-execution-tracker]")];
  let observedActiveExecution = false;
  let finalReloadScheduled = false;

  const formatStatus = (value) => value.replaceAll("_", " ");

  const formatUpdateTime = (value) => {
    if (!value) {
      return "just now";
    }
    const normalized = /(?:Z|[+-]\d{2}:\d{2})$/.test(value) ? value : `${value}Z`;
    return new Intl.DateTimeFormat([], {
      hour: "numeric",
      minute: "2-digit",
      second: "2-digit",
    }).format(new Date(normalized));
  };

  const updateTracker = (tracker, progress) => {
    tracker.dataset.tone = progress.tone;
    tracker.querySelector("[data-progress-title]").textContent = progress.title;
    tracker.querySelector("[data-progress-detail]").textContent = progress.detail;
    tracker.querySelector("[data-progress-bar]").value = progress.percent;
    tracker.querySelector("[data-progress-updated]").textContent =
      `Last update ${formatUpdateTime(progress.updated_at)}`;

    const liveState = tracker.querySelector("[data-live-state]");
    liveState.dataset.state = progress.terminal
      ? progress.tone === "error"
        ? "error"
        : "final"
      : "live";
    tracker.querySelector("[data-live-label]").textContent = progress.terminal
      ? progress.tone === "error"
        ? "Stopped"
        : "Final"
      : "Live";

    progress.steps.forEach((step) => {
      const stepElement = tracker.querySelector(`[data-progress-step="${step.key}"]`);
      if (stepElement) {
        stepElement.className = step.state;
      }
    });

    const groupCard = tracker.closest(".group-card");
    const groupStatus = groupCard.querySelector("[data-group-status]");
    groupStatus.className = `status ${progress.display_status}`;
    groupStatus.textContent = formatStatus(progress.display_status);
    groupCard.querySelector("[data-destination-id]").textContent =
      progress.destination_course_id || "Not created";

    progress.items.forEach((item) => {
      const row = groupCard.querySelector(`[data-progress-item="${item.id}"]`);
      if (!row) {
        return;
      }
      const itemStatus = row.querySelector("[data-item-status]");
      itemStatus.className = `status ${item.status}`;
      itemStatus.textContent = formatStatus(item.status);
      row.querySelector("[data-item-reason]").textContent = item.reason || "—";
    });
  };

  const showPollingError = () => {
    trackers.forEach((tracker) => {
      const liveState = tracker.querySelector("[data-live-state]");
      liveState.dataset.state = "error";
      tracker.querySelector("[data-live-label]").textContent = "Reconnecting";
      tracker.querySelector("[data-progress-updated]").textContent =
        "Progress is temporarily unavailable. Retrying automatically…";
    });
  };

  const pollProgress = async () => {
    try {
      const response = await fetch(progressRoot.dataset.progressUrl, {
        headers: { Accept: "application/json" },
        cache: "no-store",
      });
      if (!response.ok) {
        throw new Error(`Progress request failed with ${response.status}`);
      }
      const payload = await response.json();
      observedActiveExecution ||= payload.active;
      trackers.forEach((tracker) => {
        const progress = payload.groups.find(
          (group) => String(group.group_id) === tracker.dataset.groupId,
        );
        if (progress) {
          updateTracker(tracker, progress);
        }
      });

      if (payload.active) {
        window.setTimeout(pollProgress, payload.poll_after_ms || 2000);
      } else if (observedActiveExecution && !finalReloadScheduled) {
        finalReloadScheduled = true;
        trackers.forEach((tracker) => {
          tracker.querySelector("[data-progress-updated]").textContent =
            "Execution finished. Loading final actions and results…";
        });
        window.setTimeout(() => window.location.reload(), 1200);
      }
    } catch {
      showPollingError();
      window.setTimeout(pollProgress, 5000);
    }
  };

  if (trackers.length) {
    pollProgress();
  }
}

const intakeForm = document.querySelector("[data-intake-form]");
if (intakeForm) {
  const groups = intakeForm.querySelector("[data-destination-groups]");
  const groupTemplate = document.querySelector("#destination-group-template");
  const sectionTemplate = document.querySelector("#section-input-template");
  const structuredIntake = intakeForm.querySelector("[data-structured-intake]");
  const bulkUpload = intakeForm.querySelector("[data-bulk-upload]");
  const intakeMode = intakeForm.querySelector("[data-intake-mode]");

  const updateSectionControls = (group) => {
    const rows = [...group.querySelectorAll("[data-section-input]")];
    rows.forEach((row, index) => {
      row.querySelector("[data-section-number]").textContent = `Source SIS section ID ${index + 1}`;
      row.querySelector("[data-remove-section]").disabled = rows.length <= 2;
    });
  };

  const updateGroupControls = () => {
    const cards = [...groups.querySelectorAll("[data-destination-group]")];
    cards.forEach((group, index) => {
      group.querySelector("[data-group-number]").textContent = `Destination course ${index + 1}`;
      group.querySelector("[data-remove-group]").disabled = cards.length <= 1;
      updateSectionControls(group);
    });
  };

  const addSection = (group, groupIndex, value = "") => {
    const row = sectionTemplate.content.firstElementChild.cloneNode(true);
    const input = row.querySelector("input");
    input.name = `source_sis_id_${groupIndex}`;
    input.value = value;
    group.querySelector("[data-section-inputs]").append(row);
    updateSectionControls(group);
  };

  const addGroup = () => {
    const groupIndex = Number(groups.dataset.nextGroupIndex || "0");
    groups.dataset.nextGroupIndex = String(groupIndex + 1);
    const group = groupTemplate.content.firstElementChild.cloneNode(true);
    group.dataset.groupIndex = String(groupIndex);
    const hidden = group.querySelector('input[type="hidden"]');
    hidden.value = String(groupIndex);
    groups.append(group);
    addSection(group, groupIndex);
    addSection(group, groupIndex);
    updateGroupControls();
    group.scrollIntoView({ block: "nearest" });
    group.querySelector("[data-section-input] input").focus();
  };

  intakeForm.addEventListener("click", (event) => {
    const addSectionButton = event.target.closest("[data-add-section]");
    if (addSectionButton) {
      const group = addSectionButton.closest("[data-destination-group]");
      addSection(group, group.dataset.groupIndex);
      group.querySelector("[data-section-input]:last-child input").focus();
      return;
    }

    const removeSectionButton = event.target.closest("[data-remove-section]");
    if (removeSectionButton && !removeSectionButton.disabled) {
      const group = removeSectionButton.closest("[data-destination-group]");
      removeSectionButton.closest("[data-section-input]").remove();
      updateSectionControls(group);
      return;
    }

    const addGroupButton = event.target.closest("[data-add-group]");
    if (addGroupButton) {
      addGroup();
      return;
    }

    const removeGroupButton = event.target.closest("[data-remove-group]");
    if (removeGroupButton && !removeGroupButton.disabled) {
      removeGroupButton.closest("[data-destination-group]").remove();
      updateGroupControls();
    }
  });

  const updateIntakeMode = () => {
    const hasBulkFile = Boolean(bulkUpload.files.length);
    structuredIntake.disabled = hasBulkFile;
    structuredIntake.classList.toggle("disabled", structuredIntake.disabled);
    if (intakeMode) {
      intakeMode.textContent = hasBulkFile
        ? `${bulkUpload.files[0].name} will be used instead of the destination cards above.`
        : "";
    }
  };

  bulkUpload.addEventListener("change", updateIntakeMode);
  updateIntakeMode();
  updateGroupControls();
}
