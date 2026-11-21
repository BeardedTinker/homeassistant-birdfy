// Bundled Birdfy card. Moments and temporary media URLs are requested only
// when the user opens the feed and remain in this card instance's memory.

const CARD_VERSION = "1.2.3";
const CACHE_TTL_MS = 60_000;

const I18N = {
  en: {
    title: "Birdfy Moments",
    open: "Moments Feed",
    loading: "Loading Moments…",
    select_device: "Select a Birdfy feeder in the card settings.",
    empty: "No recent Moments found.",
    failed: "Could not load Birdfy Moments.",
    retry: "Try again",
    close: "Close",
    all: "All",
    open_image: "Open Image",
    open_video: "Open Video",
    play: "Play",
    moments: "Moments",
    birds: "Bird Visits",
    species: "Species",
    latest_alt: "Latest Birdfy Moment",
    snapshot_alt: "Birdfy Moment snapshot",
    motion: "Motion",
    no_media: "No media is available for this Moment.",
    filter_label: "Filter Moments by species",
    f_device_id: "Birdfy feeder",
    f_title: "Title",
    f_limit: "Maximum Moments",
    f_moments_entity: "Moments Today entity",
    f_visits_entity: "Bird Visits Today entity",
    f_species_count_entity: "Species Today entity",
    f_latest_species_entity: "Last Moment Species entity",
    f_latest_image_entity: "Last Moment image entity",
    today: "Today",
  },
  hr: {
    title: "Birdfy trenuci",
    open: "Pregled trenutaka",
    loading: "Učitavanje trenutaka…",
    select_device: "Odaberite Birdfy hranilicu u postavkama kartice.",
    empty: "Nema nedavnih trenutaka.",
    failed: "Birdfy trenuci nisu se mogli učitati.",
    retry: "Pokušaj ponovno",
    close: "Zatvori",
    all: "Sve",
    open_image: "Otvori sliku",
    open_video: "Otvori video",
    play: "Pokreni",
    moments: "Trenuci",
    birds: "Dolasci ptica",
    species: "Vrste",
    latest_alt: "Posljednji Birdfy trenutak",
    snapshot_alt: "Snimka Birdfy trenutka",
    motion: "Pokret",
    no_media: "Za ovaj trenutak nema dostupnog medija.",
    filter_label: "Filtriraj trenutke prema vrsti",
    f_device_id: "Birdfy hranilica",
    f_title: "Naslov",
    f_limit: "Najveći broj trenutaka",
    f_moments_entity: "Entitet današnjih trenutaka",
    f_visits_entity: "Entitet današnjih dolazaka ptica",
    f_species_count_entity: "Entitet današnjih vrsta",
    f_latest_species_entity: "Entitet vrste posljednjeg trenutka",
    f_latest_image_entity: "Entitet slike posljednjeg trenutka",
    today: "Danas",
  },
};

class BirdfyMomentsCard extends HTMLElement {
  #hass = null;
  #config = {};
  #feed = null;
  #loading = false;
  #error = null;
  #loadedAt = 0;
  #requestId = 0;
  #selectedIndex = 0;
  #selectedSpecies = "all";
  #rendered = false;
  #lastFeedSignature = "";

  constructor() {
    super();
    this.attachShadow({ mode: "open" });
  }

