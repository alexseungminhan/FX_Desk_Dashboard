// FX Desk Board — WebSocket client + DOM rendering.
// No build step, no framework: the server pushes a full JSON snapshot on
// every poll tick and this file re-renders each panel from it.

(() => {
  "use strict";

  let latest = null;
  let moversTab = "gainers";
  let usMoversTab = "gainers";
  let newsKeywords = [];
  let newsPage = 1;
  const NEWS_PAGE_SIZE = 20;

  const $ = (id) => document.getElementById(id);

  function esc(s) {
    return String(s ?? "").replace(/[&<>"']/g, (c) => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
    }[c]));
  }

  function staleTitle(row) {
    return row.stale ? ' title="마지막 성공한 값 (일시적 갱신 실패)"' : "";
  }

  // -- panel renderers ---------------------------------------------------

  function renderTicker(rows) {
    $("ticker-strip").innerHTML = rows.map((t) => `
      <div style="flex:1;min-width:110px;display:flex;flex-direction:column;gap:1px;padding:8px 14px;border-right:1px solid var(--color-divider)"${staleTitle(t)}>
        <span style="font-size:10px;letter-spacing:.06em;color:#5d5d60;text-transform:uppercase;white-space:nowrap">${esc(t.label)}</span>
        <span class="mono${t.stale ? " stale-dot" : ""}" style="font-size:15px;font-weight:500">${esc(t.price)}</span>
        <span class="mono" style="font-size:11px;color:${t.color}">${t.arrow} ${esc(t.pct)}</span>
      </div>`).join("");
  }

  function fxRowHtml(f, kind) {
    return `
      <div class="fx-row clickable-row" data-kind="${kind}" data-symbol="${esc(f.symbol)}" data-name="${esc(f.name)}"${f.pair ? ` data-pair="${esc(f.pair)}"` : ""}${f.sub ? ` data-sub="${esc(f.sub)}"` : ""}${staleTitle(f)}>
        <span class="fx-pair">${esc(f.pair || f.name)}</span>
        <span class="mono fx-px">${esc(f.price)}</span>
        <span class="mono fx-pct" style="color:${f.color}">${f.arrow} ${esc(f.pct)}</span>
      </div>`;
  }

  function renderFxRegions(regions) {
    regions.forEach((g, i) => {
      const col = $(`fx-col-${i}`);
      if (!col) return;
      col.innerHTML = `<div style="font-size:10px;letter-spacing:.1em;text-transform:uppercase;color:var(--color-accent);padding:4px 0;border-bottom:1px solid var(--color-divider)">${esc(g.label)}</div>`
        + g.rows.map((f) => fxRowHtml(f, "fx")).join("");
    });
  }

  function renderIdxMain(rows) {
    const half = Math.ceil(rows.length / 2);
    $("idx-col-0").innerHTML = rows.slice(0, half).map((r) => fxRowHtml(r, "index")).join("");
    $("idx-col-1").innerHTML = rows.slice(half).map((r) => fxRowHtml(r, "index")).join("");
  }

  function renderRates(rows) {
    $("rates-list").innerHTML = rows.map((rt) => `
      <div class="fx-row clickable-row" data-kind="rate" data-symbol="${esc(rt.symbol)}" data-name="${esc(rt.name)}" data-sub="${esc(rt.sub)}"${staleTitle(rt)}>
        <span class="fx-pair">${esc(rt.name)}</span>
        <span class="mono fx-px">${esc(rt.value)}</span>
        <span class="mono fx-pct" style="color:${rt.color}">${rt.arrow} ${esc(rt.chg)}</span>
      </div>`).join("");
  }

  function renderCommodities(rows) {
    $("commodities-list").innerHTML = rows.map((r) => `
      <div class="fx-row clickable-row" data-kind="commodity" data-symbol="${esc(r.symbol)}" data-name="${esc(r.name)}" data-contract="${esc(r.contract)}"${staleTitle(r)}>
        <span class="fx-pair">${esc(r.name)}</span>
        <span class="mono fx-px">$${esc(r.price)}</span>
        <span class="mono fx-pct" style="color:${r.color}">${r.arrow} ${esc(r.pct)}</span>
      </div>`).join("");
  }

  function renderMovers() {
    if (!latest) return;
    const rows = moversTab === "gainers" ? latest.gainers : latest.losers;
    $("movers-list").innerHTML = rows.map((m) => `
      <div class="mv-row clickable-row" data-symbol="${esc(m.symbol)}" data-name="${esc(m.name.replace(/\*$/, ""))}"${staleTitle(m)}>
        <span class="mv-rank">${m.rank}</span>
        <span class="mv-name">${esc(m.name)}</span>
        <span class="mono mv-px">${esc(m.price)}</span>
        <span class="mono mv-pct" style="color:${m.color}">${esc(m.pct)}</span>
      </div>`).join("");
  }

  function renderKrMostTraded() {
    if (!latest) return;
    $("kr-most-traded-list").innerHTML = latest.krMostTraded.map((m) => `
      <div class="mt-row clickable-row" data-symbol="${esc(m.symbol)}" data-name="${esc(m.name.replace(/\*$/, ""))}"${staleTitle(m)}>
        <span class="mv-rank">${m.rank}</span>
        <span class="mv-name">${esc(m.name)}</span>
        <span class="mono mv-px">${esc(m.price)}</span>
        <span class="mono mt-vol">${esc(m.tradingValue)}</span>
      </div>`).join("");
  }

  function renderUsMovers() {
    if (!latest) return;
    const rows = usMoversTab === "gainers" ? latest.usGainers : latest.usLosers;
    $("us-movers-list").innerHTML = rows.map((m) => `
      <div class="mv-row clickable-row" data-symbol="${esc(m.symbol)}" data-name="${esc(m.fullName)}" title="${esc(m.fullName)}">
        <span class="mv-rank">${m.rank}</span>
        <span class="mv-name">${esc(m.name)}</span>
        <span class="mono mv-px">${esc(m.price)}</span>
        <span class="mono mv-pct" style="color:${m.color}">${esc(m.pct)}</span>
      </div>`).join("");
  }

  function renderUsMostActive() {
    if (!latest) return;
    $("us-most-active-list").innerHTML = latest.usMostActive.map((m) => `
      <div class="mt-row clickable-row" data-symbol="${esc(m.symbol)}" data-name="${esc(m.fullName)}" title="${esc(m.fullName)}">
        <span class="mv-rank">${m.rank}</span>
        <span class="mv-name">${esc(m.name)}</span>
        <span class="mono mv-px">${esc(m.price)}</span>
        <span class="mono mt-vol">${esc(m.volume)}</span>
      </div>`).join("");
  }

  function renderWatchlist(rows) {
    $("watchlist-list").innerHTML = rows.map((r) => `
      <div class="clickable-row" data-symbol="${esc(r.symbol)}" data-name="${esc(r.name)}" style="display:grid;grid-template-columns:1fr auto auto auto;align-items:center;gap:7px;padding:5px 0;border-bottom:1px solid rgba(29,31,32,.05)"${staleTitle(r)}>
        <span style="font-size:12.5px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis">${esc(r.name)}</span>
        <span class="mono" style="font-size:12.5px;text-align:right">${esc(r.price)}</span>
        <span class="mono" style="font-size:12px;color:${r.color};text-align:right;min-width:52px">${r.arrow} ${esc(r.pct)}</span>
        <button data-remove="${esc(r.symbol)}" title="관심종목에서 삭제" style="border:none;background:none;cursor:pointer;color:#98989b;font-size:13px;padding:0 2px;line-height:1">×</button>
      </div>`).join("");

    $("watchlist-list").querySelectorAll("[data-remove]").forEach((btn) => {
      btn.addEventListener("click", async (ev) => {
        ev.stopPropagation();
        btn.disabled = true;
        try {
          await fetch(`/api/watchlist/${encodeURIComponent(btn.dataset.remove)}`, { method: "DELETE" });
        } catch (e) { console.error("remove failed", e); btn.disabled = false; }
      });
    });
  }

  // Clicking any stock row (movers or watchlist) opens the detail popup.
  // Event delegation on the containers survives the innerHTML re-renders.
  for (const containerId of ["movers-list", "kr-most-traded-list", "us-movers-list", "us-most-active-list", "watchlist-list"]) {
    $(containerId).addEventListener("click", (ev) => {
      if (ev.target.closest("button")) return;
      const row = ev.target.closest("[data-symbol]");
      if (row && row.dataset.symbol) openStockPopup(row.dataset.symbol, row.dataset.name);
    });
  }

  // Same pattern for FX / index / commodity / rate rows -> the generic
  // indicator popup (kind carried in data-kind).
  for (const containerId of ["fx-regions", "idx-main", "rates-list", "commodities-list"]) {
    $(containerId).addEventListener("click", (ev) => {
      const row = ev.target.closest("[data-kind]");
      if (row) openIndicatorPopup(row.dataset.kind, row.dataset.symbol, row.dataset);
    });
  }

  // -- news: keyword search (over the full 24h feed, not just what's on
  // screen) + numbered pagination through the matched results ----------

  function newsMatchesKeywords(n) {
    if (newsKeywords.length === 0) return true;
    const txt = `${n.headline} ${n.tag} ${n.summary || ""}`.toLowerCase();
    return newsKeywords.some((k) => txt.includes(k.toLowerCase()));
  }

  function filteredNews() {
    if (!latest || !latest.news) return [];
    return latest.news.filter(newsMatchesKeywords);
  }

  function renderNewsChips() {
    const chips = $("news-kw-chips");
    chips.style.display = newsKeywords.length ? "flex" : "none";
    chips.innerHTML = newsKeywords.map((k, i) => `
      <span class="kw-chip"><span>${esc(k)}</span><button type="button" data-remove-kw="${i}">✕</button></span>`).join("");
    chips.querySelectorAll("[data-remove-kw]").forEach((btn) => {
      btn.addEventListener("click", () => {
        newsKeywords.splice(Number(btn.dataset.removeKw), 1);
        newsPage = 1;
        renderNewsChips();
        renderNews();
      });
    });
  }

  function renderNewsPagination(totalPages) {
    const box = $("news-pagination");
    if (totalPages <= 1) { box.innerHTML = ""; box.style.display = "none"; return; }
    box.style.display = "flex";
    const btn = (label, page, opts = {}) => `
      <button type="button" data-page="${page}" ${opts.disabled ? "disabled" : ""}
        style="min-width:26px;padding:4px 8px;font-size:11.5px;border:1px solid var(--color-divider);background:${page === newsPage ? "var(--color-accent)" : "transparent"};color:${page === newsPage ? "#fff" : "inherit"};cursor:${opts.disabled ? "default" : "pointer"}">${label}</button>`;

    let pages = [];
    for (let p = 1; p <= totalPages; p++) pages.push(p);
    box.innerHTML = btn("‹", Math.max(1, newsPage - 1), { disabled: newsPage === 1 })
      + pages.map((p) => btn(p, p)).join("")
      + btn("›", Math.min(totalPages, newsPage + 1), { disabled: newsPage === totalPages });

    box.querySelectorAll("[data-page]").forEach((el) => {
      el.addEventListener("click", () => {
        if (el.disabled) return;
        newsPage = Number(el.dataset.page);
        renderNews();
      });
    });
  }

  function renderNews() {
    const all = filteredNews();
    const totalPages = Math.max(1, Math.ceil(all.length / NEWS_PAGE_SIZE));
    if (newsPage > totalPages) newsPage = totalPages;
    const start = (newsPage - 1) * NEWS_PAGE_SIZE;
    const shown = all.slice(start, start + NEWS_PAGE_SIZE);

    $("news-list").innerHTML = shown.map((n) => `
      <div style="display:flex;gap:11px;padding:7px 0;border-bottom:1px solid rgba(29,31,32,.06)">
        <span class="mono" style="font-size:10.5px;color:#98989b;min-width:34px;white-space:nowrap;padding-top:2px">${esc(n.time)}</span>
        <div style="font-size:12.5px;line-height:1.4;text-wrap:pretty">
          ${n.url ? `<a href="${esc(n.url)}" target="_blank" rel="noopener" style="color:inherit;text-decoration:none">${esc(n.headline)}</a>` : esc(n.headline)}
          <span class="tag tag-accent" style="font-size:9px;padding:0 6px;vertical-align:middle">${esc(n.tag)}</span>
        </div>
      </div>`).join("");

    $("news-empty").style.display = (latest && latest.news && latest.news.length > 0 && all.length === 0) ? "block" : "none";
    renderNewsPagination(totalPages);
    $("news-count").textContent = latest && latest.news ? `총 ${latest.news.length}건` : "";
  }

  $("news-kw-input").addEventListener("keydown", (ev) => {
    if (ev.key !== "Enter") return;
    // 한글 등 IME 조합 중에 눌린 Enter는 무시 — 조합을 마무리하는 Enter와
    // 그 다음에 오는 진짜 Enter가 중복으로 fire되어, 조합 중이던 값을 읽고
    // 지운 뒤 마지막 음절이 다시 입력창에 채워지는 문제를 막는다.
    if (ev.isComposing || ev.keyCode === 229) return;
    ev.preventDefault();
    const v = $("news-kw-input").value.trim();
    if (v && !newsKeywords.includes(v)) {
      newsKeywords.push(v);
      newsPage = 1;
      renderNewsChips();
      renderNews();
    }
    $("news-kw-input").value = "";
  });

  function render(snapshot) {
    latest = snapshot;
    renderTicker(snapshot.ticker);
    renderFxRegions(snapshot.fxRegions);
    renderIdxMain(snapshot.idxMain);
    renderRates(snapshot.rates);
    renderCommodities(snapshot.commAll);
    renderMovers();
    renderKrMostTraded();
    renderUsMovers();
    renderUsMostActive();
    renderWatchlist(snapshot.watchlist);
    renderNews();
    if (snapshot.asOf) {
      const d = new Date(snapshot.asOf);
      $("as-of").textContent = d.toLocaleTimeString("ko-KR", { hour12: false });
    }
  }

  // -- movers toggle -------------------------------------------------

  $("mv-gainers").addEventListener("change", () => { moversTab = "gainers"; renderMovers(); });
  $("mv-losers").addEventListener("change", () => { moversTab = "losers"; renderMovers(); });
  $("usmv-gainers").addEventListener("change", () => { usMoversTab = "gainers"; renderUsMovers(); });
  $("usmv-losers").addEventListener("change", () => { usMoversTab = "losers"; renderUsMovers(); });

  // -- watchlist search / add ------------------------------------------

  const searchInput = $("watchlist-search");
  const resultsBox = $("watchlist-search-results");
  let searchDebounce = null;

  function hideResults() { resultsBox.style.display = "none"; resultsBox.innerHTML = ""; }

  function renderResults(items) {
    if (!items.length) { hideResults(); return; }
    resultsBox.innerHTML = items.map((it) => `
      <div data-add='${JSON.stringify(it).replace(/'/g, "&#39;")}' style="padding:8px 10px;font-size:12.5px;cursor:pointer;display:flex;justify-content:space-between;gap:8px;border-bottom:1px solid rgba(29,31,32,.06)">
        <span>${esc(it.name)}</span>
        <span style="color:#98989b;font-size:11px">${esc(it.market)} · ${esc(it.symbol)}</span>
      </div>`).join("");
    resultsBox.style.display = "block";
    resultsBox.querySelectorAll("[data-add]").forEach((el) => {
      el.addEventListener("mouseenter", () => { el.style.background = "color-mix(in srgb, var(--color-text) 6%, transparent)"; });
      el.addEventListener("mouseleave", () => { el.style.background = ""; });
      el.addEventListener("click", async () => {
        const item = JSON.parse(el.dataset.add);
        hideResults();
        searchInput.value = "";
        try {
          await fetch("/api/watchlist", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(item),
          });
        } catch (e) { console.error("add failed", e); }
      });
    });
  }

  searchInput.addEventListener("input", () => {
    clearTimeout(searchDebounce);
    const q = searchInput.value.trim();
    if (!q) { hideResults(); return; }
    searchDebounce = setTimeout(async () => {
      try {
        const res = await fetch(`/api/search?q=${encodeURIComponent(q)}`);
        renderResults(await res.json());
      } catch (e) { console.error("search failed", e); }
    }, 250);
  });

  document.addEventListener("click", (ev) => {
    if (!resultsBox.contains(ev.target) && ev.target !== searchInput) hideResults();
  });

  // -- stock detail popup ------------------------------------------------

  const popupBackdrop = $("stock-popup-backdrop");
  let currentPopupSymbol = null;

  function stockChartSvg(chart, color, w = 512, h = 96) {
    if (!chart) return '<div style="height:96px;display:flex;align-items:center;justify-content:center;color:#98989b;font-size:11px">차트 데이터 없음</div>';
    return `<svg width="${w}" height="${h}" viewBox="0 0 ${w} ${h}" style="display:block;width:100%">
      <path d="${chart.area}" fill="${color}" fill-opacity="0.12"></path>
      <path d="${chart.line}" fill="none" stroke="${color}" stroke-width="1.3" stroke-linejoin="round" stroke-linecap="round"></path>
    </svg>`;
  }

  function setWatchlistBtn(inWatchlist) {
    $("stk-watchlist-label").textContent = inWatchlist ? "★ 관심종목에서 삭제" : "☆ 관심종목 추가";
    $("stk-watchlist-btn").dataset.inWatchlist = inWatchlist ? "1" : "0";
  }

  async function openStockPopup(symbol, name) {
    currentPopupSymbol = symbol;
    popupBackdrop.style.display = "flex";
    $("stk-name").textContent = name || symbol;
    $("stk-code").textContent = "";
    $("stk-market").textContent = "";
    $("stk-price").textContent = "…";
    $("stk-chg").textContent = "";
    $("stk-chart").innerHTML = "";
    $("stk-news").innerHTML = "";

    try {
      const res = await fetch(`/api/stock/${encodeURIComponent(symbol)}?name=${encodeURIComponent(name || "")}&scheme=kr`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const d = await res.json();
      if (currentPopupSymbol !== symbol) return; // user moved on to another row

      $("stk-name").textContent = d.name;
      $("stk-code").textContent = d.code;
      $("stk-market").textContent = d.market;
      $("stk-price").textContent = d.price;
      $("stk-price").style.color = d.color;
      $("stk-chg").textContent = `${d.arrow} ${d.chg} (${d.pct})`;
      $("stk-chg").style.color = d.color;
      $("stk-hilo").textContent = `고 ${d.high} / 저 ${d.low}`;
      $("stk-chart").innerHTML = stockChartSvg(d.chart, d.color);

      $("stk-volume").textContent = d.volume;
      $("stk-tradingValue").textContent = d.tradingValue;
      $("stk-marketCap").textContent = d.marketCap;
      $("stk-open").textContent = d.open;
      $("stk-high").textContent = d.high;
      $("stk-low").textContent = d.low;
      $("stk-week52High").textContent = d.week52High;
      $("stk-week52Low").textContent = d.week52Low;
      $("stk-foreignRate").textContent = d.foreignRate;
      $("stk-per").textContent = d.per;
      $("stk-pbr").textContent = d.pbr;
      $("stk-prevClose").textContent = d.prevClose;

      $("stk-news").innerHTML = d.news.length ? d.news.map((n) => `
        <div style="display:flex;gap:9px;padding:6px 0;border-bottom:1px solid rgba(29,31,32,.06)">
          <span style="color:var(--color-accent);font-size:12px;line-height:1.4">›</span>
          ${n.url
            ? `<a href="${esc(n.url)}" target="_blank" rel="noopener" style="color:inherit;text-decoration:none;font-size:12.5px;line-height:1.4;text-wrap:pretty">${esc(n.headline)}</a>`
            : `<span style="font-size:12.5px;line-height:1.4;text-wrap:pretty">${esc(n.headline)}</span>`}
        </div>`).join("") : '<div style="font-size:12px;color:#98989b">관련 뉴스 없음</div>';

      setWatchlistBtn(d.inWatchlist);
    } catch (e) {
      console.error("stock detail fetch failed", e);
      $("stk-price").textContent = "오류";
      $("stk-chart").innerHTML = '<div style="height:96px;display:flex;align-items:center;justify-content:center;color:#98989b;font-size:11px">데이터를 불러오지 못했습니다</div>';
    }
  }

  function closeStockPopup() {
    currentPopupSymbol = null;
    popupBackdrop.style.display = "none";
  }

  $("stk-close").addEventListener("click", closeStockPopup);
  $("stk-close-btn").addEventListener("click", closeStockPopup);
  popupBackdrop.addEventListener("click", (ev) => { if (ev.target === popupBackdrop) closeStockPopup(); });
  document.addEventListener("keydown", (ev) => { if (ev.key === "Escape" && popupBackdrop.style.display !== "none") closeStockPopup(); });

  $("stk-watchlist-btn").addEventListener("click", async () => {
    if (!currentPopupSymbol) return;
    const btn = $("stk-watchlist-btn");
    const inWatchlist = btn.dataset.inWatchlist === "1";
    btn.disabled = true;
    try {
      if (inWatchlist) {
        await fetch(`/api/watchlist/${encodeURIComponent(currentPopupSymbol)}`, { method: "DELETE" });
      } else {
        await fetch("/api/watchlist", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ symbol: currentPopupSymbol, name: $("stk-name").textContent, market: $("stk-market").textContent }),
        });
      }
      setWatchlistBtn(!inWatchlist);
    } catch (e) {
      console.error("watchlist toggle failed", e);
    } finally {
      btn.disabled = false;
    }
  });

  // -- indicator (FX / index / commodity / rate) detail popup ------------

  const indicatorBackdrop = $("indicator-popup-backdrop");
  let currentIndicatorKey = null;

  function indicatorChartSvg(chart, color, w = 476, h = 92) {
    if (!chart) return '<div style="height:92px;display:flex;align-items:center;justify-content:center;color:#98989b;font-size:11px">차트 데이터 없음</div>';
    return `<svg width="${w}" height="${h}" viewBox="0 0 ${w} ${h}" style="display:block;width:100%">
      <path d="${chart.area}" fill="${color}" fill-opacity="0.12"></path>
      <path d="${chart.line}" fill="none" stroke="${color}" stroke-width="1.3" stroke-linejoin="round" stroke-linecap="round"></path>
    </svg>`;
  }

  function renderIndicatorStats(stats) {
    $("ind-stats").innerHTML = stats.map((s) => `
      <div class="stat"><span>${esc(s.label)}</span><b class="mono"${s.color ? ` style="color:${s.color}"` : ""}>${esc(s.value)}</b></div>
    `).join("");
  }

  async function openIndicatorPopup(kind, symbol, data) {
    const key = `${kind}:${symbol}`;
    currentIndicatorKey = key;
    indicatorBackdrop.style.display = "flex";
    $("ind-title").textContent = data.pair || data.name || symbol;
    $("ind-subtitle").textContent = kind === "fx" ? (data.name || "") : (data.sub || "");
    $("ind-tag").textContent = "";
    $("ind-price").textContent = "…";
    $("ind-chg").textContent = "";
    $("ind-chart").innerHTML = "";
    $("ind-stats").innerHTML = "";
    $("ind-news").innerHTML = "";
    $("ind-news-wrap").style.display = "none";

    const params = new URLSearchParams({ name: data.name || "", scheme: "kr" });
    if (kind === "fx") params.set("pair", data.pair || "");
    if (kind === "commodity") params.set("contract", data.contract || "");
    if (kind === "rate") params.set("sub", data.sub || "");

    try {
      const res = await fetch(`/api/indicator/${kind}/${encodeURIComponent(symbol)}?${params}`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const d = await res.json();
      if (currentIndicatorKey !== key) return; // user moved on to another indicator

      $("ind-title").textContent = d.title;
      $("ind-subtitle").textContent = d.subtitle || "";
      $("ind-tag").textContent = d.tag || "";
      $("ind-price").textContent = d.price;
      $("ind-price").style.color = d.color;
      $("ind-chg").textContent = `${d.arrow} ${d.chg}${d.pct ? ` (${d.pct})` : ""}`;
      $("ind-chg").style.color = d.color;
      $("ind-chart-label").textContent = d.chartLabel || "";
      $("ind-chart-range").textContent = d.chartRange || "";
      $("ind-chart").innerHTML = indicatorChartSvg(d.chart, d.color);
      renderIndicatorStats(d.stats || []);

      if (d.news && d.news.length) {
        $("ind-news-wrap").style.display = "";
        $("ind-news").innerHTML = d.news.map((n) => `
          <div style="display:flex;gap:9px;padding:6px 0;border-bottom:1px solid rgba(29,31,32,.06)">
            <span style="color:var(--color-accent);font-size:12px;line-height:1.4">›</span>
            ${n.url
              ? `<a href="${esc(n.url)}" target="_blank" rel="noopener" style="color:inherit;text-decoration:none;font-size:12.5px;line-height:1.4;text-wrap:pretty">${esc(n.headline)}</a>`
              : `<span style="font-size:12.5px;line-height:1.4;text-wrap:pretty">${esc(n.headline)}</span>`}
          </div>`).join("");
      }
    } catch (e) {
      console.error("indicator detail fetch failed", e);
      $("ind-price").textContent = "오류";
      $("ind-chart").innerHTML = '<div style="height:92px;display:flex;align-items:center;justify-content:center;color:#98989b;font-size:11px">데이터를 불러오지 못했습니다</div>';
    }
  }

  function closeIndicatorPopup() {
    currentIndicatorKey = null;
    indicatorBackdrop.style.display = "none";
  }

  $("ind-close").addEventListener("click", closeIndicatorPopup);
  $("ind-close-btn").addEventListener("click", closeIndicatorPopup);
  indicatorBackdrop.addEventListener("click", (ev) => { if (ev.target === indicatorBackdrop) closeIndicatorPopup(); });
  document.addEventListener("keydown", (ev) => { if (ev.key === "Escape" && indicatorBackdrop.style.display !== "none") closeIndicatorPopup(); });

  // -- clock -----------------------------------------------------------

  function tickClock() {
    const now = new Date();
    const kst = new Date(now.toLocaleString("en-US", { timeZone: "Asia/Seoul" }));
    const p = (n) => String(n).padStart(2, "0");
    $("clock").textContent = `${p(kst.getHours())}:${p(kst.getMinutes())}:${p(kst.getSeconds())} KST`;
    $("today").textContent = `${kst.getFullYear()}.${p(kst.getMonth() + 1)}.${p(kst.getDate())}`;
  }
  tickClock();
  setInterval(tickClock, 1000);

  // -- WebSocket with reconnect -----------------------------------------

  let backoff = 1000;
  const MAX_BACKOFF = 15000;

  function setConn(state) {
    const dot = $("conn-dot");
    const text = $("conn-text");
    dot.className = "conn-dot" + (state === "down" ? " down" : state === "connecting" ? " connecting" : "");
    text.textContent = state === "up" ? "실시간 연결됨" : state === "connecting" ? "연결 중…" : "연결 끊김 · 재연결 중";
  }

  function connect() {
    setConn("connecting");
    const proto = location.protocol === "https:" ? "wss" : "ws";
    const ws = new WebSocket(`${proto}://${location.host}/ws?scheme=kr`);

    ws.onopen = () => { setConn("up"); backoff = 1000; };
    ws.onmessage = (ev) => {
      try { render(JSON.parse(ev.data)); } catch (e) { console.error("bad snapshot", e); }
    };
    ws.onclose = () => {
      setConn("down");
      setTimeout(connect, backoff);
      backoff = Math.min(backoff * 1.7, MAX_BACKOFF);
    };
    ws.onerror = () => ws.close();
  }
  connect();
})();
