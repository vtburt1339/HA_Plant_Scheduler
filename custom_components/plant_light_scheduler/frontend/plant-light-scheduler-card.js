const CARD_VERSION = "0.1.0";
const WS = {
  list: "plant_light_scheduler/list",
  create: "plant_light_scheduler/create",
  update: "plant_light_scheduler/update",
  delete: "plant_light_scheduler/delete",
};

const escapeHtml = (value) =>
  String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");

class PlantLightSchedulerCard extends HTMLElement {
  static getConfigElement() {
    return document.createElement("plant-light-scheduler-card-editor");
  }

  static getStubConfig() {
    return { title: "Plant Light Scheduler" };
  }

  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._config = {};
    this._schedules = [];
    this._loading = true;
    this._saving = false;
    this._error = "";
    this._draft = null;
    this._confirmingDelete = false;
    this._initialized = false;
    this.shadowRoot.addEventListener("keydown", (event) => {
      if (event.key === "Escape" && this._draft) this._closeDialog();
    });
  }

  setConfig(config) {
    if (!config || typeof config !== "object") {
      throw new Error("Invalid Plant Light Scheduler card configuration");
    }
    this._config = { title: "Plant Light Scheduler", ...config };
    if (this._initialized) this._render();
  }

  set hass(hass) {
    const firstAssignment = !this._hass;
    this._hass = hass;
    if (firstAssignment && this.isConnected) this._initialize();
  }

  getCardSize() {
    return Math.max(3, this._schedules.length * 2 + 2);
  }

  connectedCallback() {
    if (this._hass) this._initialize();
  }

  _initialize() {
    if (this._initialized) return;
    this._initialized = true;
    this._render();
    this._loadSchedules();
  }

  get _isAdmin() {
    return this._hass?.user?.is_admin !== false;
  }

  async _loadSchedules() {
    this._loading = true;
    this._error = "";
    this._render();
    try {
      const result = await this._hass.connection.sendMessagePromise({
        type: WS.list,
      });
      this._schedules = Array.isArray(result) ? result : [];
    } catch (error) {
      this._error = this._errorMessage(
        error,
        "Could not load schedules. Make sure the integration is configured."
      );
    } finally {
      this._loading = false;
      this._render();
    }
  }

  _friendlyName(entityId) {
    return (
      this._hass?.states?.[entityId]?.attributes?.friendly_name ||
      entityId.replace(/^switch\./, "").replaceAll("_", " ")
    );
  }

  _scheduleName(schedule) {
    return schedule.entities.map((id) => this._friendlyName(id)).join(", ");
  }

  _formatTime(value) {
    const [hour, minute] = value.split(":").map(Number);
    const date = new Date(2000, 0, 1, hour, minute);
    const configuredFormat = this._hass?.locale?.time_format;
    const options = { hour: "numeric", minute: "2-digit" };
    if (configuredFormat === "12") options.hour12 = true;
    if (configuredFormat === "24") options.hour12 = false;
    return new Intl.DateTimeFormat(this._hass?.locale?.language, options).format(
      date
    );
  }

  _render() {
    if (!this.shadowRoot) return;
    this.shadowRoot.innerHTML = `
      <style>${this._styles()}</style>
      <ha-card>
        <div class="card-header">
          <div>
            <h1>${escapeHtml(this._config.title || "Plant Light Scheduler")}</h1>
            <p class="subtitle">Daily switch schedules</p>
          </div>
          ${
            this._schedules.length
              ? '<span class="enabled-heading">Enabled</span>'
              : ""
          }
        </div>
        <div class="content" aria-live="polite">
          ${this._renderContent()}
        </div>
        ${this._renderFooter()}
      </ha-card>
      <div class="dialog-host"></div>
    `;
    this._bindCardEvents();
    if (this._draft) this._renderDialog();
  }

  _renderContent() {
    if (this._loading) {
      return `
        <div class="message loading-message">
          <ha-circular-progress active size="small"></ha-circular-progress>
          <span>Loading schedules…</span>
        </div>`;
    }
    if (this._error) {
      return `
        <div class="message error-message">
          <ha-icon icon="mdi:alert-circle-outline"></ha-icon>
          <div><strong>Schedules unavailable</strong><span>${escapeHtml(
            this._error
          )}</span></div>
          <button class="text-button" data-action="retry">Retry</button>
        </div>`;
    }
    if (!this._schedules.length) {
      return `
        <div class="empty-state">
          <div class="empty-icon"><ha-icon icon="mdi:sprout-outline"></ha-icon></div>
          <strong>No light schedules yet</strong>
          <span>Add a daily on/off schedule for one or more switches.</span>
        </div>`;
    }
    return `<div class="schedule-list">${this._schedules
      .map((schedule) => this._renderSchedule(schedule))
      .join("")}</div>`;
  }

  _renderSchedule(schedule) {
    const enabled = schedule.enabled !== false;
    const entityCount = schedule.entities.length;
    return `
      <div class="schedule-row ${enabled ? "" : "disabled"}">
        <button class="schedule-main" data-action="edit" data-id="${escapeHtml(
          schedule.id
        )}" aria-label="Edit ${escapeHtml(this._scheduleName(schedule))}">
          <span class="schedule-icon"><ha-icon icon="mdi:lightbulb-fluorescent-tube-outline"></ha-icon></span>
          <span class="schedule-details">
            <span class="schedule-name">${escapeHtml(
              this._scheduleName(schedule)
            )}</span>
            <span class="schedule-time">
              <strong>ON</strong> ${escapeHtml(this._formatTime(schedule.on))}
              <span class="dot" aria-hidden="true"></span>
              <strong>OFF</strong> ${escapeHtml(this._formatTime(schedule.off))}
            </span>
            ${
              entityCount > 1
                ? `<span class="entity-count">${entityCount} switches</span>`
                : ""
            }
          </span>
        </button>
        <ha-switch
          class="enable-switch"
          data-id="${escapeHtml(schedule.id)}"
          aria-label="${enabled ? "Disable" : "Enable"} ${escapeHtml(
            this._scheduleName(schedule)
          )}"
          ${enabled ? "checked" : ""}
          ${this._isAdmin ? "" : "disabled"}
        ></ha-switch>
      </div>`;
  }

  _renderFooter() {
    if (this._loading || this._error || !this._isAdmin) {
      if (!this._isAdmin && !this._loading && !this._error) {
        return '<div class="admin-note">An administrator can edit these schedules.</div>';
      }
      return "";
    }
    return `
      <div class="card-footer">
        <button class="add-button" data-action="add">
          <ha-icon icon="mdi:plus"></ha-icon><span>Add schedule</span>
        </button>
      </div>`;
  }

  _bindCardEvents() {
    this.shadowRoot
      .querySelector('[data-action="add"]')
      ?.addEventListener("click", () => this._openEditor());
    this.shadowRoot
      .querySelector('[data-action="retry"]')
      ?.addEventListener("click", () => this._loadSchedules());
    this.shadowRoot.querySelectorAll('[data-action="edit"]').forEach((button) => {
      button.addEventListener("click", () => {
        const schedule = this._schedules.find(
          (item) => item.id === button.dataset.id
        );
        if (schedule && this._isAdmin) this._openEditor(schedule);
      });
    });
    this.shadowRoot.querySelectorAll("ha-switch[data-id]").forEach((toggle) => {
      toggle.addEventListener("change", (event) => {
        event.stopPropagation();
        this._toggleSchedule(toggle.dataset.id, Boolean(toggle.checked));
      });
    });
  }

  _openEditor(schedule = null) {
    if (!this._isAdmin) return;
    this._confirmingDelete = false;
    this._draft = schedule
      ? {
          id: schedule.id,
          entities: [...schedule.entities],
          on: schedule.on,
          off: schedule.off,
          enabled: schedule.enabled !== false,
        }
      : { entities: [], on: "08:00", off: "21:00", enabled: true };
    this._renderDialog();
  }

  _closeDialog() {
    if (this._saving) return;
    this._draft = null;
    this._confirmingDelete = false;
    const host = this.shadowRoot.querySelector(".dialog-host");
    if (host) host.innerHTML = "";
  }

  _renderDialog() {
    const host = this.shadowRoot.querySelector(".dialog-host");
    if (!host || !this._draft) return;
    host.innerHTML = this._confirmingDelete
      ? this._deleteConfirmationTemplate()
      : this._editorTemplate();
    this._bindDialogEvents();
  }

  _editorTemplate() {
    const isEditing = Boolean(this._draft.id);
    return `
      <div class="dialog-scrim" data-action="dismiss">
        <section class="dialog" role="dialog" aria-modal="true" aria-labelledby="schedule-dialog-title">
          <div class="dialog-heading">
            <div>
              <span class="eyebrow">Plant lights</span>
              <h2 id="schedule-dialog-title">${
                isEditing ? "Edit schedule" : "Add schedule"
              }</h2>
            </div>
            <button class="icon-button" data-action="cancel" aria-label="Close">
              <ha-icon icon="mdi:close"></ha-icon>
            </button>
          </div>
          <div class="dialog-body">
            <div class="field-group">
              <label class="field-label">Switches</label>
              <p class="field-help">Search for a switch, then select it to add it.</p>
              <ha-entity-picker class="entity-picker"></ha-entity-picker>
              <div class="entity-chips">
                ${
                  this._draft.entities.length
                    ? this._draft.entities
                        .map(
                          (entityId) => `
                          <span class="entity-chip">
                            <ha-icon icon="mdi:toggle-switch-outline"></ha-icon>
                            <span>${escapeHtml(this._friendlyName(entityId))}</span>
                            <button data-action="remove-entity" data-entity="${escapeHtml(
                              entityId
                            )}" aria-label="Remove ${escapeHtml(
                              this._friendlyName(entityId)
                            )}">
                              <ha-icon icon="mdi:close"></ha-icon>
                            </button>
                          </span>`
                        )
                        .join("")
                    : '<span class="selection-hint">No switches selected</span>'
                }
              </div>
            </div>
            <div class="time-grid">
              <label class="time-field">
                <span>On</span>
                <input id="on-time" type="time" value="${escapeHtml(
                  this._draft.on
                )}" required>
              </label>
              <label class="time-field">
                <span>Off</span>
                <input id="off-time" type="time" value="${escapeHtml(
                  this._draft.off
                )}" required>
              </label>
            </div>
            <div class="validation-message" role="alert"></div>
          </div>
          <div class="dialog-actions">
            ${
              isEditing
                ? '<button class="delete-button" data-action="ask-delete"><ha-icon icon="mdi:delete-outline"></ha-icon><span>Delete</span></button>'
                : '<span class="action-spacer"></span>'
            }
            <button class="secondary-button" data-action="cancel">Cancel</button>
            <button class="save-button" data-action="save" ${
              this._saving ? "disabled" : ""
            }>${this._saving ? "Saving…" : "Save"}</button>
          </div>
        </section>
      </div>`;
  }

  _deleteConfirmationTemplate() {
    return `
      <div class="dialog-scrim confirm-scrim">
        <section class="dialog confirm-dialog" role="alertdialog" aria-modal="true" aria-labelledby="delete-title">
          <div class="confirm-icon"><ha-icon icon="mdi:delete-outline"></ha-icon></div>
          <h2 id="delete-title">Delete this schedule?</h2>
          <p>The switches will keep their current state, and this schedule will no longer run.</p>
          <div class="confirm-actions">
            <button class="secondary-button" data-action="keep">Keep schedule</button>
            <button class="confirm-delete-button" data-action="delete" ${
              this._saving ? "disabled" : ""
            }>${this._saving ? "Deleting…" : "Delete schedule"}</button>
          </div>
        </section>
      </div>`;
  }

  _bindDialogEvents() {
    this._setupEntityPicker();

    this.shadowRoot.querySelectorAll('[data-action="cancel"]').forEach((button) =>
      button.addEventListener("click", () => this._closeDialog())
    );
    this.shadowRoot
      .querySelector('[data-action="dismiss"]')
      ?.addEventListener("click", (event) => {
        if (event.target === event.currentTarget) this._closeDialog();
      });
    this.shadowRoot
      .querySelectorAll('[data-action="remove-entity"]')
      .forEach((button) =>
        button.addEventListener("click", () => {
          this._draft.entities = this._draft.entities.filter(
            (entityId) => entityId !== button.dataset.entity
          );
          this._renderDialog();
        })
      );
    this.shadowRoot
      .querySelector('[data-action="save"]')
      ?.addEventListener("click", () => this._saveDraft());
    this.shadowRoot
      .querySelector('[data-action="ask-delete"]')
      ?.addEventListener("click", () => {
        this._confirmingDelete = true;
        this._renderDialog();
      });
    this.shadowRoot
      .querySelector('[data-action="keep"]')
      ?.addEventListener("click", () => {
        this._confirmingDelete = false;
        this._renderDialog();
      });
    this.shadowRoot
      .querySelector('[data-action="delete"]')
      ?.addEventListener("click", () => this._deleteDraft());
  }

  async _setupEntityPicker() {
    try {
      if (!customElements.get("ha-entity-picker")) {
        const helpers = await window.loadCardHelpers?.();
        await helpers?.createCardElement({ type: "entities", entities: [] });
        const entitiesCard = customElements.get("hui-entities-card");
        await entitiesCard?.getConfigElement?.();
        await customElements.whenDefined("ha-entity-picker");
      }
      const picker = this.shadowRoot.querySelector(".entity-picker");
      if (!picker || !this._draft) return;
      picker.hass = this._hass;
      picker.includeDomains = ["switch"];
      picker.allowCustomEntity = false;
      picker.label = "Search switches";
      picker.addEventListener("value-changed", (event) => {
        const entityId = event.detail?.value;
        if (entityId && !this._draft.entities.includes(entityId)) {
          this._draft.entities.push(entityId);
          this._renderDialog();
        }
      });
    } catch (error) {
      this._showValidation(
        this._errorMessage(error, "Could not load the switch picker.")
      );
    }
  }

  _showValidation(message) {
    const element = this.shadowRoot.querySelector(".validation-message");
    if (element) element.textContent = message;
  }

  async _saveDraft() {
    const on = this.shadowRoot.querySelector("#on-time")?.value;
    const off = this.shadowRoot.querySelector("#off-time")?.value;
    if (!this._draft.entities.length) {
      this._showValidation("Select at least one switch.");
      return;
    }
    if (!on || !off) {
      this._showValidation("Choose both an on time and an off time.");
      return;
    }
    if (on === off) {
      this._showValidation("On and off times must be different.");
      return;
    }

    const wasEditing = Boolean(this._draft.id);
    this._draft.on = on;
    this._draft.off = off;
    this._saving = true;
    this._renderDialog();
    try {
      const type = this._draft.id ? WS.update : WS.create;
      const message = {
        type,
        entities: this._draft.entities,
        on: this._draft.on,
        off: this._draft.off,
        enabled: this._draft.enabled,
      };
      if (this._draft.id) message.schedule_id = this._draft.id;
      await this._hass.connection.sendMessagePromise(message);
      this._draft = null;
      await this._loadSchedules();
      this._notify(wasEditing ? "Schedule updated" : "Schedule saved");
    } catch (error) {
      this._saving = false;
      this._renderDialog();
      this._showValidation(this._errorMessage(error, "Could not save schedule."));
      return;
    }
    this._saving = false;
  }

  async _deleteDraft() {
    if (!this._draft?.id) return;
    this._saving = true;
    this._renderDialog();
    try {
      await this._hass.connection.sendMessagePromise({
        type: WS.delete,
        schedule_id: this._draft.id,
      });
      this._draft = null;
      this._confirmingDelete = false;
      await this._loadSchedules();
      this._notify("Schedule deleted");
    } catch (error) {
      this._saving = false;
      this._confirmingDelete = false;
      this._renderDialog();
      this._showValidation(this._errorMessage(error, "Could not delete schedule."));
      return;
    }
    this._saving = false;
  }

  async _toggleSchedule(scheduleId, enabled) {
    const schedule = this._schedules.find((item) => item.id === scheduleId);
    if (!schedule || !this._isAdmin) return;
    try {
      await this._hass.connection.sendMessagePromise({
        type: WS.update,
        schedule_id: schedule.id,
        entities: schedule.entities,
        on: schedule.on,
        off: schedule.off,
        enabled,
      });
      schedule.enabled = enabled;
      this._render();
    } catch (error) {
      this._notify(this._errorMessage(error, "Could not change schedule."));
      this._render();
    }
  }

  _notify(message) {
    this.dispatchEvent(
      new CustomEvent("hass-notification", {
        bubbles: true,
        composed: true,
        detail: { message },
      })
    );
  }

  _errorMessage(error, fallback) {
    return error?.message || error?.body?.message || fallback;
  }

  _styles() {
    return `
      :host {
        display: block;
        --scheduler-accent: var(--primary-color, #03a9d9);
        --scheduler-on: var(--state-switch-active-color, var(--primary-color, #03a9d9));
      }
      * { box-sizing: border-box; }
      ha-card {
        overflow: hidden;
        color: var(--primary-text-color);
        background: var(--ha-card-background, var(--card-background-color, #fff));
      }
      button, input { font: inherit; }
      button { color: inherit; }
      .card-header {
        display: flex;
        align-items: end;
        justify-content: space-between;
        gap: 24px;
        padding: 24px 24px 14px;
      }
      h1, h2, p { margin: 0; }
      h1 {
        font-size: 24px;
        line-height: 1.2;
        font-weight: 500;
        letter-spacing: -0.2px;
      }
      .subtitle {
        margin-top: 4px;
        color: var(--secondary-text-color);
        font-size: 13px;
      }
      .enabled-heading {
        width: 64px;
        text-align: center;
        color: var(--secondary-text-color);
        font-size: 12px;
        font-weight: 500;
      }
      .content { min-height: 72px; }
      .schedule-list { padding: 0 12px 8px; }
      .schedule-row {
        display: grid;
        grid-template-columns: minmax(0, 1fr) 64px;
        align-items: center;
        min-height: 78px;
        border-radius: 14px;
        transition: background 120ms ease, opacity 120ms ease;
      }
      .schedule-row:hover { background: var(--secondary-background-color); }
      .schedule-row.disabled { opacity: 0.58; }
      .schedule-main {
        display: flex;
        align-items: center;
        gap: 16px;
        width: 100%;
        min-width: 0;
        padding: 12px;
        border: 0;
        background: transparent;
        text-align: left;
        cursor: pointer;
      }
      .schedule-main:focus-visible, .add-button:focus-visible, .icon-button:focus-visible,
      .save-button:focus-visible, .secondary-button:focus-visible, .delete-button:focus-visible,
      .confirm-delete-button:focus-visible {
        outline: 2px solid var(--scheduler-accent);
        outline-offset: 2px;
      }
      .schedule-icon {
        display: grid;
        place-items: center;
        flex: 0 0 42px;
        width: 42px;
        height: 42px;
        border-radius: 12px;
        color: var(--scheduler-on);
        background: color-mix(in srgb, var(--scheduler-on) 14%, transparent);
      }
      .schedule-icon ha-icon { --mdc-icon-size: 23px; }
      .schedule-details { display: flex; flex-direction: column; min-width: 0; gap: 4px; }
      .schedule-name {
        overflow: hidden;
        color: var(--primary-text-color);
        font-size: 16px;
        font-weight: 500;
        line-height: 1.25;
        text-overflow: ellipsis;
        white-space: nowrap;
      }
      .schedule-time {
        display: flex;
        align-items: center;
        flex-wrap: wrap;
        gap: 5px;
        color: var(--secondary-text-color);
        font-size: 13px;
        line-height: 1.3;
      }
      .schedule-time strong {
        color: var(--primary-text-color);
        font-size: 11px;
        letter-spacing: .45px;
      }
      .dot { width: 3px; height: 3px; margin: 0 3px; border-radius: 50%; background: currentColor; }
      .entity-count { color: var(--secondary-text-color); font-size: 11px; }
      .enable-switch { justify-self: center; }
      .card-footer {
        padding: 8px 16px 16px;
        border-top: 1px solid var(--divider-color);
      }
      .add-button, .text-button {
        display: inline-flex;
        align-items: center;
        gap: 8px;
        min-height: 40px;
        padding: 0 12px;
        border: 0;
        border-radius: 20px;
        color: var(--scheduler-accent);
        background: transparent;
        font-weight: 500;
        cursor: pointer;
      }
      .add-button:hover, .text-button:hover { background: color-mix(in srgb, var(--scheduler-accent) 10%, transparent); }
      .add-button ha-icon { --mdc-icon-size: 20px; }
      .admin-note {
        padding: 12px 24px 18px;
        border-top: 1px solid var(--divider-color);
        color: var(--secondary-text-color);
        font-size: 12px;
      }
      .message {
        display: flex;
        align-items: center;
        justify-content: center;
        gap: 12px;
        min-height: 112px;
        padding: 20px 24px;
        color: var(--secondary-text-color);
      }
      .error-message { justify-content: flex-start; color: var(--error-color); }
      .error-message > div { display: flex; flex: 1; flex-direction: column; gap: 3px; }
      .error-message span { color: var(--secondary-text-color); font-size: 12px; }
      .empty-state {
        display: flex;
        align-items: center;
        flex-direction: column;
        padding: 22px 24px 26px;
        text-align: center;
      }
      .empty-icon {
        display: grid;
        place-items: center;
        width: 48px;
        height: 48px;
        margin-bottom: 10px;
        border-radius: 50%;
        color: var(--scheduler-accent);
        background: color-mix(in srgb, var(--scheduler-accent) 12%, transparent);
      }
      .empty-state > span { max-width: 310px; margin-top: 5px; color: var(--secondary-text-color); font-size: 13px; }
      .dialog-scrim {
        position: fixed;
        z-index: 1000;
        inset: 0;
        display: grid;
        place-items: center;
        padding: 20px;
        background: rgba(0, 0, 0, .52);
      }
      .dialog {
        width: min(520px, 100%);
        max-height: min(720px, calc(100vh - 40px));
        overflow: auto;
        border-radius: 28px;
        color: var(--primary-text-color);
        background: var(--card-background-color, #fff);
        box-shadow: 0 12px 42px rgba(0, 0, 0, .28);
      }
      .dialog-heading {
        display: flex;
        align-items: flex-start;
        justify-content: space-between;
        gap: 16px;
        padding: 24px 24px 14px;
      }
      .eyebrow {
        display: block;
        margin-bottom: 5px;
        color: var(--scheduler-accent);
        font-size: 11px;
        font-weight: 600;
        letter-spacing: .8px;
        text-transform: uppercase;
      }
      .dialog-heading h2 { font-size: 24px; font-weight: 500; }
      .icon-button {
        display: grid;
        place-items: center;
        width: 40px;
        height: 40px;
        margin: -7px -7px 0 0;
        border: 0;
        border-radius: 50%;
        background: transparent;
        cursor: pointer;
      }
      .icon-button:hover { background: var(--secondary-background-color); }
      .dialog-body { padding: 8px 24px 24px; }
      .field-label { display: block; font-size: 14px; font-weight: 500; }
      .field-help { margin: 4px 0 12px; color: var(--secondary-text-color); font-size: 12px; }
      .entity-picker { display: block; width: 100%; }
      .entity-chips { display: flex; flex-wrap: wrap; gap: 8px; min-height: 30px; margin-top: 12px; }
      .entity-chip {
        display: inline-flex;
        align-items: center;
        gap: 6px;
        max-width: 100%;
        min-height: 32px;
        padding: 3px 4px 3px 10px;
        border-radius: 17px;
        color: var(--primary-text-color);
        background: var(--secondary-background-color);
        font-size: 12px;
      }
      .entity-chip > span { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
      .entity-chip > ha-icon { flex: none; color: var(--secondary-text-color); --mdc-icon-size: 16px; }
      .entity-chip button {
        display: grid;
        place-items: center;
        width: 26px;
        height: 26px;
        padding: 0;
        border: 0;
        border-radius: 50%;
        background: transparent;
        cursor: pointer;
      }
      .entity-chip button:hover { background: var(--divider-color); }
      .entity-chip button ha-icon { --mdc-icon-size: 15px; }
      .selection-hint { color: var(--secondary-text-color); font-size: 12px; font-style: italic; }
      .time-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; margin-top: 26px; }
      .time-field { display: flex; flex-direction: column; gap: 7px; font-size: 14px; font-weight: 500; }
      .time-field input {
        width: 100%;
        height: 52px;
        padding: 0 14px;
        border: 1px solid var(--divider-color);
        border-radius: 12px;
        color: var(--primary-text-color);
        color-scheme: light dark;
        background: transparent;
        font-size: 16px;
      }
      .time-field input:focus { outline: 2px solid var(--scheduler-accent); outline-offset: -1px; }
      .validation-message { min-height: 18px; margin-top: 10px; color: var(--error-color); font-size: 12px; }
      .dialog-actions {
        display: flex;
        align-items: center;
        gap: 8px;
        padding: 12px 20px 20px;
      }
      .action-spacer { flex: 1; }
      .dialog-actions .delete-button { margin-right: auto; }
      .save-button, .secondary-button, .delete-button, .confirm-delete-button {
        min-height: 40px;
        padding: 0 16px;
        border: 0;
        border-radius: 20px;
        font-weight: 500;
        cursor: pointer;
      }
      .save-button { color: var(--text-primary-color, #fff); background: var(--scheduler-accent); }
      .secondary-button { color: var(--scheduler-accent); background: transparent; }
      .secondary-button:hover { background: color-mix(in srgb, var(--scheduler-accent) 10%, transparent); }
      .delete-button {
        display: inline-flex;
        align-items: center;
        gap: 7px;
        padding: 0 12px;
        color: var(--error-color);
        background: transparent;
      }
      .delete-button:hover { background: color-mix(in srgb, var(--error-color) 10%, transparent); }
      .delete-button ha-icon { --mdc-icon-size: 19px; }
      button:disabled { opacity: .55; cursor: default; }
      .confirm-dialog { width: min(400px, 100%); padding: 28px; text-align: center; }
      .confirm-icon {
        display: grid;
        place-items: center;
        width: 52px;
        height: 52px;
        margin: 0 auto 16px;
        border-radius: 50%;
        color: var(--error-color);
        background: color-mix(in srgb, var(--error-color) 12%, transparent);
      }
      .confirm-dialog h2 { font-size: 22px; font-weight: 500; }
      .confirm-dialog p { margin: 9px auto 24px; color: var(--secondary-text-color); font-size: 14px; line-height: 1.45; }
      .confirm-actions { display: flex; justify-content: center; gap: 8px; }
      .confirm-delete-button { color: #fff; background: var(--error-color); }
      @media (max-width: 480px) {
        .card-header { padding: 20px 18px 12px; }
        h1 { font-size: 21px; }
        .enabled-heading { display: none; }
        .schedule-list { padding-inline: 6px; }
        .schedule-row { grid-template-columns: minmax(0, 1fr) 54px; }
        .schedule-main { gap: 12px; padding: 10px; }
        .schedule-icon { flex-basis: 38px; width: 38px; height: 38px; }
        .dialog-scrim { align-items: end; padding: 0; }
        .dialog { width: 100%; max-height: 92vh; border-radius: 28px 28px 0 0; }
        .time-grid { grid-template-columns: 1fr; gap: 14px; }
        .dialog-actions { position: sticky; bottom: 0; background: var(--card-background-color, #fff); }
        .confirm-dialog { width: 100%; border-radius: 28px 28px 0 0; }
      }
    `;
  }
}