  setConfig(config) {
    if (!config || typeof config !== "object") {
      throw new Error("Invalid Birdfy card configuration");
    }
    const limit = Number(config.limit ?? 20);
    this.#config = {
      ...config,
      limit: Number.isFinite(limit) ? Math.min(20, Math.max(1, limit)) : 20,
    };
    this.#feed = null;
    this.#error = null;
    this.#loadedAt = 0;
    this.#requestId += 1;
    this.#render();
  }

  set hass(hass) {
    this.#hass = hass;
    if (!this.#rendered) {
      this.#render();
      return;
    }
    this.#updateContent();
  }

  get hass() {
    return this.#hass;
  }

  connectedCallback() {
    if (!this.#rendered) {
      this.#render();
    }
  }

  disconnectedCallback() {
    this.#requestId += 1;
    const dialog = this.shadowRoot?.querySelector("dialog");
    if (dialog?.open) {
      dialog.close();
    }
  }

  getCardSize() {
    return 4;
  }

  getGridOptions() {
    return {
      columns: 12,
      rows: 5,
      min_columns: 6,
      min_rows: 3,
    };
  }

  static getConfigForm() {
    const language = (
      document.querySelector("home-assistant")?.hass?.locale?.language || "en"
    ).slice(0, 2);
    const translations = I18N[language] || I18N.en;
    return {
      schema: [
        {
          name: "device_id",
          required: true,
          selector: { device: { integration: "birdfy" } },
        },
        { name: "title", selector: { text: {} } },
        {
          name: "limit",
          selector: { number: { min: 1, max: 20, mode: "box" } },
        },
        { name: "moments_entity", selector: { entity: {} } },
        { name: "visits_entity", selector: { entity: {} } },
        { name: "species_count_entity", selector: { entity: {} } },
        { name: "latest_species_entity", selector: { entity: {} } },
        { name: "latest_image_entity", selector: { entity: {} } },
      ],
      computeLabel: (schema) => translations[`f_${schema.name}`],
    };
  }

  static getStubConfig() {
    return { limit: 20 };
  }

  get #t() {
    const language = (this.#hass?.locale?.language || "en").slice(0, 2);
    return I18N[language] || I18N.en;
  }

  #formatTime(epochMs) {
    if (!epochMs) return "";
    const numeric = Number(epochMs);
    const dt = new Date(Number.isFinite(numeric) ? numeric : epochMs);
    if (Number.isNaN(dt.getTime())) return "";
    const now = new Date();
    const isToday = dt.toDateString() === now.toDateString();
    const timeStr = dt.toLocaleTimeString([], {
      hour: "numeric",
      minute: "2-digit",
    });
    if (isToday) {
      return `${this.#t.today}, ${timeStr}`;
    }
    const dateStr = dt.toLocaleDateString([], {
      month: "short",
      day: "numeric",
    });
    return `${dateStr}, ${timeStr}`;
  }

  #getSummaryEntity(configKey) {
    const entityId = this.#config[configKey];
    return entityId ? this.#hass?.states?.[entityId] || null : null;
  }

  #getSummaryValue(configKey, fallback = null) {
    const state = this.#getSummaryEntity(configKey)?.state;
    return state && !["unknown", "unavailable"].includes(state)
      ? state
      : fallback;
  }

  #getFilteredMoments(moments) {
    if (this.#selectedSpecies === "all") {
      return moments;
    }
    const target = this.#selectedSpecies.toLowerCase();
    return moments.filter((m) => {
      const primary = String(m.species || "").toLowerCase();
      const labels = Array.isArray(m.ai_labels)
        ? m.ai_labels.map((l) => String(l).toLowerCase())
        : [];
      return (
        primary.includes(target) || labels.some((l) => l.includes(target))
      );
    });
  }

  async #loadFeed(force = false) {
    if (!this.#hass || !this.#config.device_id) return false;
    if (
      !force &&
      this.#feed &&
      Date.now() - this.#loadedAt < CACHE_TTL_MS
    ) {
      return true;
    }

    const requestId = ++this.#requestId;
    this.#loading = true;
    this.#error = null;
    this.#updateContent();
    try {
      const result = await this.#hass.callWS({
        type: "call_service",
        domain: "birdfy",
        service: "get_moments",
        service_data: {
          device_id: this.#config.device_id,
          limit: this.#config.limit,
        },
        return_response: true,
      });
      if (requestId !== this.#requestId) return false;
      const response = result?.response;
      if (!response || !Array.isArray(response.moments)) {
        throw new Error("Invalid Birdfy response");
      }
      this.#feed = response;
      this.#loadedAt = Date.now();
      this.#selectedSpecies = "all";
      this.#selectedIndex = 0;
      return true;
    } catch (error) {
      if (requestId !== this.#requestId) return false;
      this.#error = error;
      return false;
    } finally {
      if (requestId === this.#requestId) {
        this.#loading = false;
        this.#updateContent();
      }
    }
  }

  async #openModal(initialIndex = 0) {
    if (!this.#config.device_id) return;
    this.#selectedIndex = initialIndex;
    const dialog = this.shadowRoot?.querySelector("#moments-dialog");
    if (!dialog) return;
    this.#renderModalBody();
    if (!dialog.open) {
      dialog.showModal();
    }
    if (await this.#loadFeed()) {
      this.#renderModalBody();
    }
  }

  #stopVideoPlayback() {
    const video = this.shadowRoot?.querySelector("#modal-video-player");
    if (video) {
      video.pause();
      video.removeAttribute("src");
      video.load();
    }
  }

  #render() {
    if (!this.shadowRoot) return;
    const t = this.#t;

    this.shadowRoot.innerHTML = `
      <style>
        :host {
          display: block;
          --birdfy-accent: var(--primary-color, #00b894);
          --birdfy-bg: var(--ha-card-background, var(--card-background-color, #1c1c1e));
          --birdfy-text: var(--primary-text-color, #f5f5f7);
          --birdfy-muted: var(--secondary-text-color, #9a9aa6);
          --birdfy-surface: rgba(255, 255, 255, 0.06);
          --birdfy-border: rgba(255, 255, 255, 0.12);
        }

        ha-card {
          overflow: hidden;
          position: relative;
          background: var(--birdfy-bg);
          color: var(--birdfy-text);
          border-radius: var(--ha-card-border-radius, 12px);
        }

        .hero-button {
          appearance: none;
          border: none;
          padding: 0;
          margin: 0;
          width: 100%;
          display: block;
          position: relative;
          background: #0d0d10;
          cursor: pointer;
          text-align: left;
          color: inherit;
          aspect-ratio: 16 / 9;
          overflow: hidden;
        }

        .hero-button:focus-visible {
          outline: 2px solid var(--birdfy-accent);
          outline-offset: -2px;
        }

        .hero-img {
          width: 100%;
          height: 100%;
          object-fit: cover;
          display: block;
          transition: transform 0.25s ease, opacity 0.2s ease;
        }

        .hero-img:not([src]),
        .hero-img[src=""] {
          opacity: 0;
        }

        .hero-button:hover .hero-img {
          transform: scale(1.02);
        }

        .hero-overlay {
          position: absolute;
          inset: 0;
          background: linear-gradient(
            180deg,
            rgba(0, 0, 0, 0.45) 0%,
            rgba(0, 0, 0, 0) 35%,
            rgba(0, 0, 0, 0.78) 100%
          );
          display: flex;
          flex-direction: column;
          justify-content: space-between;
          padding: 14px 16px;
        }

        .hero-top {
          display: flex;
          justify-content: space-between;
          align-items: center;
          gap: 8px;
        }

        .card-title {
          font-size: 0.95rem;
          font-weight: 600;
          color: #fff;
          text-shadow: 0 1px 3px rgba(0, 0, 0, 0.6);
        }

        .expand-pill {
          background: rgba(0, 0, 0, 0.55);
          backdrop-filter: blur(8px);
          color: #fff;
          padding: 5px 10px;
          border-radius: 999px;
          font-size: 0.78rem;
          font-weight: 600;
          display: inline-flex;
          align-items: center;
          gap: 5px;
          border: 1px solid rgba(255, 255, 255, 0.2);
        }

        .hero-bottom {
          display: flex;
          justify-content: space-between;
          align-items: flex-end;
          gap: 12px;
        }

        .latest-species {
          font-size: 1.15rem;
          font-weight: 700;
          color: #fff;
          text-shadow: 0 1px 4px rgba(0, 0, 0, 0.75);
        }

        .latest-time {
          font-size: 0.8rem;
          color: rgba(255, 255, 255, 0.82);
          margin-top: 2px;
        }

        .stats-bar {
          display: grid;
          grid-template-columns: repeat(3, 1fr);
          gap: 8px;
          padding: 12px 16px;
          border-top: 1px solid var(--birdfy-border);
        }

        .stat-item {
          display: flex;
          flex-direction: column;
          align-items: center;
          padding: 6px 8px;
          background: var(--birdfy-surface);
          border-radius: 8px;
        }

        .stat-value {
          font-size: 1.05rem;
          font-weight: 700;
          color: var(--birdfy-text);
        }

        .stat-label {
          font-size: 0.72rem;
          color: var(--birdfy-muted);
          text-transform: uppercase;
          letter-spacing: 0.04em;
        }

        /* Native <dialog> Pop-Out Modal */
        dialog#moments-dialog {
          width: min(1040px, 94vw);
          max-height: 90vh;
          padding: 0;
          border: 1px solid rgba(255, 255, 255, 0.16);
          border-radius: 16px;
          background: #141417;
          color: #f5f5f7;
          box-shadow: 0 24px 64px rgba(0, 0, 0, 0.65);
          overflow: hidden;
        }

        dialog#moments-dialog[open] {
          display: flex;
          flex-direction: column;
        }

        dialog#moments-dialog::backdrop {
          background-color: rgba(0, 0, 0, 0.72);
          backdrop-filter: blur(4px);
        }

        .modal-header {
          display: flex;
          justify-content: space-between;
          align-items: center;
          padding: 14px 20px;
          border-bottom: 1px solid rgba(255, 255, 255, 0.1);
          background: #1c1c21;
        }

        .modal-header h2 {
          margin: 0;
          font-size: 1.1rem;
          font-weight: 700;
        }

        .close-btn {
          appearance: none;
          border: 1px solid rgba(255, 255, 255, 0.18);
          background: rgba(255, 255, 255, 0.08);
          color: #fff;
          border-radius: 8px;
          padding: 6px 12px;
          font-size: 0.85rem;
          font-weight: 600;
          cursor: pointer;
        }

        .close-btn:hover {
          background: rgba(255, 255, 255, 0.16);
        }

        .species-nav {
          display: flex;
          gap: 8px;
          padding: 10px 20px;
          overflow-x: auto;
          border-bottom: 1px solid rgba(255, 255, 255, 0.08);
          background: #18181c;
          scrollbar-width: thin;
        }

        .filter-pill {
          appearance: none;
          border: 1px solid rgba(255, 255, 255, 0.14);
          background: rgba(255, 255, 255, 0.06);
          color: #e5e5ea;
          border-radius: 999px;
          padding: 5px 12px;
          font-size: 0.8rem;
          font-weight: 600;
          white-space: nowrap;
          cursor: pointer;
        }

        .filter-pill[aria-pressed="true"] {
          background: var(--birdfy-accent);
          border-color: var(--birdfy-accent);
          color: #fff;
        }

        .modal-body {
          display: grid;
          grid-template-columns: 1.45fr 1fr;
          min-height: 0;
          flex: 1;
          overflow: hidden;
        }

        @media (max-width: 780px) {
          .modal-body {
            grid-template-columns: 1fr;
            overflow-y: auto;
          }
        }

        .stage-pane {
          padding: 18px 20px;
          display: flex;
          flex-direction: column;
          gap: 12px;
          border-right: 1px solid rgba(255, 255, 255, 0.08);
          background: #0f0f12;
        }

        .stage-media-wrap {
          width: 100%;
          aspect-ratio: 16 / 9;
          background: #000;
          border-radius: 12px;
          overflow: hidden;
          display: flex;
          align-items: center;
          justify-content: center;
        }

        .stage-media-wrap video,
        .stage-media-wrap img {
          width: 100%;
          height: 100%;
          object-fit: contain;
          display: block;
        }

        .stage-meta {
          display: flex;
          justify-content: space-between;
          align-items: flex-start;
          gap: 12px;
          flex-wrap: wrap;
        }

        .stage-species {
          font-size: 1.2rem;
          font-weight: 700;
        }

        .stage-time {
          font-size: 0.85rem;
          color: #9a9aa6;
          margin-top: 2px;
        }

        .stage-links {
          display: flex;
          gap: 8px;
        }

        .stage-link {
          text-decoration: none;
          background: rgba(255, 255, 255, 0.1);
          color: #fff;
          padding: 6px 12px;
          border-radius: 8px;
          font-size: 0.8rem;
          font-weight: 600;
        }

        .feed-list {
          list-style: none;
          margin: 0;
          padding: 12px;
          overflow-y: auto;
          display: flex;
          flex-direction: column;
          gap: 8px;
          max-height: 68vh;
        }

        .feed-item-btn {
          appearance: none;
          width: 100%;
          border: 1px solid rgba(255, 255, 255, 0.08);
          background: rgba(255, 255, 255, 0.04);
          color: inherit;
          border-radius: 10px;
          padding: 8px;
          display: grid;
          grid-template-columns: 112px 1fr;
          gap: 12px;
          align-items: center;
          text-align: left;
          cursor: pointer;
          transition: background 0.15s ease, border-color 0.15s ease;
        }

        .feed-item-btn:hover {
          background: rgba(255, 255, 255, 0.09);
        }

        .feed-item-btn[aria-current="true"] {
          border-color: var(--birdfy-accent);
          background: rgba(0, 184, 148, 0.14);
        }

        .feed-thumb-wrap {
          position: relative;
          width: 112px;
          aspect-ratio: 16 / 9;
          border-radius: 7px;
          overflow: hidden;
          background: #000;
        }

        .feed-thumb-wrap img {
          width: 100%;
          height: 100%;
          object-fit: cover;
          display: block;
        }

        .play-badge {
          position: absolute;
          bottom: 4px;
          right: 4px;
          background: rgba(0, 0, 0, 0.72);
          color: #fff;
          font-size: 0.68rem;
          padding: 2px 6px;
          border-radius: 4px;
          font-weight: 600;
        }

        .feed-item-species {
          font-size: 0.92rem;
          font-weight: 600;
          color: #fff;
        }

        .feed-item-time {
          font-size: 0.78rem;
          color: #9a9aa6;
          margin-top: 3px;
        }
      </style>

      <ha-card>
        <button type="button" class="hero-button" id="open-modal-btn" aria-haspopup="dialog"
          ${this.#config.device_id ? "" : "disabled"}>
          <img class="hero-img" id="hero-img" alt="${t.latest_alt}" />
          <div class="hero-overlay">
            <div class="hero-top">
              <span class="card-title" id="card-title"></span>
              <span class="expand-pill">⤢ ${t.open}</span>
            </div>
            <div class="hero-bottom">
              <div>
                <div class="latest-species" id="latest-species"></div>
                <div class="latest-time" id="latest-time"></div>
              </div>
            </div>
          </div>
        </button>

        <div class="stats-bar">
          <div class="stat-item">
            <span class="stat-value" id="stat-moments">0</span>
            <span class="stat-label">${t.moments}</span>
          </div>
          <div class="stat-item">
            <span class="stat-value" id="stat-visits">0</span>
            <span class="stat-label">${t.birds}</span>
          </div>
          <div class="stat-item">
            <span class="stat-value" id="stat-species">0</span>
            <span class="stat-label">${t.species}</span>
          </div>
        </div>
      </ha-card>

      <dialog id="moments-dialog" closedby="any" aria-labelledby="birdfy-modal-title">
        <div class="modal-header">
          <h2 id="birdfy-modal-title">${t.title}</h2>
          <button type="button" class="close-btn" id="close-modal-btn">✕ ${t.close}</button>
        </div>
        <nav class="species-nav" id="species-nav" aria-label="${t.filter_label}"></nav>
        <div class="modal-body">
          <div class="stage-pane" id="stage-pane"></div>
          <ul class="feed-list" id="feed-list"></ul>
        </div>
      </dialog>
    `;

    const openBtn = this.shadowRoot.querySelector("#open-modal-btn");
    const closeBtn = this.shadowRoot.querySelector("#close-modal-btn");
    const dialog = this.shadowRoot.querySelector("#moments-dialog");

    openBtn?.addEventListener("click", () => this.#openModal(0));
    closeBtn?.addEventListener("click", () => dialog?.close());
    dialog?.addEventListener("close", () => this.#stopVideoPlayback());

    // Fallback for browsers without declarative <dialog closedby="any"> support (e.g. Safari)
    if (dialog && !("closedBy" in HTMLDialogElement.prototype)) {
      dialog.addEventListener("click", (event) => {
        if (event.target !== dialog) return;
        const rect = dialog.getBoundingClientRect();
        const isDialogContent =
          rect.top <= event.clientY &&
          event.clientY <= rect.top + rect.height &&
          rect.left <= event.clientX &&
          event.clientX <= rect.left + rect.width;
        if (isDialogContent) return;
        dialog.close();
      });
    }

    this.#rendered = true;
    this.#updateContent();
  }

  #updateContent() {
    if (!this.#rendered || !this.shadowRoot) return;
    const titleEl = this.shadowRoot.querySelector("#card-title");
    const speciesEl = this.shadowRoot.querySelector("#latest-species");
    const timeEl = this.shadowRoot.querySelector("#latest-time");
    const heroImg = this.shadowRoot.querySelector("#hero-img");
    const statMoments = this.shadowRoot.querySelector("#stat-moments");
    const statVisits = this.shadowRoot.querySelector("#stat-visits");
    const statSpecies = this.shadowRoot.querySelector("#stat-species");

    const title =
      this.#config.title ||
      this.#feed?.device_name ||
      this.#t.title;
    if (titleEl) titleEl.textContent = title;

    const moments = Array.isArray(this.#feed?.moments)
      ? this.#feed.moments
      : [];
    const latest = moments[0] || null;
    const summarySpeciesEntity = this.#getSummaryEntity(
      "latest_species_entity"
    );
    const summaryImageEntity = this.#getSummaryEntity("latest_image_entity");
    const summarySpecies = this.#getSummaryValue("latest_species_entity");
    const summaryTime =
      summarySpeciesEntity?.attributes?.detection_time ||
      summaryImageEntity?.attributes?.detection_time;

    if (speciesEl) {
      speciesEl.textContent = !this.#config.device_id
        ? this.#t.select_device
        : this.#loading && !this.#feed
          ? this.#t.loading
          : this.#error && !this.#feed
            ? this.#t.failed
            : latest?.species ||
              summarySpecies ||
              (this.#feed ? this.#t.empty : this.#t.open);
    }
    if (timeEl) {
      timeEl.textContent = this.#formatTime(latest?.time || summaryTime);
    }

    const summaryImgUrl = summaryImageEntity?.attributes?.detection_time
      ? summaryImageEntity.attributes.entity_picture
      : "";
    const imgUrl = latest?.image_url || summaryImgUrl || "";
    if (heroImg && imgUrl && heroImg.getAttribute("src") !== imgUrl) {
      heroImg.setAttribute("src", imgUrl);
    } else if (heroImg && !imgUrl) {
      heroImg.removeAttribute("src");
    }

    if (statMoments) {
      statMoments.textContent = String(
        this.#feed?.total_moments ??
          this.#getSummaryValue("moments_entity", moments.length)
      );
    }
    if (statVisits) {
      statVisits.textContent = String(
        this.#feed?.bird_visits ??
          this.#getSummaryValue("visits_entity", 0)
      );
    }
    if (statSpecies) {
      statSpecies.textContent = String(
        this.#feed?.species_count ??
          this.#getSummaryValue("species_count_entity", 0)
      );
    }

    const dialog = this.shadowRoot.querySelector("#moments-dialog");
    const sig = `${latest?.alarm_id || ""}:${moments.length}:${this.#loading}:${Boolean(this.#error)}`;
    if (dialog?.open && sig !== this.#lastFeedSignature) {
      this.#lastFeedSignature = sig;
      this.#renderModalBody();
    }
  }

  #renderModalBody() {
    if (!this.shadowRoot) return;
    const allMoments = Array.isArray(this.#feed?.moments)
      ? this.#feed.moments
      : [];

    const navEl = this.shadowRoot.querySelector("#species-nav");
    const stageEl = this.shadowRoot.querySelector("#stage-pane");
    const listEl = this.shadowRoot.querySelector("#feed-list");
    if (!navEl || !stageEl || !listEl) return;
    navEl.innerHTML = "";
    stageEl.innerHTML = "";
    listEl.innerHTML = "";

    if (this.#loading && !this.#feed) {
      stageEl.textContent = this.#t.loading;
      return;
    }
    if (this.#error && !this.#feed) {
      const message = document.createElement("div");
      message.textContent = this.#t.failed;
      const retry = document.createElement("button");
      retry.type = "button";
      retry.className = "close-btn";
      retry.textContent = this.#t.retry;
      retry.addEventListener("click", async () => {
        if (await this.#loadFeed(true)) this.#renderModalBody();
      });
      stageEl.append(message, retry);
      return;
    }
    if (!this.#config.device_id) {
      stageEl.textContent = this.#t.select_device;
      return;
    }
    if (!allMoments.length) {
      stageEl.textContent = this.#t.empty;
      return;
    }

    const speciesCounts = {};
    for (const moment of allMoments) {
      const species = String(moment.species || "").trim();
      if (species) speciesCounts[species] = (speciesCounts[species] || 0) + 1;
    }

    if (navEl) {
      const pills = [
        { key: "all", label: `${this.#t.all} (${allMoments.length})` },
        ...Object.entries(speciesCounts).map(([sp, cnt]) => ({
          key: sp,
          label: `${sp} (${cnt})`,
        })),
      ];
      for (const pill of pills) {
        const btn = document.createElement("button");
        btn.type = "button";
        btn.className = "filter-pill";
        btn.setAttribute(
          "aria-pressed",
          String(this.#selectedSpecies === pill.key)
        );
        btn.textContent = pill.label;
        btn.addEventListener("click", () => {
          this.#selectedSpecies = pill.key;
          this.#selectedIndex = 0;
          this.#renderModalBody();
        });
        navEl.appendChild(btn);
      }
    }

    const filtered = this.#getFilteredMoments(allMoments);
    if (this.#selectedIndex >= filtered.length) {
      this.#selectedIndex = 0;
    }
    const activeMoment = filtered[this.#selectedIndex] || null;

    if (stageEl) {
      if (!activeMoment) {
        const empty = document.createElement("div");
        empty.textContent = this.#t.empty;
        stageEl.appendChild(empty);
      } else {
        const mediaWrap = document.createElement("div");
        mediaWrap.className = "stage-media-wrap";

        if (activeMoment.video_url) {
          const video = document.createElement("video");
          video.id = "modal-video-player";
          video.controls = true;
          video.autoplay = true;
          video.playsInline = true;
          if (activeMoment.image_url) {
            video.poster = activeMoment.image_url;
          }
          video.src = activeMoment.video_url;
          mediaWrap.appendChild(video);
        } else if (activeMoment.image_url) {
          const img = document.createElement("img");
          img.src = activeMoment.image_url;
          img.alt = activeMoment.species || this.#t.snapshot_alt;
          mediaWrap.appendChild(img);
        } else {
          mediaWrap.textContent = this.#t.no_media;
        }

        const meta = document.createElement("div");
        meta.className = "stage-meta";

        const info = document.createElement("div");
        const spTitle = document.createElement("div");
        spTitle.className = "stage-species";
        spTitle.textContent = activeMoment.species || this.#t.motion;

        const tmSub = document.createElement("div");
        tmSub.className = "stage-time";
        tmSub.textContent = this.#formatTime(activeMoment.time);
        info.append(spTitle, tmSub);

        const links = document.createElement("div");
        links.className = "stage-links";
        if (activeMoment.video_url) {
          const aVid = document.createElement("a");
          aVid.className = "stage-link";
          aVid.href = activeMoment.video_url;
          aVid.target = "_blank";
          aVid.rel = "noopener noreferrer";
          aVid.textContent = `▶ ${this.#t.open_video}`;
          links.appendChild(aVid);
        }
        if (activeMoment.image_url) {
          const aImg = document.createElement("a");
          aImg.className = "stage-link";
          aImg.href = activeMoment.image_url;
          aImg.target = "_blank";
          aImg.rel = "noopener noreferrer";
          aImg.textContent = `🖼 ${this.#t.open_image}`;
          links.appendChild(aImg);
        }

        meta.append(info, links);
        stageEl.append(mediaWrap, meta);
      }
    }

    if (listEl) {
      filtered.forEach((moment, idx) => {
        const li = document.createElement("li");
        const btn = document.createElement("button");
        btn.type = "button";
        btn.className = "feed-item-btn";
        btn.setAttribute("aria-current", String(idx === this.#selectedIndex));

        const thumbWrap = document.createElement("div");
        thumbWrap.className = "feed-thumb-wrap";
        if (moment.image_url) {
          const img = document.createElement("img");
          img.src = moment.image_url;
          img.loading = "lazy";
          img.alt = moment.species || "Moment thumbnail";
          thumbWrap.appendChild(img);
        }
        if (moment.video_url) {
          const badge = document.createElement("span");
          badge.className = "play-badge";
          badge.textContent = `▶ ${this.#t.play}`;
          thumbWrap.appendChild(badge);
        }

        const textWrap = document.createElement("div");
        const sp = document.createElement("div");
        sp.className = "feed-item-species";
        sp.textContent = moment.species || this.#t.motion;

        const tm = document.createElement("div");
        tm.className = "feed-item-time";
        tm.textContent = this.#formatTime(moment.time);

        textWrap.append(sp, tm);
        btn.append(thumbWrap, textWrap);
        btn.addEventListener("click", () => {
          this.#stopVideoPlayback();
          this.#selectedIndex = idx;
          this.#renderModalBody();
        });

        li.appendChild(btn);
        listEl.appendChild(li);
      });
    }
  }
}

if (!customElements.get("birdfy-moments-card")) {
  customElements.define("birdfy-moments-card", BirdfyMomentsCard);
}

window.customCards = window.customCards || [];
if (!window.customCards.some((c) => c.type === "birdfy-moments-card")) {
  window.customCards.push({
    type: "birdfy-moments-card",
    name: "Birdfy Moments Card",
    description:
      "On-demand Birdfy Moments browser with snapshots and video playback.",
    documentationURL:
      "https://github.com/BeardedTinker/homeassistant-birdfy",
    preview: true,
  });
}

console.info(
  `%c BIRDFY-MOMENTS-CARD %c ${CARD_VERSION} `,
  "background:#00b894;color:#fff;border-radius:4px 0 0 4px;padding:2px 6px",
  "background:#eee;color:#333;border-radius:0 4px 4px 0;padding:2px 6px",
);
