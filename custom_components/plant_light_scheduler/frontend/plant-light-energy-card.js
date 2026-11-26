const PREFIX = "plant_light_scheduler/energy";
const CARD_NAME = "Plant Light Energy";
const POLL_MS = 10_000;

const escapeHtml = (value) =>
  String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");

class PlantLightEnergyCard extends HTMLElement {
  static getStubConfig() {
    return {};
  }

  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._config = {};
    this._request = 0;
    this._busy = false;
    this._refreshing = false;
  }

  setConfig(config) {
    this._config = config;
    if (this._ready) this._update();
  }

  set hass(hass) {
    const first = !this._hass;
    this._hass = hass;
    if (this.isConnected && first) this._refresh();
  }

  getCardSize() {
    return Math.max(4, 3 + (this._report?.rows.length ?? 0));
  }

  connectedCallback() {
    if (!this._ready) this._render();
    this._refresh();
    this._timer = setInterval(() => this._refresh(), POLL_MS);
  }

  disconnectedCallback() {
    clearInterval(this._timer);
    this._request++;
    this._refreshing = false;
  }

  get _admin() {
    return this._hass?.user?.is_admin === true;
  }

  _send(command, fields = {}) {
    return this._hass.connection.sendMessagePromise({
      type: `${PREFIX}/${command}`,
      ...fields,
    });
  }

  async _refresh(force = false) {
    if (!this._hass || (!force && (this._refreshing || this._busy))) return;
    this._refreshing = true;
    const request = ++this._request;
    try {
      const report = await this._send("get");
      if (request !== this._request) return;
      this._report = report;
      this._error = "";
    } catch (error) {
      if (request !== this._request) return;
      this._error = error?.message || "Could not load energy readings.";
    } finally {
      if (request === this._request) {
        this._refreshing = false;
        this._update();
      }
    }
  }

  _render() {
    this.shadowRoot.innerHTML = `
      <style>${this._styles()}</style>
      <ha-card>
        <div class="header">
          <h2></h2>
          <p class="rate"></p>
        </div>
        <div class="report"></div>
        <div class="actions" hidden>
          <button type="button" data-action="settings">Energy settings</button>
          <button type="button" class="danger" data-action="reset">Reset averages</button>
        </div>
      </ha-card>
      <dialog aria-labelledby="energy-dialog-title"></dialog>`;
    this.shadowRoot.querySelector('[data-action="settings"]').addEventListener("click", () => this._openSettings());
    this.shadowRoot.querySelector('[data-action="reset"]').addEventListener("click", () => this._confirmReset());
    this.shadowRoot.querySelector("dialog").addEventListener("cancel", (event) => {
      if (this._busy) event.preventDefault();
    });
    this._ready = true;
    this._update();
  }

  _format(value, decimals = 2) {
    return new Intl.NumberFormat(this._hass?.locale?.language, {
      maximumFractionDigits: decimals,
    }).format(value);
  }

  _money(value, decimals) {
    return new Intl.NumberFormat(this._hass?.locale?.language, {
      style: "currency",
      currency: this._report.currency,
      ...(decimals === undefined ? {} : { maximumFractionDigits: decimals }),
    }).format(value);
  }

  _label(entityId) {
    return this._hass?.states?.[entityId]?.attributes?.friendly_name || entityId;
  }

  _cell(row, field, unit, partial) {
    const value = row[field];
    const isEnergy = field !== "watts";
    const amount = value === null ? "—" : `${this._format(value)} ${unit}`;
    const cost = isEnergy && value !== null && this._report.rate !== null
      ? `<small>${escapeHtml(this._money(value * this._report.rate))}</small>` : "";
    const extra = isEnergy && row.warming_up ? "Collecting data" : partial ? "Partial" : "";
    return `<td>${escapeHtml(amount)}${cost}${extra ? `<small>${extra}</small>` : ""}</td>`;
  }

  _row(row) {
    const names = row.switches?.map((id) =>
      this._hass?.states?.[id]?.attributes?.friendly_name
        || id.replace(/^switch\./, "").replaceAll("_", " ")
    );
    const name = row.name || names?.join(", ") || "Total";
    return `<tr><th scope="row">${escapeHtml(name)}</th>
      ${this._cell(row, "watts", "W", row.power_partial)}
      ${this._cell(row, "daily_kwh", "kWh/day", row.energy_partial)}
      ${this._cell(row, "monthly_kwh", "kWh", row.energy_partial)}</tr>`;
  }

  _update() {
    this.shadowRoot.querySelector("h2").textContent = this._config.title || CARD_NAME;
    const rate = this.shadowRoot.querySelector(".rate");
    rate.textContent = this._report && this._report.rate !== null
      ? `Energy rate ${this._money(this._report.rate, 4)}/kWh` : "";
    this.shadowRoot.querySelector(".actions").hidden = !this._admin || !this._report;
    const report = this.shadowRoot.querySelector(".report");
    let markup;
    if (this._error) {
      markup = `<p class="message" role="status">${escapeHtml(this._error)}
        <button type="button" data-action="retry">Retry</button></p>`;
    } else if (!this._report) {
      markup = '<p class="message" role="status">Loading energy readings…</p>';
    } else if (!this._report.rows.length) {
      markup = '<p class="message">Add a plant light schedule to see its energy usage.</p>';
    } else {
      markup = `<div class="table-scroll" tabindex="0">
        <table aria-label="Energy by schedule">
          <thead><tr><th scope="col">Schedule</th><th scope="col">Current power</th>
            <th scope="col">Daily average</th><th scope="col">30-day estimate</th></tr></thead>
          <tbody>${this._report.rows.map((row) => this._row(row)).join("")}</tbody>
          <tfoot>${this._row(this._report.totals)}</tfoot>
        </table></div>
        <p class="table-note">Averages appear after 24 hours of tracking. Partial values exclude missing readings.</p>`;
    }
    // Leave the table and its scroll position alone when the report is unchanged.
    if (this._reportMarkup !== markup) {
      const scroll = report.querySelector(".table-scroll")?.scrollLeft ?? 0;
      report.innerHTML = markup;
      this._reportMarkup = markup;
      const scroller = report.querySelector(".table-scroll");
      if (scroller) scroller.scrollLeft = scroll;
      report.querySelector('[data-action="retry"]')?.addEventListener("click", () => this._refresh());
    }
  }

  _showDialog(title, contents) {
    const dialog = this.shadowRoot.querySelector("dialog");
    dialog.innerHTML = `<h2 id="energy-dialog-title">${title}</h2>${contents}
      <p class="dialog-error" role="alert"></p>`;
    dialog.querySelector("[data-close]").addEventListener("click", () => dialog.close());
    dialog.showModal();
    return dialog;
  }

  _openSettings() {
    const switches = Object.keys(this._report?.sources ?? {});
    const dialog = this._showDialog("Energy settings", `
      <form class="rate-form">
        <label>Cost per kWh (${escapeHtml(this._report.currency)})
          <input name="rate" type="number" min="0" step="any" value="${escapeHtml(this._report.rate)}">
        </label>
        <button type="submit">Save rate</button>
      </form>
      ${switches.length ? `<form class="source-form">
        <label>Scheduled switch
          <select name="switch">${switches.map((id) =>
            `<option value="${escapeHtml(id)}">${escapeHtml(this._label(id))}</option>`).join("")}</select>
        </label>
        <div class="meter-fields"></div>
        <button type="submit">Save meter sources</button>
      </form>` : ""}
      <div class="dialog-actions"><button type="button" data-close>Done</button></div>`);
    dialog.querySelector(".rate-form").addEventListener("submit", (event) => {
      event.preventDefault();
      this._change(dialog, "set_rate", { rate: event.currentTarget.elements.rate.value });
    });
    const sources = dialog.querySelector(".source-form");
    if (!sources) return;
    const switchSelect = sources.elements.namedItem("switch");
    const updateMeters = () => this._renderMeterFields(sources, switchSelect.value);
    switchSelect.addEventListener("change", updateMeters);
    updateMeters();
    sources.addEventListener("submit", (event) => {
      event.preventDefault();
      this._change(dialog, "set_sources", {
        switch: switchSelect.value,
        power: sources.elements.power.value,
        energy: sources.elements.energy.value,
      }, updateMeters);
    });
  }

  _renderMeterFields(form, switchId) {
    const settings = this._report.sources[switchId];
    const states = this._hass.states ?? {};
    form.querySelector(".meter-fields").innerHTML = ["power", "energy"].map((kind) => {
      const source = settings?.[kind];
      const override = source?.override;
      const candidates = Object.keys(states).filter((id) => {
        const attributes = states[id].attributes;
        return id.startsWith("sensor.") && attributes.device_class === kind
          && (kind !== "energy" || ["total", "total_increasing"].includes(attributes.state_class));
      });
      if (override && !candidates.includes(override)) candidates.push(override);
      candidates.sort((a, b) => this._label(a).localeCompare(this._label(b)));
      const description = source?.conflict ? "This meter is assigned to multiple schedules."
        : source?.detected ? `Source: ${this._label(source.detected)}` : "Choose a sensor if automatic matching finds none.";
      return `<label>${kind === "power" ? "Live power sensor" : "Cumulative energy sensor"}
        <select name="${kind}"><option value="">Automatic</option>
          ${candidates.map((id) => `<option value="${escapeHtml(id)}" ${id === override ? "selected" : ""}>${escapeHtml(this._label(id))}</option>`).join("")}
        </select><small>${escapeHtml(description)}</small></label>`;
    }).join("");
  }

  _confirmReset() {
    const dialog = this._showDialog("Reset energy averages?", `
      <p>All schedules will start a new 24-hour tracking window. Live power, your energy rate, and the plugs’ counters are kept.</p>
      <div class="dialog-actions">
        <button type="button" data-close autofocus>Cancel</button>
        <button type="button" class="danger" data-confirm>Reset averages</button>
      </div>`);
    dialog.querySelector("[data-confirm]").addEventListener("click", () => {
      this._change(dialog, "reset", {}, () => dialog.close());
    });
  }

  async _change(dialog, command, fields, onSuccess) {
    if (this._busy) return;
    this._busy = true;
    const buttons = dialog.querySelectorAll("button");
    const message = dialog.querySelector(".dialog-error");
    buttons.forEach((button) => { button.disabled = true; });
    message.textContent = "";
    try {
      await this._send(command, fields);
      await this._refresh(true);
      if (!this._error && dialog.open) onSuccess?.();
      if (dialog.open) message.textContent = this._error || "Saved.";
    } catch (error) {
      message.textContent = error?.message || "Could not save energy settings.";
    } finally {
      this._busy = false;
      buttons.forEach((button) => { button.disabled = false; });
    }
  }

  _styles() {
    return `
      :host { display: block; }
      *, *::before, *::after { box-sizing: border-box; }
      ha-card { display: block; overflow: hidden; color: var(--primary-text-color); }
      .header { padding: 20px 20px 10px; }
      h2 { margin: 0; font-size: 22px; font-weight: 500; }
      .rate, .table-note { color: var(--secondary-text-color); font-size: 12px; }
      .rate { margin: 5px 0 0; }
      .rate:empty { display: none; }
      .table-scroll { overflow-x: auto; }
      table { width: 100%; min-width: 360px; border-collapse: collapse; font-size: 14px; }
      th, td { padding: 10px 8px; border-bottom: 1px solid var(--divider-color); }
      th:first-child { padding-inline-start: 20px; text-align: start; overflow-wrap: anywhere; }
      th:last-child, td:last-child { padding-inline-end: 20px; }
      th, td { text-align: end; }
      td { font-variant-numeric: tabular-nums; }
      thead th { font-size: 12px; color: var(--secondary-text-color); }
      tbody th { font-weight: 500; }
      tfoot { font-weight: 600; }
      small { display: block; color: var(--secondary-text-color); font-size: 12px; margin-top: 3px; font-weight: 400; }
      .table-note, .message { margin: 0; padding: 12px 20px; }
      .actions, .dialog-actions { display: flex; flex-wrap: wrap; gap: 8px; padding: 8px 12px 12px; }
      .actions[hidden] { display: none; }
      button { border: 0; background: transparent; color: var(--primary-color); padding: 0 12px;
        min-height: 40px; border-radius: 20px; cursor: pointer; font: inherit; font-weight: 500; }
      button:hover { background: var(--secondary-background-color); }
      button:disabled { opacity: .5; cursor: default; }
      button:focus-visible, input:focus-visible, select:focus-visible {
        outline: 2px solid var(--primary-color); outline-offset: 1px;
      }
      .danger, .dialog-error { color: var(--error-color, #d32f2f); }
      dialog { width: min(460px, calc(100% - 24px)); max-height: calc(100dvh - 24px);
        padding: 22px; border: 1px solid var(--divider-color); border-radius: 20px;
        color: var(--primary-text-color); background: var(--card-background-color, #fff); }
      dialog::backdrop { background: rgba(0, 0, 0, .5); }
      dialog > p { line-height: 1.45; }
      .dialog-actions { justify-content: flex-end; padding: 10px 0 0; }
      form { display: grid; gap: 8px; padding: 12px 0; border-bottom: 1px solid var(--divider-color); }
      form button { justify-self: end; }
      label { display: grid; gap: 5px; font-size: 14px; }
      input, select { width: 100%; height: 42px; min-width: 0; border: 1px solid var(--divider-color);
        border-radius: 8px; padding: 0 10px; background: var(--card-background-color, #fff);
        color: var(--primary-text-color); font: inherit; font-size: 16px; }
      .meter-fields { display: grid; gap: 10px; }
      .dialog-error { margin: 8px 0 0; font-size: 13px; }
      .dialog-error:empty { display: none; }
    `;
  }
}

const type = "plant-light-energy-card";
if (!customElements.get(type)) {
  customElements.define(type, PlantLightEnergyCard);
}
window.customCards = window.customCards || [];
if (!window.customCards.some((card) => card.type === type)) {
  window.customCards.push({
    type,
    name: CARD_NAME,
    description: "Live power, energy averages, and estimated costs by plant-light schedule.",
    preview: true,
  });
}
