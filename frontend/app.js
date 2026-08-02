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
  let flowMarket = "kospi";
  let flowChartPeriod = "1d";
  let kwNewsGroup = "fxbond";
  let kwNewsPage = 1;
  let curveKey = "국고채권";
  let bondFlowType = "합계";
  let bondFlowPeriod = "1d";
  // 환율 뉴스는 스냅샷이 아니라 여기 붙들어 둔다 — 페이저를 눌렀을 때
  // 스냅샷을 기다리지 않고 바로 그 자리에서 다시 그리기 위해서다.
  let fxNewsRows = null;
  let fxNewsPage = 1;
  const NEWS_PAGE_SIZE = 20;
  const KW_NEWS_PAGE_SIZE = 15, KW_NEWS_PAGES = 3;
  const FX_NEWS_PAGE_SIZE = 15;
  // 기관계 아래로 들여쓸 세부 주체 — 합계와 구성요소를 눈으로 구분한다.
  const FLOW_SUB_ROWS = new Set([
    "financial", "insurance", "trust", "bank", "otherFinance", "pension",
  ]);

  const $ = (id) => document.getElementById(id);

  function esc(s) {
    return String(s ?? "").replace(/[&<>"']/g, (c) => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
    }[c]));
  }

  function staleTitle(row) {
    return row.stale ? ' title="마지막 성공한 값 (일시적 갱신 실패)"' : "";
  }

  // ETF·ETN도 개별종목과 같은 랭킹에 섞여 들어오므로(네이버 증권 ·
  // 야후 파이낸스 원본과 동일) 이름 옆에 종류를 붙여 구분한다.
  function kindTag(row) {
    return row.kind ? `<span class="mv-tag">${esc(row.kind)}</span>` : "";
  }

  // -- panel renderers ---------------------------------------------------

  function renderTicker(rows) {
    $("ticker-strip").innerHTML = rows.map((t) => `
      <div class="clickable-row" data-kind="${esc(t.kind || "index")}" data-symbol="${esc(t.symbol)}" data-name="${esc(t.name || t.label)}"${t.pair ? ` data-pair="${esc(t.pair)}"` : ""}${t.contract ? ` data-contract="${esc(t.contract)}"` : ""}${t.sub ? ` data-sub="${esc(t.sub)}"` : ""} style="flex:1;min-width:110px;display:flex;flex-direction:column;gap:1px;padding:8px 14px;border-right:1px solid var(--color-divider)"${staleTitle(t)}>
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
        <span class="mv-name">${esc(m.name)}${kindTag(m)}</span>
        <span class="mono mv-px">${esc(m.price)}</span>
        <span class="mono mv-pct" style="color:${m.color}">${esc(m.pct)}</span>
      </div>`).join("");
  }

  function renderKrMostTraded() {
    if (!latest) return;
    $("kr-most-traded-list").innerHTML = latest.krMostTraded.map((m) => `
      <div class="mt-row clickable-row" data-symbol="${esc(m.symbol)}" data-name="${esc(m.name.replace(/\*$/, ""))}"${staleTitle(m)}>
        <span class="mv-rank">${m.rank}</span>
        <span class="mv-name">${esc(m.name)}${kindTag(m)}</span>
        <span class="mono mv-px">${esc(m.price)}</span>
        <span class="mono mv-pct" style="color:${m.color}">${esc(m.pct)}</span>
        <span class="mono mt-vol">${esc(m.tradingValue)}</span>
      </div>`).join("");
  }

  function renderUsMovers() {
    if (!latest) return;
    const rows = usMoversTab === "gainers" ? latest.usGainers : latest.usLosers;
    $("us-movers-list").innerHTML = rows.map((m) => `
      <div class="mv-row clickable-row" data-symbol="${esc(m.symbol)}" data-name="${esc(m.fullName)}" title="${esc(m.symbol)} · ${esc(m.fullName)}">
        <span class="mv-rank">${m.rank}</span>
        <span class="mv-name">${esc(m.name)}${kindTag(m)}</span>
        <span class="mono mv-px">${esc(m.price)}</span>
        <span class="mono mv-pct" style="color:${m.color}">${esc(m.pct)}</span>
      </div>`).join("");
  }

  function renderUsMostActive() {
    if (!latest) return;
    $("us-most-active-list").innerHTML = latest.usMostActive.map((m) => `
      <div class="mt-row clickable-row" data-symbol="${esc(m.symbol)}" data-name="${esc(m.fullName)}" title="${esc(m.symbol)} · ${esc(m.fullName)}">
        <span class="mv-rank">${m.rank}</span>
        <span class="mv-name">${esc(m.name)}${kindTag(m)}</span>
        <span class="mono mv-px">${esc(m.price)}</span>
        <span class="mono mv-pct" style="color:${m.color}">${esc(m.pct)}</span>
        <span class="mono mt-vol">${esc(m.volume)}</span>
      </div>`).join("");
  }

  function renderKrRates(rows) {
    if (!rows) return;
    $("kr-rates-list").innerHTML = rows.map((r) => `
      <div class="fx-row clickable-row" data-kind="krrate" data-symbol="${esc(r.code)}" data-name="${esc(r.name)}"${staleTitle(r)}>
        <span class="fx-pair">${esc(r.name)}</span>
        <span class="mono fx-px">${esc(r.value)}</span>
        <span class="mono fx-pct" style="color:${r.color}">${r.arrow} ${esc(r.chg)}</span>
      </div>`).join("");
  }

  function renderFxNews(rows) {
    if (rows) fxNewsRows = rows;
    if (!fxNewsRows) return;

    const totalPages = Math.max(1, Math.ceil(fxNewsRows.length / FX_NEWS_PAGE_SIZE));
    if (fxNewsPage > totalPages) fxNewsPage = totalPages;
    const start = (fxNewsPage - 1) * FX_NEWS_PAGE_SIZE;

    $("fx-news-list").innerHTML = fxNewsRows.slice(start, start + FX_NEWS_PAGE_SIZE).map((n) => `
      <div style="display:flex;gap:11px;padding:6px 0;border-bottom:1px solid rgba(29,31,32,.06)">
        <span class="mono" style="font-size:10.5px;color:#98989b;min-width:34px;white-space:nowrap;padding-top:2px">${esc(n.time)}</span>
        <div style="font-size:12.5px;line-height:1.4;text-wrap:pretty">
          ${n.url ? `<a href="${esc(n.url)}" target="_blank" rel="noopener" style="color:inherit;text-decoration:none">${esc(n.headline)}</a>` : esc(n.headline)}
          <span class="tag tag-accent" style="font-size:9px;padding:0 6px;vertical-align:middle">${esc(n.tag)}</span>
        </div>
      </div>`).join("");

    renderPagination("fx-news-pagination", fxNewsPage, totalPages, (p) => {
      fxNewsPage = p;
      renderFxNews();
    });
  }

  // -- 수급: 투자자(행) × 기간(열) -------------------------------------

  function renderInvestorFlow() {
    if (!latest) return;
    const flow = latest.investorFlow || {};
    const periods = latest.investorFlowPeriods || [];
    const data = flow[flowMarket];
    const table = $("flow-table");
    const foot = $("flow-foot");

    if (!data || !periods.length) {
      table.innerHTML = `<div style="font-size:12px;color:#98989b;padding:10px 0">수급 데이터를 불러오지 못했습니다.</div>`;
      foot.textContent = "";
      return;
    }

    const head = `
      <div class="flow-row flow-head">
        <span>투자자</span>${periods.map((p) => `<span>${esc(p.label)}</span>`).join("")}
      </div>`;

    // 기간별 배열은 모두 같은 투자자 순서라 첫 기간을 기준으로 행을 편다.
    const rows = data.periods[periods[0].key].map((_, i) => {
      const first = data.periods[periods[0].key][i];
      const cells = periods.map((p) => {
        const cell = data.periods[p.key][i];
        return `<span class="mono flow-val" style="color:${cell.color}">${esc(cell.value)}</span>`;
      }).join("");
      return `
        <div class="flow-row${FLOW_SUB_ROWS.has(first.key) ? " flow-sub" : ""}">
          <span class="flow-name">${esc(first.label)}</span>${cells}
        </div>`;
    }).join("");

    table.innerHTML = head + rows;
    foot.textContent = `${data.asOf} 기준 · 단위 ${data.unitLabel} · 최근 ${data.days}영업일 누적`
      + (data.stale ? " · 갱신 실패(직전 값)" : "");
    renderFlowChart(data, periods);
  }

  // -- 수급 차트: 주체별 순매수 막대 ------------------------------------
  // 색은 부호(순매수/순매도)만 나타내는 diverging 인코딩이고, 보드가 이미
  // 쓰는 상승 빨강 / 하락 파랑 짝을 그대로 받는다(서버가 스킴별로 내려줌).
  // 색만으로 의미를 나르지 않도록 막대마다 부호 붙은 숫자를 함께 찍는다.

  const FLOW_W = 1120, FLOW_H = 264;
  const FLOW_PAD = { l: 64, r: 14, t: 26, b: 42 };

  // 축 눈금용 — 서버의 _fmt_signed_flow 와 같은 규칙. 네이버 원본이 이미
  // 억원이라(페이지 표기 "단위:억원") 조 단위만 접는다.
  function flowAxisLabel(v, unitLabel) {
    if (v === 0) return "0";   // 조 눈금 사이에 "0억"이 끼면 단위가 뒤섞여 보인다
    if (unitLabel === "계약") return v.toLocaleString("en-US");
    return Math.abs(v) >= 10000
      ? `${(v / 10000).toLocaleString("en-US", { maximumFractionDigits: 1 })}조`
      : `${Math.round(v).toLocaleString("en-US")}억`;
  }

  // 1 / 2 / 2.5 / 5 × 10^k 중 v 이상인 첫 값 — 눈금이 읽기 좋은 수로 떨어진다.
  function niceStep(v) {
    if (v <= 0) return 1;
    const exp = Math.floor(Math.log10(v));
    const base = Math.pow(10, exp);
    for (const m of [1, 2, 2.5, 5, 10]) if (v <= m * base) return m * base;
    return 10 * base;
  }

  // 주식 수급·채권 수급이 같은 막대 차트를 쓴다. cells 는
  // [{label, raw, value, color}], axisFmt 는 눈금 숫자 포맷터.
  function netBuyBarsSvg(cells, axisFmt, ariaLabel) {
    const step = niceStep(Math.max(...cells.map((c) => Math.abs(c.raw))) / 2) || 1;
    const max = step * 2;                       // 위아래 각 2칸 = 눈금선 5줄
    const x0 = FLOW_PAD.l, x1 = FLOW_W - FLOW_PAD.r;
    const y0 = FLOW_PAD.t, y1 = FLOW_H - FLOW_PAD.b;
    const zeroY = (y0 + y1) / 2;
    const half = (y1 - y0) / 2;
    const yFor = (v) => zeroY - (v / max) * half;

    const band = (x1 - x0) / cells.length;
    const barW = Math.min(24, band * 0.44);     // 슬롯을 채우지 않고 여백을 남긴다

    // 눈금선은 한 단계 옅은 회색 실선 1px — 데이터보다 뒤로 물러나야 한다.
    let grid = "";
    for (let i = -2; i <= 2; i++) {
      const v = step * i;
      const y = yFor(v);
      grid += `<line x1="${x0}" y1="${y}" x2="${x1}" y2="${y}" stroke="${i === 0 ? "#b9b9bc" : "#dedee0"}" stroke-width="1"/>`
        + `<text x="${x0 - 8}" y="${y + 3.5}" text-anchor="end" font-size="10.5" fill="#98989b">${esc(axisFmt(v))}</text>`;
    }

    const marks = cells.map((c, i) => {
      const cx = x0 + band * (i + 0.5);
      const x = cx - barW / 2;
      const yv = yFor(c.raw);
      const h = Math.abs(yv - zeroY);
      const r = Math.min(4, h);                 // 막대가 4px보다 얇으면 라운드를 줄인다
      const up = c.raw >= 0;
      // 데이터 끝은 4px 라운드, 기준선 쪽은 각지게.
      const path = up
        ? `M${x} ${zeroY} V${yv + r} Q${x} ${yv} ${x + r} ${yv} H${x + barW - r} Q${x + barW} ${yv} ${x + barW} ${yv + r} V${zeroY} Z`
        : `M${x} ${zeroY} V${yv - r} Q${x} ${yv} ${x + r} ${yv} H${x + barW - r} Q${x + barW} ${yv} ${x + barW} ${yv - r} V${zeroY} Z`;
      const labelY = up ? yv - 7 : yv + 15;
      return `
        <g>
          <title>${esc(c.label)} ${esc(c.value)}</title>
          <rect x="${cx - band / 2}" y="${y0}" width="${band}" height="${y1 - y0}" fill="transparent"/>
          ${h < 0.5 ? "" : `<path d="${path}" fill="${c.color}"/>`}
          <text x="${cx}" y="${labelY}" text-anchor="middle" font-size="11" font-weight="500" fill="${c.color}">${esc(c.value)}</text>
          <text x="${cx}" y="${y1 + 17}" text-anchor="middle" font-size="11" fill="#5d5d60">${esc(c.label)}</text>
        </g>`;
    }).join("");

    return `
      <svg viewBox="0 0 ${FLOW_W} ${FLOW_H}" width="100%" height="${FLOW_H}"
           preserveAspectRatio="xMidYMid meet" role="img"
           aria-label="${esc(ariaLabel)}">
        ${grid}${marks}
      </svg>`;
  }

  let flowChartKey = "";   // 마지막으로 그린 차트의 데이터 서명

  function renderFlowChart(data, periods) {
    const box = $("flow-chart");
    const cells = (data.periods[flowChartPeriod] || []).filter((c) => c.chart);
    const period = periods.find((p) => p.key === flowChartPeriod);
    $("flow-chart-period").textContent = period ? `· ${period.label} 누적` : "";

    if (!cells.length) { box.innerHTML = ""; flowChartKey = ""; return; }

    // 스냅샷은 10초마다 오지만 수급 원본은 5분마다 갱신된다. 값이 그대로면
    // SVG를 다시 만들지 않는다 — 매번 새로 그리면 눈에 띄게 깜빡인다.
    const key = `${data.label}|${flowChartPeriod}|${cells.map((c) => c.raw).join(",")}`;
    if (key === flowChartKey) return;
    flowChartKey = key;

    box.innerHTML = netBuyBarsSvg(cells, (v) => flowAxisLabel(v, data.unitLabel),
      `${data.label} 주체별 순매수`);
  }

  // -- 채권 수급 (KOFIA 장외채권) ----------------------------------------

  // 채권 수급은 서버가 이미 억원으로 준다 — 주식 쪽(백만원)과 단위가 달라
  // 축 포맷터를 따로 둔다.
  function bondAxisLabel(v) {
    if (v === 0) return "0";
    return Math.abs(v) >= 10000
      ? `${(v / 10000).toLocaleString("en-US", { maximumFractionDigits: 1 })}조`
      : `${Math.round(v).toLocaleString("en-US")}억`;
  }

  let bondFlowChartKey = "";

  function renderBondFlow() {
    if (!latest) return;
    const bf = latest.bondFlow;
    const periods = latest.bondFlowPeriods || [];
    const table = $("bflow-table");

    if (!bf || !periods.length) {
      table.innerHTML = `<div style="font-size:12px;color:#98989b;padding:10px 0">채권 수급을 불러오지 못했습니다.</div>`;
      $("bflow-foot").textContent = "";
      $("bflow-chart").innerHTML = "";
      return;
    }

    if (!bf.bondTypes.includes(bondFlowType)) bondFlowType = bf.bondTypes[0];

    // 채권종류 탭은 서버가 준 목록으로 만든다.
    const seg = $("bflow-seg");
    if (seg.dataset.keys !== bf.bondTypes.join("|")) {
      seg.dataset.keys = bf.bondTypes.join("|");
      seg.innerHTML = bf.bondTypes.map((t) => `
        <label class="seg-opt" style="padding:4px 10px;font-size:11.5px">
          <input type="radio" name="bflowsel" value="${esc(t)}"${t === bondFlowType ? " checked" : ""}><span>${esc(t)}</span>
        </label>`).join("");
      seg.querySelectorAll("input").forEach((el) => {
        el.addEventListener("change", () => { bondFlowType = el.value; renderBondFlow(); });
      });
    }

    const head = `<div class="flow-row bflow-row flow-head"><span>투자자</span>${
      periods.map((p) => `<span>${esc(p.label)}</span>`).join("")}</div>`;

    const first = bf.periods[periods[0].key][bondFlowType] || [];
    const rows = first.map((_, i) => {
      const cells = periods.map((p) => {
        const c = (bf.periods[p.key][bondFlowType] || [])[i];
        return c
          ? `<span class="mono flow-val" style="color:${c.color}">${esc(c.value)}</span>`
          : `<span class="mono flow-val" style="color:#c4c4c6">—</span>`;
      }).join("");
      return `<div class="flow-row bflow-row"><span class="flow-name">${esc(first[i].label)}</span>${cells}</div>`;
    }).join("");

    table.innerHTML = head + rows;
    const d = bf.asOf;
    $("bflow-foot").textContent =
      `${d.slice(0, 4)}.${d.slice(4, 6)}.${d.slice(6)} 기준 · 단위 원 · 순매수 = 매수 − 매도 · 장외 거래대금`
      + (bf.stale ? " · 갱신 실패(직전 값)" : "");

    const period = periods.find((p) => p.key === bondFlowPeriod);
    $("bflow-chart-label").textContent = period ? `· ${bondFlowType} · ${period.label} 누적` : "";
    const cells = bf.periods[bondFlowPeriod][bondFlowType] || [];
    if (!cells.length) { $("bflow-chart").innerHTML = ""; bondFlowChartKey = ""; return; }

    const key = `${bondFlowType}|${bondFlowPeriod}|${cells.map((c) => c.raw).join(",")}`;
    if (key === bondFlowChartKey) return;
    bondFlowChartKey = key;
    $("bflow-chart").innerHTML = netBuyBarsSvg(cells, bondAxisLabel,
      `${bondFlowType} 주체별 순매수`);
  }

  // -- 지표종목 최종호가 (채권 커브 패널 상단) ----------------------------
  // 금리 레벨에는 색을 안 입힌다 — 오르내림이 아니라 수준이라서다.
  // 전일대비만 색·화살표를 달아 방향을 나른다.

  function renderBondQuotes() {
    if (!latest) return;
    const q = latest.bondQuotes;
    const list = $("quotes-list");
    if (!q || !q.rows.length) {
      list.innerHTML = `<div style="font-size:12px;color:#98989b;padding:8px 0">최종호가를 불러오지 못했습니다.</div>`;
      $("quotes-asof").textContent = "";
      return;
    }

    const head = `<div class="q-row q-head">
      <span>종목</span><span>잔존기간</span><span class="q-num">수익률</span>
      <span class="q-num">전일대비</span><span class="q-num">연중 최저~최고</span></div>`;

    list.innerHTML = head + q.rows.map((r) => `
      <div class="q-row">
        <span style="font-weight:500;white-space:nowrap;overflow:hidden;text-overflow:ellipsis">${esc(r.label)}</span>
        <span style="color:#7a7a7d;font-size:11px">${esc(r.term)}</span>
        <span class="mono q-num" style="font-weight:500">${esc(r["yield"])}</span>
        <span class="mono q-num" style="color:${r.changeColor}">${r.arrow} ${esc(r.changeBp)}</span>
        <span class="mono q-num" style="color:#7a7a7d;font-size:11px">${esc(r.range)}</span>
      </div>`).join("");

    const d = q.asOf;
    $("quotes-asof").textContent = `· ${d.slice(0, 4)}.${d.slice(4, 6)}.${d.slice(6)} · 단위 %`;
  }

  // -- 단기금융시장 금리 (CP · 전단채) ------------------------------------

  function renderShortTermRates() {
    if (!latest) return;
    const st = latest.shortTermRates;
    const table = $("strate-table");
    if (!st || !st.rows.length) {
      table.innerHTML = `<div style="font-size:12px;color:#98989b;padding:10px 0">단기금리를 불러오지 못했습니다.</div>`;
      $("strate-asof").textContent = "";
      $("strate-foot").textContent = "";
      return;
    }

    const head = `<div class="st-row st-head"><span>구분</span>${
      st.tenors.map((t) => `<span class="st-num">${esc(t)}</span>`).join("")
      }<span class="st-num">당일 거래대금</span></div>`;

    table.innerHTML = head + st.rows.map((r) => `
      <div class="st-row">
        <span style="font-weight:500;white-space:nowrap">${esc(r.label)}</span>
        ${r.rates.map((v) => `<span class="mono st-num"${v === "—" ? ' style="color:#c4c4c6"' : ""}>${esc(v)}</span>`).join("")}
        <span class="mono st-num" style="color:#7a7a7d">${esc(r.amount)}</span>
      </div>`).join("");

    const d = st.asOf;
    $("strate-asof").textContent = `${d.slice(0, 4)}.${d.slice(4, 6)}.${d.slice(6)} 고시`;
    $("strate-foot").textContent = "단위 % · 만기구간별 가중평균금리 · 거래가 거의 없는 구간의 금리는 참고치";
  }

  // -- 채권 수익률 곡선 --------------------------------------------------

  function renderBondCurve() {
    if (!latest) return;
    const bc = latest.bondCurve;
    const table = $("curve-table");
    if (!bc || !bc.curves.length) {
      table.innerHTML = `<div style="font-size:12px;color:#98989b;padding:10px 0">수익률 곡선을 불러오지 못했습니다.</div>`;
      $("curve-asof").textContent = "";
      $("curve-chart").innerHTML = "";
      return;
    }

    const head = `<div class="cv-row cv-head"><span>종류</span>${
      bc.tenors.map((t) => `<span>${esc(t)}</span>`).join("")}</div>`;

    const rows = bc.curves.map((c, ci) => {
      const byTenor = new Map(c.points.map((p) => [p.label, p]));
      const cells = bc.tenors.map((t) => {
        const p = byTenor.get(t);
        if (!p) return `<span class="mono cv-val" style="color:#c4c4c6">—</span>`;
        // 국고채(기준) 행은 스프레드가 0이므로 값만 보여준다.
        const spread = p.spread == null ? "" : `<small>${p.spread >= 0 ? "+" : ""}${p.spread}bp</small>`;
        return `<span class="mono cv-val">${esc(p.value)}${spread}</span>`;
      }).join("");
      return `<div class="cv-row${ci === 0 ? " cv-base" : ""}"><span class="cv-name">${esc(c.label)}</span>${cells}</div>`;
    }).join("");

    table.innerHTML = head + rows;
    const d = bc.asOf;
    $("curve-asof").textContent = d.length === 8 ? `${d.slice(0, 4)}.${d.slice(4, 6)}.${d.slice(6)} 고시` : d;
    $("curve-foot").textContent = "단위 % · 값 아래는 국고채권 대비 스프레드"
      + (bc.stale ? " · 갱신 실패(직전 값)" : "");

    // 종류 선택 탭은 서버가 준 커브 목록에서 만든다 (종류가 바뀌어도 따라간다)
    const seg = $("curve-seg");
    if (seg.dataset.keys !== bc.curves.map((c) => c.key).join("|")) {
      seg.dataset.keys = bc.curves.map((c) => c.key).join("|");
      seg.innerHTML = bc.curves.map((c, i) => `
        <label class="seg-opt" style="padding:3px 10px;font-size:11.5px">
          <input type="radio" name="curvesel" value="${esc(c.key)}"${i === 0 ? " checked" : ""}><span>${esc(c.label)}</span>
        </label>`).join("");
      seg.querySelectorAll("input").forEach((el) => {
        el.addEventListener("change", () => { curveKey = el.value; renderBondCurve(); });
      });
      if (!bc.curves.some((c) => c.key === curveKey)) curveKey = bc.curves[0].key;
    }

    renderCurveChart(bc.curves.find((c) => c.key === curveKey) || bc.curves[0]);
  }

  // 만기(x)에 대한 수익률(y) 한 줄짜리 선 그래프. 계열이 하나뿐이라 색은
  // 식별이 아니라 강조만 하고, 범례 없이 제목이 무엇을 그린 건지 말한다.
  function renderCurveChart(curve) {
    const box = $("curve-chart");
    $("curve-chart-label").textContent = `· ${curve.label}`;
    const pts = curve.points;
    if (pts.length < 2) { box.innerHTML = ""; return; }

    const W = FLOW_W, H = 210, P = { l: 52, r: 16, t: 18, b: 34 };
    const x0 = P.l, x1 = W - P.r, y0 = P.t, y1 = H - P.b;

    const ys = pts.map((p) => p.yield);
    const lo = Math.min(...ys), hi = Math.max(...ys);
    const pad = (hi - lo) * 0.18 || 0.2;
    const top = hi + pad, bottom = Math.max(0, lo - pad);
    const yFor = (v) => y1 - ((v - bottom) / (top - bottom)) * (y1 - y0);
    // 만기 간격이 3개월~20년으로 극단적이라 실제 연수로 두면 앞쪽이 뭉친다.
    // 순서만 유지해 등간격으로 편다.
    const xFor = (i) => x0 + (i / (pts.length - 1)) * (x1 - x0);

    let grid = "";
    for (let i = 0; i <= 3; i++) {
      const v = bottom + ((top - bottom) * i) / 3;
      const y = yFor(v);
      grid += `<line x1="${x0}" y1="${y}" x2="${x1}" y2="${y}" stroke="#dedee0" stroke-width="1"/>`
        + `<text x="${x0 - 8}" y="${y + 3.5}" text-anchor="end" font-size="10.5" fill="#98989b">${v.toFixed(2)}</text>`;
    }

    const line = pts.map((p, i) => `${i ? "L" : "M"}${xFor(i)} ${yFor(p.yield)}`).join(" ");
    const area = `${line} L${xFor(pts.length - 1)} ${y1} L${x0} ${y1} Z`;

    const marks = pts.map((p, i) => {
      const cx = xFor(i), cy = yFor(p.yield);
      return `
        <g>
          <title>${esc(p.label)} ${esc(p.value)}%</title>
          <circle cx="${cx}" cy="${cy}" r="8" fill="transparent"/>
          <circle cx="${cx}" cy="${cy}" r="4" fill="#5980a6" stroke="#f2f2f3" stroke-width="2"/>
          <text x="${cx}" y="${cy - 12}" text-anchor="middle" font-size="10.5" fill="#5d5d60">${esc(p.value)}</text>
          <text x="${cx}" y="${y1 + 17}" text-anchor="middle" font-size="10.5" fill="#5d5d60">${esc(p.label)}</text>
        </g>`;
    }).join("");

    box.innerHTML = `
      <svg viewBox="0 0 ${W} ${H}" width="100%" height="${H}" preserveAspectRatio="xMidYMid meet"
           role="img" aria-label="${esc(curve.label)} 만기별 수익률">
        ${grid}
        <path d="${area}" fill="rgba(89,128,166,.10)"/>
        <path d="${line}" fill="none" stroke="#5980a6" stroke-width="2" stroke-linejoin="round" stroke-linecap="round"/>
        ${marks}
      </svg>`;
  }

  // -- 외화표시채 발행 ---------------------------------------------------

  function renderFxBondIssues() {
    if (!latest) return;
    const rows = latest.fxBondIssues || [];
    $("fxbond-empty").style.display = rows.length ? "none" : "block";
    $("fxbond-count").textContent = rows.length ? `${rows.length}건` : "";

    const head = `<div class="fb-row fb-head">
      <span>발행일</span><span>발행사</span><span>종목</span><span>통화</span>
      <span class="fb-num">발행액</span><span class="fb-num">쿠폰</span><span class="fb-num">만기</span></div>`;

    $("fxbond-list").innerHTML = head + rows.map((r) => `
      <div class="fb-row">
        <span class="mono" style="color:#7a7a7d">${esc(r.issueDate)}</span>
        <span class="fb-name" style="font-weight:500">${esc(r.issuer)}</span>
        <span class="fb-name" style="color:#5d5d60" title="${esc(r.name)}">${esc(r.name)}</span>
        <span class="mono" style="font-size:11px">${esc(r.currency)}</span>
        <span class="mono fb-num">${esc(r.amount)}</span>
        <span class="mono fb-num">${esc(r.coupon)}</span>
        <span class="mono fb-num" style="color:#7a7a7d">${esc(r.maturity)}</span>
      </div>`).join("");
  }

  // -- 코스피200 현·선물 베이시스 ---------------------------------------

  function renderBasis() {
    if (!latest) return;
    const b = latest.basis;
    const body = $("basis-body");
    const foot = $("basis-foot");

    if (!b) {
      body.innerHTML = `<div style="font-size:12px;color:#98989b;padding:10px 0">베이시스를 불러오지 못했습니다.</div>`;
      foot.textContent = "";
      return;
    }

    const stat = (label, value, color) =>
      `<div class="bs-stat"><span>${esc(label)}</span><b class="mono"${color ? ` style="color:${color}"` : ""}>${esc(value)}</b></div>`;

    body.innerHTML = `
      <div class="bs-big">
        <b class="mono" style="color:${b.basisColor}">${esc(b.basis)}</b>
        <span class="bs-tag" style="color:${b.basisColor}">${esc(b.state)}</span>
        <span style="font-size:11px;color:#98989b;margin-left:auto">베이시스 (선물−현물)</span>
      </div>
      <div class="bs-stats">
        ${stat("현물 KOSPI200", b.spot)}
        ${stat(`선물 ${b.contract ? `(${b.contract})` : ""}`.trim(), b.futures)}
        ${stat("이론가", b.theoretical)}
        ${stat("이론 베이시스", b.theoBasis)}
        ${stat("괴리율", `${b.spread} · ${b.valuation}`, b.spreadColor)}
        ${stat("만기까지", b.daysToExpiry != null ? `${b.daysToExpiry}일 (${b.expiry})` : "—")}
      </div>`;

    foot.textContent = `${b.stamp}\n${b.assumption}`;
  }

  // -- 키워드 뉴스 -------------------------------------------------------

  function renderKeywordNews() {
    if (!latest) return;
    const groups = latest.keywordNews || {};
    const group = groups[kwNewsGroup];
    const items = (group && group.items) || [];

    // 관련도순 상위 KW_NEWS_PAGES 페이지까지만 노출한다. 뒤로 갈수록
    // 주제와 멀어지는 목록이라 끝까지 넘기게 두는 게 의미가 없다.
    const pool = items.slice(0, KW_NEWS_PAGE_SIZE * KW_NEWS_PAGES);
    const totalPages = Math.max(1, Math.ceil(pool.length / KW_NEWS_PAGE_SIZE));
    if (kwNewsPage > totalPages) kwNewsPage = totalPages;
    const start = (kwNewsPage - 1) * KW_NEWS_PAGE_SIZE;
    const shown = pool.slice(start, start + KW_NEWS_PAGE_SIZE);

    // 검색어가 하나뿐인 그룹은 모든 줄에 같은 태그가 붙어 정보가 없다.
    // 검색어가 섞인 그룹(M&A = M&A + 인수합병)에서만 어느 쪽에 걸렸는지 표시.
    const showHit = new Set(shown.map((n) => n.hit)).size > 1;

    const empty = $("kwnews-empty");
    empty.style.display = items.length ? "none" : "block";
    if (!items.length) {
      // 클라우드에서 네이버 검색이 IP로 막히면 영영 안 채워진다. 그때
      // "불러오는 중"만 띄우면 고장인 걸 알아챌 수가 없다.
      empty.textContent = latest.keywordNewsStatus === "failed"
        ? "네이버 뉴스 검색에 접근하지 못했습니다 (서버 IP 차단 가능성 · NAVER_CLIENT_ID/SECRET 설정 시 공식 API로 전환)"
        : "키워드 뉴스를 불러오는 중입니다.";
    }
    $("kwnews-list").innerHTML = shown.map((n) => `
      <div class="kwn-row">
        <span class="mono kwn-when">${esc(n.when)}</span>
        <div style="font-size:12.5px;line-height:1.4;text-wrap:pretty">
          <a href="${esc(n.url)}" target="_blank" rel="noopener" style="color:inherit;text-decoration:none">${esc(n.headline)}</a>
          ${showHit ? `<span class="tag tag-accent" style="font-size:9px;padding:0 6px;vertical-align:middle">${esc(n.hit)}</span>` : ""}
          ${n.press ? `<span style="font-size:10.5px;color:#98989b;margin-left:5px">${esc(n.press)}</span>` : ""}
        </div>
      </div>`).join("");

    renderPagination("kwnews-pagination", kwNewsPage, totalPages, (p) => {
      kwNewsPage = p;
      renderKeywordNews();
    });
  }

  // Clicking any stock row (movers lists) opens the detail popup.
  // Event delegation on the containers survives the innerHTML re-renders.
  for (const containerId of ["movers-list", "kr-most-traded-list", "us-movers-list", "us-most-active-list"]) {
    $(containerId).addEventListener("click", (ev) => {
      if (ev.target.closest("button")) return;
      const row = ev.target.closest("[data-symbol]");
      if (row && row.dataset.symbol) openStockPopup(row.dataset.symbol, row.dataset.name);
    });
  }

  // Same pattern for FX / index / commodity / rate rows -> the generic
  // indicator popup (kind carried in data-kind).
  for (const containerId of ["ticker-strip", "fx-regions", "idx-main", "rates-list", "kr-rates-list", "commodities-list"]) {
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

  // 번호 페이저 — 주요 뉴스와 키워드 뉴스가 같이 쓴다. onPick 은 고른
  // 페이지 번호를 받아 해당 패널을 다시 그린다.
  function renderPagination(boxId, current, totalPages, onPick) {
    const box = $(boxId);
    if (totalPages <= 1) { box.innerHTML = ""; box.style.display = "none"; return; }
    box.style.display = "flex";
    const btn = (label, page, opts = {}) => `
      <button type="button" data-page="${page}" ${opts.disabled ? "disabled" : ""}
        style="min-width:26px;padding:4px 8px;font-size:11.5px;border:1px solid var(--color-divider);background:${page === current ? "var(--color-accent)" : "transparent"};color:${page === current ? "#fff" : "inherit"};cursor:${opts.disabled ? "default" : "pointer"}">${label}</button>`;

    let pages = [];
    for (let p = 1; p <= totalPages; p++) pages.push(p);
    box.innerHTML = btn("‹", Math.max(1, current - 1), { disabled: current === 1 })
      + pages.map((p) => btn(p, p)).join("")
      + btn("›", Math.min(totalPages, current + 1), { disabled: current === totalPages });

    box.querySelectorAll("[data-page]").forEach((el) => {
      el.addEventListener("click", () => {
        if (el.disabled) return;
        onPick(Number(el.dataset.page));
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
    renderPagination("news-pagination", newsPage, totalPages, (p) => { newsPage = p; renderNews(); });
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
    renderKrRates(snapshot.krRates);
    renderFxNews(snapshot.fxNews);
    renderNews();
    renderInvestorFlow();
    renderBondFlow();
    renderBondQuotes();
    renderShortTermRates();
    renderBondCurve();
    renderFxBondIssues();
    renderBasis();
    renderKeywordNews();
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

  for (const market of ["kospi", "kosdaq", "futures"]) {
    $(`flow-${market}`).addEventListener("change", () => { flowMarket = market; renderInvestorFlow(); });
  }
  for (const period of ["1d", "1w", "1m", "3m"]) {
    $(`flowp-${period}`).addEventListener("change", () => { flowChartPeriod = period; renderInvestorFlow(); });
  }
  for (const period of ["1d", "1w", "1m", "3m"]) {
    $(`bflowp-${period}`).addEventListener("change", () => { bondFlowPeriod = period; renderBondFlow(); });
  }
  for (const group of ["fxbond", "ma", "blockdeal", "dividend"]) {
    $(`kwn-${group}`).addEventListener("change", () => {
      kwNewsGroup = group;
      kwNewsPage = 1;   // 탭을 바꾸면 3페이지째에 머물러 있을 이유가 없다
      renderKeywordNews();
    });
  }

  // -- global search (header): 지표 + 종목 통합 검색 --------------------

  const gsInput = $("global-search");
  const gsResults = $("global-search-results");
  let gsDebounce = null;
  let gsItems = [];   // flattened, in display order
  let gsSeq = 0;      // discard out-of-order async results

  // The board's own indicators (FX / indices / rates / commodities) are
  // searched client-side from the latest snapshot; stocks come from the
  // Naver autocomplete proxy (/api/search).
  function indicatorIndex() {
    if (!latest) return [];
    const out = [];
    (latest.fxRegions || []).forEach((g) => (g.rows || []).forEach((r) => out.push({
      type: "ind", kind: "fx", symbol: r.symbol, label: r.pair, sub: r.name,
      tag: "환율", data: { name: r.name, pair: r.pair },
    })));
    (latest.idxMain || []).forEach((r) => out.push({
      type: "ind", kind: "index", symbol: r.symbol, label: r.name, sub: "",
      tag: "지수", data: { name: r.name },
    }));
    (latest.rates || []).forEach((r) => out.push({
      type: "ind", kind: "rate", symbol: r.symbol, label: r.name, sub: r.sub,
      tag: "미국 금리", data: { name: r.name, sub: r.sub },
    }));
    (latest.krRates || []).forEach((r) => out.push({
      type: "ind", kind: "krrate", symbol: r.code, label: r.name, sub: "",
      tag: "한국 금리", data: { name: r.name },
    }));
    (latest.commAll || []).forEach((r) => out.push({
      type: "ind", kind: "commodity", symbol: r.symbol, label: r.name, sub: r.contract,
      tag: "원자재", data: { name: r.name, contract: r.contract },
    }));
    return out;
  }

  function gsHide() { gsResults.style.display = "none"; gsResults.innerHTML = ""; gsItems = []; }

  function gsRender(indMatches, stockMatches) {
    gsItems = [...indMatches, ...stockMatches];
    if (!gsItems.length) {
      gsResults.innerHTML = '<div style="padding:10px;font-size:12px;color:#98989b">검색 결과 없음</div>';
      gsResults.style.display = "block";
      return;
    }
    const secLabel = (t) => `<div style="padding:6px 10px 3px;font-size:9.5px;letter-spacing:.12em;text-transform:uppercase;color:var(--color-accent);border-bottom:1px solid var(--color-divider)">${t}</div>`;
    const row = (it, i) => `
      <div class="gs-row" data-i="${i}" style="padding:8px 10px;font-size:12.5px;cursor:pointer;display:flex;justify-content:space-between;align-items:baseline;gap:8px;border-bottom:1px solid rgba(29,31,32,.06)">
        <span>${esc(it.label)}${it.sub ? ` <span style="color:#98989b;font-size:11px">${esc(it.sub)}</span>` : ""}</span>
        <span style="color:#98989b;font-size:11px;white-space:nowrap">${esc(it.tag)}</span>
      </div>`;
    let html = "";
    let i = 0;
    if (indMatches.length) {
      html += secLabel("지표 Indicators");
      indMatches.forEach((it) => { html += row(it, i++); });
    }
    if (stockMatches.length) {
      html += secLabel("종목 Stocks");
      stockMatches.forEach((it) => { html += row(it, i++); });
    }
    gsResults.innerHTML = html;
    gsResults.style.display = "block";
    gsResults.querySelectorAll(".gs-row").forEach((el) => {
      el.addEventListener("mouseenter", () => { el.style.background = "color-mix(in srgb, var(--color-text) 6%, transparent)"; });
      el.addEventListener("mouseleave", () => { el.style.background = ""; });
    });
  }

  function gsOpen(it) {
    gsHide();
    gsInput.value = "";
    gsInput.blur(); // 모바일: 키보드 닫고 입력 포커스 줌 해제
    if (it.type === "stock") openStockPopup(it.symbol, it.name);
    else openIndicatorPopup(it.kind, it.symbol, it.data);
  }

  async function gsSearch(q) {
    const seq = ++gsSeq;
    const needle = q.toLowerCase();
    const indMatches = indicatorIndex().filter((it) =>
      it.label.toLowerCase().includes(needle)
      || (it.sub || "").toLowerCase().includes(needle)
      || it.symbol.toLowerCase().includes(needle)
    ).slice(0, 6);

    // 지표 결과는 로컬 매칭이라 즉시 보여주고, 종목 결과는 도착하는
    // 대로 아래에 덧붙인다 — 네이버 응답을 기다렸다 한꺼번에 그리면
    // 체감이 느리다.
    if (indMatches.length) gsRender(indMatches, []);

    let stockMatches = [];
    try {
      const res = await fetch(`/api/search?q=${encodeURIComponent(q)}`);
      if (res.ok) {
        stockMatches = (await res.json()).map((it) => ({
          type: "stock", symbol: it.symbol, name: it.name,
          label: it.name, sub: `${it.market} · ${it.symbol}`, tag: "종목",
        }));
      }
    } catch (e) { console.error("stock search failed", e); }

    if (seq !== gsSeq || gsInput.value.trim() !== q) return; // stale response
    gsRender(indMatches, stockMatches);
  }

  gsInput.addEventListener("input", () => {
    clearTimeout(gsDebounce);
    const q = gsInput.value.trim();
    if (!q) { gsHide(); return; }
    gsDebounce = setTimeout(() => gsSearch(q), 150);
  });

  gsInput.addEventListener("keydown", (ev) => {
    if (ev.key === "Escape") { gsHide(); return; }
    if (ev.key !== "Enter") return;
    if (ev.isComposing || ev.keyCode === 229) return; // IME 조합 중 Enter 무시
    ev.preventDefault();
    if (gsItems.length) gsOpen(gsItems[0]);
  });

  gsResults.addEventListener("click", (ev) => {
    const el = ev.target.closest(".gs-row");
    if (el) gsOpen(gsItems[Number(el.dataset.i)]);
  });

  document.addEventListener("click", (ev) => {
    if (!gsResults.contains(ev.target) && ev.target !== gsInput) gsHide();
  });

  // -- popup chart: 640x132 고정 viewBox에 path만 갈아끼운다 (popup.html) --

  const CHART_W = 640, CHART_H = 132, CHART_PAD = 8;
  const CHART_IDS = {
    stk: { label: "stk-chart-label", hilo: "stk-hilo" },
    ind: { label: "ind-chart-label", hilo: "ind-chart-range" },
  };
  // Which instrument each popup's range buttons should fetch for.
  const chartCtx = { stk: null, ind: null };

  function drawPopChart(prefix, values) {
    const line = $(prefix + "-line"), area = $(prefix + "-area");
    const cross = $(prefix + "-cross"), dot = $(prefix + "-dot");
    if (!values || values.length < 2) {
      line.setAttribute("d", "");
      area.setAttribute("d", "");
      cross.style.display = "none";
      dot.style.display = "none";
      return;
    }
    cross.style.display = "";
    dot.style.display = "";
    const lo = Math.min(...values), hi = Math.max(...values), span = hi - lo;
    // 보합(모든 값 동일)이면 중앙에 수평선으로 그린다.
    const pt = (v, i) => [
      (i / (values.length - 1)) * CHART_W,
      span === 0 ? CHART_H / 2 : CHART_PAD + (1 - (v - lo) / span) * (CHART_H - CHART_PAD * 2),
    ];
    const d = values.map((v, i) => {
      const p = pt(v, i);
      return (i ? "L" : "M") + p[0].toFixed(1) + " " + p[1].toFixed(1);
    }).join(" ");
    const last = pt(values[values.length - 1], values.length - 1);
    line.setAttribute("d", d);
    area.setAttribute("d", `${d} L${CHART_W} ${CHART_H} L0 ${CHART_H} Z`);
    cross.setAttribute("x1", last[0]);
    cross.setAttribute("x2", last[0]);
    dot.setAttribute("cx", last[0]);
    dot.setAttribute("cy", last[1]);
  }

  function applyChart(prefix, chart) {
    const ids = CHART_IDS[prefix];
    if (chart && chart.values && chart.values.length >= 2) {
      drawPopChart(prefix, chart.values);
      $(ids.label).textContent = chart.label || "";
      $(ids.hilo).textContent = chart.hiloText || "";
      $(prefix + "-axis-l").textContent = chart.axisL || "";
      $(prefix + "-axis-r").textContent = chart.axisR || "";
    } else {
      drawPopChart(prefix, null);
      $(ids.hilo).textContent = "차트 데이터 없음";
      $(prefix + "-axis-l").textContent = "";
      $(prefix + "-axis-r").textContent = "";
    }
  }

  function setRangePressed(groupId, range) {
    $(groupId).querySelectorAll("button").forEach((b) =>
      b.setAttribute("aria-pressed", String(b.dataset.range === range)));
  }

  function wireRangeButtons(prefix, groupId) {
    $(groupId).addEventListener("click", async (ev) => {
      const btn = ev.target.closest("button");
      if (!btn) return;
      const ctx = chartCtx[prefix];
      if (!ctx) return;
      const range = btn.dataset.range;
      setRangePressed(groupId, range);
      try {
        const res = await fetch(`/api/chart/${ctx.kind}/${encodeURIComponent(ctx.symbol)}?range=${range}`);
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const chart = await res.json();
        // 그 사이 다른 항목/레인지로 넘어갔으면 버린다.
        if (chartCtx[prefix] !== ctx) return;
        const pressed = $(groupId).querySelector('button[aria-pressed="true"]');
        if (pressed && pressed.dataset.range !== range) return;
        applyChart(prefix, chart);
      } catch (e) {
        console.error("chart range fetch failed", e);
        if (chartCtx[prefix] === ctx) applyChart(prefix, null);
      }
    });
  }
  wireRangeButtons("stk", "stk-range");
  wireRangeButtons("ind", "ind-range");

  function nowAsOf() {
    const now = new Date();
    const kst = new Date(now.toLocaleString("en-US", { timeZone: "Asia/Seoul" }));
    const p = (n) => String(n).padStart(2, "0");
    return `기준 ${p(kst.getHours())}:${p(kst.getMinutes())}:${p(kst.getSeconds())} KST`;
  }

  function popupNewsHtml(news) {
    if (!news || !news.length) return '<div style="font-size:12px;color:#98989b;padding:6px 0">관련 뉴스 없음</div>';
    return news.map((n) => `
      <div class="pop-news-row">
        ${n.url
          ? `<a href="${esc(n.url)}" target="_blank" rel="noopener">${esc(n.headline)}</a>`
          : `<span style="font-family:var(--font-body);font-size:12.5px;line-height:1.4;color:#1d1f20;white-space:normal">${esc(n.headline)}</span>`}
      </div>`).join("");
  }

  // -- stock detail popup ------------------------------------------------

  const popupBackdrop = $("stock-popup-backdrop");
  let currentPopupSymbol = null;

  async function openStockPopup(symbol, name) {
    currentPopupSymbol = symbol;
    chartCtx.stk = { kind: "stock", symbol };
    popupBackdrop.hidden = false;
    setRangePressed("stk-range", "1D");
    $("stk-name").textContent = name || symbol;
    $("stk-code").textContent = "";
    $("stk-market").textContent = "";
    $("stk-price").textContent = "…";
    $("stk-price").style.color = "";
    $("stk-chg").textContent = "";
    $("stk-asof").textContent = "";
    $("stk-source").textContent = "";
    $("stk-chart-label").textContent = "당일 분봉 · Intraday";
    $("stk-hilo").textContent = "";
    $("stk-axis-l").textContent = "";
    $("stk-axis-r").textContent = "";
    drawPopChart("stk", null);
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
      $("stk-asof").textContent = nowAsOf();
      const isKr = symbol.endsWith(".KS") || symbol.endsWith(".KQ");
      $("stk-source").textContent = isKr ? "Naver Finance · 실시간" : "Yahoo Finance · 실시간";
      applyChart("stk", d.chart);

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

      $("stk-news").innerHTML = popupNewsHtml(d.news);
    } catch (e) {
      console.error("stock detail fetch failed", e);
      $("stk-price").textContent = "오류";
      applyChart("stk", null);
    }
  }

  function closeStockPopup() {
    currentPopupSymbol = null;
    chartCtx.stk = null;
    popupBackdrop.hidden = true;
  }

  $("stk-close").addEventListener("click", closeStockPopup);
  $("stk-close-btn").addEventListener("click", closeStockPopup);
  popupBackdrop.addEventListener("click", (ev) => { if (ev.target === popupBackdrop) closeStockPopup(); });
  document.addEventListener("keydown", (ev) => { if (ev.key === "Escape" && !popupBackdrop.hidden) closeStockPopup(); });

  // -- indicator (FX / index / commodity / 미국·한국 금리) detail popup ----

  const indicatorBackdrop = $("indicator-popup-backdrop");
  let currentIndicatorKey = null;

  const INDICATOR_SOURCES = {
    fx: "Yahoo Finance · 실시간",
    index: "Yahoo Finance · 실시간",
    commodity: "Yahoo Finance · 실시간",
    rate: "Yahoo Finance · 실시간",
    krrate: "Naver Finance · 마켓인덱스",
  };
  const NAVER_INDEX_SYMBOLS = ["^KS11", "^KQ11", "^KS200"];

  function renderIndicatorStats(stats) {
    $("ind-stats").innerHTML = stats.map((s) => `
      <div class="stat"><span>${esc(s.label)}</span><b${s.color ? ` style="color:${s.color}"` : ""}>${esc(s.value)}</b></div>
    `).join("");
  }

  async function openIndicatorPopup(kind, symbol, data) {
    const key = `${kind}:${symbol}`;
    currentIndicatorKey = key;
    chartCtx.ind = { kind, symbol };
    indicatorBackdrop.hidden = false;
    setRangePressed("ind-range", "1D");
    $("ind-title").textContent = data.pair || data.name || symbol;
    $("ind-subtitle").textContent = kind === "fx" ? (data.name || "") : (data.sub || "");
    $("ind-tag").textContent = "";
    $("ind-price").textContent = "…";
    $("ind-price").style.color = "";
    $("ind-chg").textContent = "";
    $("ind-asof").textContent = "";
    $("ind-source").textContent = "";
    $("ind-chart-label").textContent = "";
    $("ind-chart-range").textContent = "";
    $("ind-axis-l").textContent = "";
    $("ind-axis-r").textContent = "";
    drawPopChart("ind", null);
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
      $("ind-asof").textContent = nowAsOf();
      $("ind-source").textContent = (kind === "index" && NAVER_INDEX_SYMBOLS.includes(symbol))
        ? "Naver Finance · 실시간" : (INDICATOR_SOURCES[kind] || "");
      applyChart("ind", d.chart);
      renderIndicatorStats(d.stats || []);

      if (d.news && d.news.length) {
        $("ind-news-wrap").style.display = "";
        $("ind-news").innerHTML = popupNewsHtml(d.news);
      }
    } catch (e) {
      console.error("indicator detail fetch failed", e);
      $("ind-price").textContent = "오류";
      applyChart("ind", null);
    }
  }

  function closeIndicatorPopup() {
    currentIndicatorKey = null;
    chartCtx.ind = null;
    indicatorBackdrop.hidden = true;
  }

  $("ind-close").addEventListener("click", closeIndicatorPopup);
  $("ind-close-btn").addEventListener("click", closeIndicatorPopup);
  indicatorBackdrop.addEventListener("click", (ev) => { if (ev.target === indicatorBackdrop) closeIndicatorPopup(); });
  document.addEventListener("keydown", (ev) => { if (ev.key === "Escape" && !indicatorBackdrop.hidden) closeIndicatorPopup(); });

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

  // -- SEIBro 미국 주식 보관금액 (월별) ----------------------------------

  // USD 금액을 "1,493억 달러" 형태로. (억 = 1e8)
  function fmtCustody(v) {
    const eok = v / 1e8;
    return eok.toLocaleString("ko-KR", { maximumFractionDigits: 0 }) + "억 달러";
  }
  function fmtUsdShort(v) {
    return "$" + (v / 1e9).toFixed(1) + "B";
  }
  function fmtPct(p) {
    return (p >= 0 ? "+" : "") + p.toFixed(1) + "%";
  }
  function pctColor(p) {
    return p > 0 ? "var(--color-accent)" : p < 0 ? "#c0392b" : "#7a7a7d";
  }

  function statTile(label, value, sub, subColor) {
    return `<div style="display:flex;flex-direction:column;gap:2px">
      <span style="font-size:10px;letter-spacing:.06em;color:#7a7a7d;text-transform:uppercase">${esc(label)}</span>
      <span class="mono" style="font-size:19px;font-weight:600;line-height:1.1">${esc(value)}</span>
      ${sub ? `<span class="mono" style="font-size:11.5px;color:${subColor || "#7a7a7d"}">${esc(sub)}</span>` : ""}
    </div>`;
  }

  // CSS 변수는 SVG 프레젠테이션 속성(fill/stroke)에 적용되지 않으므로 실제 색으로 변환.
  function cssVar(name, fallback) {
    const v = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
    return v || fallback;
  }

  function custodyChartSvg(points, w = 1000, h = 240) {
    const padL = 34, padR = 34, padT = 34, padB = 30;
    const accent = cssVar("--color-accent", "#5980a6");
    const divider = cssVar("--color-divider", "#d7d7d9");
    const bg = "#f2f2f3";
    const amts = points.map((p) => p.amount);
    const lo = Math.min(...amts), hi = Math.max(...amts);
    const rng = (hi - lo) || 1;
    const iw = w - padL - padR, ih = h - padT - padB;
    const n = points.length;
    const xs = (i) => padL + (n === 1 ? iw / 2 : (i / (n - 1)) * iw);
    const ys = (v) => padT + ih - ((v - lo) / rng) * ih;

    const coords = points.map((p, i) => [xs(i), ys(p.amount)]);
    const line = coords.map(([x, y], i) => `${i === 0 ? "M" : "L"}${x.toFixed(1)} ${y.toFixed(1)}`).join(" ");
    const area = `${line} L ${(padL + iw).toFixed(1)} ${(padT + ih).toFixed(1)} L ${padL.toFixed(1)} ${(padT + ih).toFixed(1)} Z`;

    // 가로 기준선 (min/max)
    const grid = [padT, padT + ih].map(
      (y) => `<line x1="${padL}" y1="${y}" x2="${padL + iw}" y2="${y}" stroke="${divider}" stroke-width="1"/>`
    ).join("");

    // 데이터 포인트 (기본은 빈 점)
    const dots = coords.map(([x, y]) =>
      `<circle cx="${x.toFixed(1)}" cy="${y.toFixed(1)}" r="2.8" fill="${bg}" stroke="${accent}" stroke-width="1.4"/>`
    ).join("");

    // x축 월 라벨 (YY.MM)
    const labels = points.map((p, i) => {
      const [yy, mm] = p.month.split("-");
      return `<text x="${coords[i][0].toFixed(1)}" y="${(h - 8).toFixed(1)}" text-anchor="middle" font-size="10" fill="#98989b" font-family="monospace">${yy.slice(2)}.${mm}</text>`;
    }).join("");

    // 마우스 감지용 세로 밴드 (점 사이 전 구간을 커버) — hover 시 값 표시
    const hit = coords.map(([x, y], i) => {
      const left = i === 0 ? 0 : (coords[i - 1][0] + x) / 2;
      const right = i === n - 1 ? w : (coords[i + 1][0] + x) / 2;
      return `<rect class="custody-hit" x="${left.toFixed(1)}" y="0" width="${(right - left).toFixed(1)}" height="${h}" fill="transparent"
        data-x="${x.toFixed(1)}" data-y="${y.toFixed(1)}" data-amount="${points[i].amount}" data-month="${points[i].month}"/>`;
    }).join("");

    // hover 표시 그룹 (JS로 위치·텍스트 갱신)
    const hover = `<g class="custody-hover" style="display:none" pointer-events="none">
      <circle class="hv-dot" r="4.5" fill="${accent}" stroke="${bg}" stroke-width="1.6"/>
      <text class="hv-val" text-anchor="middle" font-family="monospace" font-size="13" font-weight="700" fill="${accent}"
        style="paint-order:stroke;stroke:${bg};stroke-width:3.5px;stroke-linejoin:round"></text>
      <text class="hv-month" text-anchor="middle" font-family="monospace" font-size="10" fill="#7a7a7d"
        style="paint-order:stroke;stroke:${bg};stroke-width:3px;stroke-linejoin:round"></text>
    </g>`;

    return `<svg viewBox="0 0 ${w} ${h}" style="display:block;width:100%;height:auto">
      ${grid}
      <path d="${area}" fill="${accent}" fill-opacity="0.10"></path>
      <path d="${line}" fill="none" stroke="${accent}" stroke-width="1.8" stroke-linejoin="round" stroke-linecap="round"></path>
      ${dots}
      ${labels}
      ${hover}
      ${hit}
    </svg>`;
  }

  // 차트 삽입 후 hover 상호작용 연결.
  function wireCustodyHover(chartEl, w = 1000) {
    const svg = chartEl.querySelector("svg");
    if (!svg) return;
    const hover = svg.querySelector(".custody-hover");
    const dot = svg.querySelector(".hv-dot");
    const val = svg.querySelector(".hv-val");
    const mon = svg.querySelector(".hv-month");

    svg.querySelectorAll(".custody-hit").forEach((band) => {
      const show = () => {
        const x = parseFloat(band.dataset.x);
        const y = parseFloat(band.dataset.y);
        const amount = parseFloat(band.dataset.amount);
        const tx = Math.max(46, Math.min(w - 46, x));   // 라벨 좌우 잘림 방지
        const ty = Math.max(16, y - 16);
        dot.setAttribute("cx", x);
        dot.setAttribute("cy", y);
        val.setAttribute("x", tx);
        val.setAttribute("y", ty);
        val.textContent = fmtCustody(amount);
        mon.setAttribute("x", tx);
        mon.setAttribute("y", ty - 12);
        mon.textContent = band.dataset.month;
        hover.style.display = "";
      };
      band.addEventListener("mouseenter", show);
      band.addEventListener("mousemove", show);
      band.addEventListener("mouseleave", () => { hover.style.display = "none"; });
    });
  }

  function renderCustody(d) {
    const empty = $("custody-empty"), chart = $("custody-chart"), stats = $("custody-stats");
    if (!d || !d.points || d.points.length === 0 || !d.stats) {
      chart.innerHTML = "";
      stats.innerHTML = "";
      empty.style.display = "block";
      return;
    }
    empty.style.display = "none";
    $("custody-source").textContent = d.source || "";

    const s = d.stats;
    const latestMonth = s.latest.month;
    const firstMonth = d.points[0].month;
    stats.innerHTML = [
      statTile(`최신 (${latestMonth})`, fmtCustody(s.latest.amount), fmtUsdShort(s.latest.amount)),
      statTile("전월 대비", fmtPct(s.changePct), `${s.change >= 0 ? "+" : ""}${fmtCustody(Math.abs(s.change)).replace("억 달러", "")}억`, pctColor(s.changePct)),
      statTile(`1년 증감 (${firstMonth}→)`, fmtPct(s.yoyPct), fmtUsdShort(Math.abs(s.yoyChange)), pctColor(s.yoyPct)),
      statTile("최고 / 최저", `${fmtUsdShort(s.max.amount)} / ${fmtUsdShort(s.min.amount)}`, `${s.max.month} / ${s.min.month}`),
    ].join("");

    chart.innerHTML = custodyChartSvg(d.points);
    wireCustodyHover(chart);
  }

  async function loadCustody() {
    try {
      const r = await fetch("/api/seibro-custody");
      if (!r.ok) throw new Error(r.status);
      renderCustody(await r.json());
    } catch (e) {
      console.error("custody load failed", e);
      renderCustody(null);
    }
  }
  loadCustody();
  setInterval(loadCustody, 30 * 60 * 1000); // 월별 데이터 — 30분마다 재확인

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
