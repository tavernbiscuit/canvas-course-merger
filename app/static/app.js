document.addEventListener("submit", (event) => {
  const message = event.target.dataset.confirm;
  if (message && !window.confirm(message)) {
    event.preventDefault();
  }
});

const intakeForm = document.querySelector("[data-intake-form]");
if (intakeForm) {
  const groups = intakeForm.querySelector("[data-destination-groups]");
  const groupTemplate = document.querySelector("#destination-group-template");
  const sectionTemplate = document.querySelector("#section-input-template");
  const structuredIntake = intakeForm.querySelector("[data-structured-intake]");
  const bulkUpload = intakeForm.querySelector("[data-bulk-upload]");

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
    structuredIntake.disabled = Boolean(bulkUpload.files.length);
    structuredIntake.classList.toggle("disabled", structuredIntake.disabled);
  };

  bulkUpload.addEventListener("change", updateIntakeMode);
  updateIntakeMode();
  updateGroupControls();
}