class PlantLightSchedulerCardEditor extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._config = {};
  }

  set hass(hass) {
    this._hass = hass;
  }

  setConfig(config) {
    this._config = { ...config };
    this._render();
  }

  _render() {
    this.shadowRoot.innerHTML = `
      <style>
        :host { display: block; padding: 8px 0; }
        label { display: flex; flex-direction: column; gap: 8px; color: var(--primary-text-color); font-size: 14px; }
        input {
          height: 48px;
          padding: 0 14px;
          border: 1px solid var(--divider-color);
          border-radius: 10px;
          color: var(--primary-text-color);
          background: transparent;
          font: inherit;
        }
        input:focus { outline: 2px solid var(--primary-color); outline-offset: -1px; }
      </style>
      <label>Card title
        <input value="${escapeHtml(
          this._config.title || "Plant Light Scheduler"
        )}" placeholder="Plant Light Scheduler">
      </label>`;
    this.shadowRoot.querySelector("input").addEventListener("input", (event) => {
      const title = event.target.value.trim();
      const config = { ...this._config };
      if (title && title !== "Plant Light Scheduler") config.title = title;
      else delete config.title;
      this.dispatchEvent(
        new CustomEvent("config-changed", {
          bubbles: true,
          composed: true,
          detail: { config },
        })
      );
    });
  }
}

if (!customElements.get("plant-light-scheduler-card")) {
  customElements.define("plant-light-scheduler-card", PlantLightSchedulerCard);
}
if (!customElements.get("plant-light-scheduler-card-editor")) {
  customElements.define(
    "plant-light-scheduler-card-editor",
    PlantLightSchedulerCardEditor
  );
}

window.customCards = window.customCards || [];
if (!window.customCards.some((card) => card.type === "plant-light-scheduler-card")) {
  window.customCards.push({
    type: "plant-light-scheduler-card",
    name: "Plant Light Scheduler",
    description: "Create and manage daily schedules for plant-light switches.",
    preview: true,
  });
}

console.info(
  `%c PLANT LIGHT SCHEDULER %c v${CARD_VERSION} `,
  "color: white; background: #03a9d9; font-weight: 600;",
  "color: #03a9d9; background: transparent;"
);
