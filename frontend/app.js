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

  // -- 한/영 -------------------------------------------------------------
  // 사전은 i18n.js 에 있다. 여기서는 세 가지 통로만 둔다:
  //   t(key)   HTML·app.js 에 박힌 고정 문구
  //   tr(s)    서버가 실어 보낸 라벨 (못 찾으면 원문 그대로)
  //   amt(s)   억·조가 붙은 금액 (영문에서는 자릿수를 다시 접는다)
  // 언어를 바꾸면 스냅샷을 다시 받는 게 아니라 들고 있던 걸로 다시 그린다.
  const I18N = window.FXI18N;
  const LANG_KEY = "fxdesk.lang";
  let lang = "ko";
  try {
    if (localStorage.getItem(LANG_KEY) === "en") lang = "en";
  } catch (e) { /* 사파리 프라이빗 모드 — 기본(한국어)으로 간다 */ }

  const isEn = () => lang === "en";

  function t(key) {
    const e = I18N.STR[key];
    if (!e) return key;
    return e[lang] ?? e.ko;
  }

  function tr(s) {
    if (!isEn()) return String(s ?? "");
    const raw = String(s ?? "");
    const hit = I18N.DYN[raw];
    if (hit !== undefined) return hit;
    // 통째로는 못 찾는 문구 — 한글이 섞여 있을 때만 조각을 갈아 끼운다.
    if (!/[가-힯]/.test(raw)) return raw;
    let out = raw;
    for (const [re, to] of I18N.RULES) out = out.replace(re, to);
    return out;
  }

  function amt(s) {
    return isEn() ? I18N.trAmount(s) : String(s ?? "");
  }

  // 서버가 준 금액 문자열 대신 원값에서 직접 만든다 — 수급 표·차트처럼
  // raw 를 같이 받는 자리에서는 반올림을 두 번 하지 않는 이쪽이 정확하다.
  function fmtFlowValue(raw, unitLabel) {
    if (!isEn()) return null;                       // 한국어는 서버 문자열 그대로
    if (unitLabel === "계약") return `${raw > 0 ? "+" : raw < 0 ? "−" : ""}${Math.abs(Math.round(raw)).toLocaleString("en-US")}`;
    return I18N.fmtWon(raw * 1e8, true);            // 억원 단위로 온다
  }

  // "총 128건" / "128 headlines". 한국어는 수 앞에 단위가 붙고 영어는 뒤에
  // 붙어서 문장 순서 자체가 달라진다 — 조각을 이어 붙이지 않고 통째로 만든다.
  function fmtCount(n, total) {
    if (isEn()) return total ? `${n} headlines` : `${n} issues`;
    return total ? `총 ${n}건` : `${n}건`;
  }

  // -- 언어 전환 ---------------------------------------------------------
  // 스냅샷을 다시 받지 않고 들고 있는 걸로 전부 다시 그린다. 다시 그리기를
  // 건너뛰는 캐시(setHtml 의 __html, 차트 서명, 탭 목록)가 여럿 있어서
  // 그것부터 비워야 한다 — 안 그러면 언어만 그대로 남는 패널이 생긴다.
  let connState = "connecting";

  // 접히는 패널 제목 앞의 꺾쇠. CSS 로 그린 도형이라 내용이 없다
  // (styles 의 .pnl-caret). 제목 자체가 data-i18n 이라 언어를 바꾸면
  // innerHTML 과 함께 쓸려 나간다 — 문구를 갈아 끼운 뒤 다시 심는다.
  function ensureCarets() {
    document.querySelectorAll(".panel.collapsible > .pnl-head").forEach((head) => {
      const host = head.querySelector("h4") || head;
      if (host.querySelector(":scope > .pnl-caret")) return;
      const caret = document.createElement("span");
      caret.className = "pnl-caret";
      caret.setAttribute("aria-hidden", "true");
      host.prepend(caret);
    });
  }

  function applyStaticText() {
    document.documentElement.lang = lang;
    document.querySelectorAll("[data-i18n]").forEach((el) => {
      el.innerHTML = t(el.dataset.i18n);
    });
    ensureCarets();
    document.querySelectorAll("[data-i18n-ph]").forEach((el) => {
      el.placeholder = t(el.dataset.i18nPh);
    });
    document.querySelectorAll("[data-i18n-aria]").forEach((el) => {
      el.setAttribute("aria-label", t(el.dataset.i18nAria));
    });
    // 네이버 한국어 뉴스만 담긴 패널은 영문판에 실을 게 없다.
    document.querySelectorAll("[data-ko-only]").forEach((el) => {
      el.style.display = isEn() ? "none" : "";
    });
    // 판본 버튼("자동/밝게/어둡게")은 data-i18n 이 아니라 테마 스크립트가
    // 그린다 — 지금 무엇이 걸려 있는지에 따라 글자가 달라지기 때문이다.
    if (window.__fxTheme) window.__fxTheme.refresh();
    // 제목 innerHTML 을 통째로 갈아 끼웠다 — motion.js 가 단어 마스크를
    // 새 문구로 다시 잘라 넣게 알린다.
    document.dispatchEvent(new CustomEvent("lang-applied"));
  }

  function resetRenderCaches() {
    document.querySelectorAll("[id]").forEach((el) => { delete el.__html; });
    flowChartKey = "";
    bondFlowChartKey = "";
    const bseg = $("bflow-seg"), cseg = $("curve-seg");
    if (bseg) delete bseg.dataset.keys;
    if (cseg) delete cseg.dataset.keys;
  }

  function applyLang() {
    applyStaticText();
    resetRenderCaches();
    setConn(connState);
    tickClock();
    if (latest) render(latest);
    renderNewsChips();
    // 아직 한 번도 안 받았으면 그대로 둔다 — 여기서 null 로 그리면 도착
    // 직전에 "불러오지 못했습니다"가 잠깐 번쩍인다.
    if (custodyData) renderCustody(custodyData);
  }

  function setLang(next) {
    if (next === lang) return;
    lang = next;
    try {
      localStorage.setItem(LANG_KEY, lang);
    } catch (e) { /* 저장만 못 할 뿐 전환은 된다 */ }
    applyLang();
  }

  function esc(s) {
    return String(s ?? "").replace(/[&<>"']/g, (c) => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
    }[c]));
  }

  // 링크는 http(s) 만 통과시킨다. esc() 는 따옴표를 막아 속성을 못 벗어나게
  // 하지만 href="javascript:…" 자체는 막지 못한다 — 출처(네이버·야후)를
  // 믿더라도 클릭 한 번에 실행되는 통로는 열어둘 이유가 없다.
  function safeUrl(u) {
    try {
      const p = new URL(String(u ?? ""), location.origin).protocol;
      return p === "http:" || p === "https:" ? String(u) : "";
    } catch { return ""; }
  }

  // 스냅샷은 10초마다 오지만 패널 대부분은 그대로다. 만들어낸 HTML 이
  // 직전과 같으면 DOM 을 건드리지 않는다 — 25개 패널을 매번 새로 그리면
  // 그만큼 레이아웃과 이벤트 바인딩이 통째로 다시 일어난다.
  function setHtml(el, html) {
    if (!el || el.__html === html) return false;
    const before = snapshotValues(el);
    el.__html = html;
    el.innerHTML = html;
    flashChanged(el, before);
    return true;
  }

  // -- 값 변화 하이라이트 ------------------------------------------------
  //
  // 초 단위로 갱신되는 화면에서 "방금 무엇이 움직였나" 를 눈으로 잡을 수
  // 있게, 값이 바뀐 칸의 바탕을 잠깐 물들였다 뺀다 (styles 의 flash-up/dn).
  //
  // 칸을 알아보는 열쇠는 "그 행의 종목코드 + 칸 순서" 다. 순위표는 매 갱신
  // 마다 행 순서가 바뀌므로 위치만으로 짚으면 엉뚱한 칸이 번쩍인다.

  // 다시 그리기 직전의 값을 뜬다. 갱신이 잦은 칸(.mono)만 본다.
  function snapshotValues(el) {
    const map = new Map();
    el.querySelectorAll(".mono").forEach((node, i) => {
      map.set(valueKey(node, i), node.textContent);
    });
    return map;
  }

  function valueKey(node, i) {
    const row = node.closest("[data-symbol]");
    if (!row) return `#${i}`;
    // 같은 행 안에서 몇 번째 값 칸인지 — 종목이 순위를 오르내려도 안 흔들린다
    const cells = [...row.querySelectorAll(".mono")];
    return `${row.dataset.symbol}#${cells.indexOf(node)}`;
  }

  // 숫자만 뽑아 견준다 — "▲ +0.24%" 처럼 화살표·부호가 섞여 있다.
  function numOf(text) {
    const m = String(text).replace(/,/g, "").match(/-?\d+(\.\d+)?/);
    return m ? parseFloat(m[0]) : null;
  }

  function flashChanged(el, before) {
    if (!before.size) return;                 // 첫 렌더는 전부 "새 값" 이라 건너뛴다
    el.querySelectorAll(".mono").forEach((node, i) => {
      const key = valueKey(node, i);
      if (!before.has(key)) return;
      const old = before.get(key);
      const now = node.textContent;
      if (old === now) return;
      const a = numOf(old), b = numOf(now);
      // 숫자를 못 뽑으면(종목명 등) 오른 것도 내린 것도 아니다 — 그냥 둔다
      if (a === null || b === null || a === b) return;
      const cls = b > a ? "flash-up" : "flash-dn";
      node.classList.remove("flash-up", "flash-dn");
      void node.offsetWidth;                  // 애니메이션 재시작
      node.classList.add(cls);
      node.addEventListener("animationend", () => node.classList.remove(cls), { once: true });
    });
  }

  // 값 행은 div 지만 하는 일은 버튼이다 — 키보드와 스크린리더에도 그렇게
  // 보이도록 역할과 탭 순서를 붙인다. 실제 Enter/Space 처리는 아래
  // 위임 리스너가 맡는다.
  const ROW_A11Y = ' role="button" tabindex="0"';

  function staleTitle(row) {
    return row.stale ? ` title="${esc(t("stale.title"))}"` : "";
  }

  // ETF·ETN도 개별종목과 같은 랭킹에 섞여 들어오므로(네이버 증권 ·
  // 야후 파이낸스 원본과 동일) 이름 옆에 종류를 붙여 구분한다.
  function kindTag(row) {
    return row.kind ? `<span class="mv-tag">${esc(row.kind)}</span>` : "";
  }

  // -- panel renderers ---------------------------------------------------

  // -- 티커 띠 ------------------------------------------------------------
  //
  // 칸은 전부 같은 크기고, 띠 전체가 천천히 왼쪽으로 흐른다. 같은 칸을 한 벌
  // 더 복제해 붙여 두고 -50% 까지 민 뒤 0 으로 되감으므로 이음매가 안 보인다
  // (styles 의 @keyframes ticker-slide).
  //
  // 값이 바뀔 때마다 innerHTML 을 갈아 끼우면 CSS 애니메이션이 처음으로
  // 되감겨 띠가 계속 튄다. 그래서 뼈대는 종목 구성이 바뀔 때만 다시 짓고,
  // 평소에는 칸 안의 글자만 제자리에서 고쳐 쓴다.
  const TICKER_SECONDS_PER_CELL = 5.5;   // 한 칸이 지나가는 데 걸리는 시간

  function tickerCellHtml(r, clone) {
    return `<div class="tk-cell clickable-row${clone ? " tk-clone" : ""}"${clone ? ' aria-hidden="true"' : ""}${ROW_A11Y} data-kind="${esc(r.kind || "index")}" data-symbol="${esc(r.symbol)}" data-name="${esc(r.name || r.label)}"${r.pair ? ` data-pair="${esc(r.pair)}"` : ""}${r.contract ? ` data-contract="${esc(r.contract)}"` : ""}${r.sub ? ` data-sub="${esc(r.sub)}"` : ""} style="display:flex;flex-direction:column;gap:1px;padding:9px 16px 10px"${staleTitle(r)}>
        <span class="tk-label" style="font-size:9px;font-weight:700;letter-spacing:.2em;color:var(--c-label);text-transform:uppercase;white-space:nowrap">${esc(tr(r.label))}</span>
        <span class="mono tk-px" style="font-size:26px;font-weight:400;letter-spacing:-.04em;line-height:1.05">${esc(r.price)}</span>
        <span class="mono tk-pct" style="font-size:11px;color:${r.color}">${r.arrow} ${esc(r.pct)}</span>
      </div>`;
  }

  function renderTicker(rows) {
    const track = $("ticker-track");
    if (!track) return;

    // 뼈대는 종목 구성(과 언어)이 바뀔 때만 다시 짓는다
    const shape = rows.map((r) => `${r.symbol}|${tr(r.label)}`).join(",");
    if (track.dataset.shape !== shape) {
      track.dataset.shape = shape;
      // 복제본은 스크린리더에서 빼 준다 — 같은 값이 두 번 읽힌다. 감싸는
      // 껍데기 없이 나란히 붙여야 칸막이 규칙(.tk-cell)이 양쪽에 다 걸린다.
      track.innerHTML = rows.map((r) => tickerCellHtml(r, false)).join("")
        + rows.map((r) => tickerCellHtml(r, true)).join("");
      const cells = [...track.children];
      track.__cells = cells.slice(0, rows.length);
      track.__clones = cells.slice(rows.length);
      sizeTicker(rows.length);
      return;
    }

    // 평소 갱신 — 글자만 제자리에서 고쳐 쓴다
    const cells = track.__cells || [];
    const clones = track.__clones || [];
    rows.forEach((r, i) => {
      [cells[i], clones[i]].forEach((cell) => {
        if (!cell) return;
        const px = cell.querySelector(".tk-px");
        setCellText(px, esc(r.price));
        const pct = cell.querySelector(".tk-pct");
        setCellText(pct, `${r.arrow} ${esc(r.pct)}`);
        if (pct) pct.style.color = r.color;
        px?.classList.toggle("stale-dot", !!r.stale);
      });
    });
  }

  // 칸 하나가 지나가는 시간을 일정하게 잡아 종목 수가 달라져도 속도가 같다.
  function sizeTicker(count) {
    const track = $("ticker-track");
    if (!track) return;
    const strip = $("ticker-strip");
    // 화면을 다 채우지 못할 만큼 종목이 적으면 굳이 흘리지 않는다
    const fits = track.scrollWidth / 2 <= strip.clientWidth;
    track.classList.toggle("static", fits);
    track.classList.toggle("rolling", !fits);
    track.style.setProperty("--ticker-duration", `${(count * TICKER_SECONDS_PER_CELL).toFixed(1)}s`);
  }
  window.addEventListener("resize", () => sizeTicker($("ticker-track")?.__cells?.length || 0));

  // 흐르는 띠라 하이라이트는 걸지 않는다 — 글자만 조용히 갈아 끼운다.
  // 바뀐 게 없으면 DOM 도 건드리지 않는다 (초당 한 번씩 도는 자리다).
  function setCellText(node, html) {
    if (node && node.innerHTML !== html) node.innerHTML = html;
  }

  function fxRowHtml(f, kind) {
    return `
      <div class="fx-row clickable-row"${ROW_A11Y} data-kind="${kind}" data-symbol="${esc(f.symbol)}" data-name="${esc(f.name)}"${f.pair ? ` data-pair="${esc(f.pair)}"` : ""}${f.sub ? ` data-sub="${esc(f.sub)}"` : ""}${staleTitle(f)}>
        <span class="fx-pair">${esc(tr(f.pair || f.name))}</span>
        <span class="mono fx-px">${esc(f.price)}</span>
        <span class="mono fx-pct" style="color:${f.color}">${f.arrow} ${esc(f.pct)}</span>
      </div>`;
  }

  function renderFxRegions(regions) {
    regions.forEach((g, i) => {
      const col = $(`fx-col-${i}`);
      if (!col) return;
      setHtml(col, `<div style="font-size:9.5px;font-weight:700;letter-spacing:.2em;text-transform:uppercase;color:var(--color-accent);padding:0 0 5px;border-bottom:1px solid var(--rule-mid)">${esc(tr(g.label))}</div>`
        + g.rows.map((f) => fxRowHtml(f, "fx")).join(""));
    });
  }

  function renderIdxMain(rows) {
    const half = Math.ceil(rows.length / 2);
    setHtml($("idx-col-0"), rows.slice(0, half).map((r) => fxRowHtml(r, "index")).join(""));
    setHtml($("idx-col-1"), rows.slice(half).map((r) => fxRowHtml(r, "index")).join(""));
  }

  function renderRates(rows) {
    setHtml($("rates-list"), rows.map((rt) => `
      <div class="fx-row clickable-row"${ROW_A11Y} data-kind="rate" data-symbol="${esc(rt.symbol)}" data-name="${esc(rt.name)}" data-sub="${esc(rt.sub)}"${staleTitle(rt)}>
        <span class="fx-pair">${esc(tr(rt.name))}</span>
        <span class="mono fx-px">${esc(rt.value)}</span>
        <span class="mono fx-pct" style="color:${rt.color}">${rt.arrow} ${esc(rt.chg)}</span>
      </div>`).join(""));
  }

  function renderCommodities(rows) {
    setHtml($("commodities-list"), rows.map((r) => `
      <div class="fx-row clickable-row"${ROW_A11Y} data-kind="commodity" data-symbol="${esc(r.symbol)}" data-name="${esc(r.name)}" data-contract="${esc(r.contract)}"${staleTitle(r)}>
        <span class="fx-pair">${esc(tr(r.name))}</span>
        <span class="mono fx-px">$${esc(r.price)}</span>
        <span class="mono fx-pct" style="color:${r.color}">${r.arrow} ${esc(r.pct)}</span>
      </div>`).join(""));
  }

  // 국내 순위가 오늘 장중 값이 아니라 직전 영업일 확정치일 때 제목 옆에
  // 그 날짜를 적는다 (개장 전 08:30~09:00). 오늘 값이면 자리를 비워 둔다.
  function krRankingsNote(session) {
    if (!session) return "";
    const [, mm, dd] = session.split("-");
    return isEn() ? `as of ${mm}/${dd} close` : `${mm}.${dd} 종가 기준`;
  }

  // 어느 칸이 무엇인지 표 위에 적는다 — 값만 넉 줄 늘어놓으면 "주가"인지
  // "등락률"인지 매번 헤아려야 한다. 거래대금 칸은 있는 표에만 붙는다.
  function rankHead(volLabel) {
    return `<div class="${volLabel ? "mt-row" : "mv-row"} mv-head">
        <span class="mv-rank">#</span>
        <span>${esc(t("h.name"))}</span>
        <span style="text-align:right">${esc(t("h.price"))}</span>
        <span style="text-align:right">${esc(t("h.chgpct"))}</span>
        ${volLabel ? `<span style="text-align:right">${esc(t(volLabel))}</span>` : ""}
      </div>`;
  }

  function renderMovers() {
    if (!latest) return;
    $("movers-session").textContent = krRankingsNote(latest.moversSession);
    const rows = moversTab === "gainers" ? latest.gainers : latest.losers;
    setHtml($("movers-list"), rankHead() + rows.map((m) => `
      <div class="mv-row clickable-row"${ROW_A11Y} data-symbol="${esc(m.symbol)}" data-name="${esc(m.name.replace(/\*$/, ""))}"${staleTitle(m)}>
        <span class="mv-rank">${m.rank}</span>
        <span class="mv-name">${esc(m.name)}${kindTag(m)}</span>
        <span class="mono mv-px">${esc(m.price)}</span>
        <span class="mono mv-pct" style="color:${m.color}">${esc(m.pct)}</span>
      </div>`).join(""));
  }

  function renderKrMostTraded() {
    if (!latest) return;
    $("mt-session").textContent = krRankingsNote(latest.mostTradedSession);
    setHtml($("kr-most-traded-list"), rankHead("h.value") + latest.krMostTraded.map((m) => `
      <div class="mt-row clickable-row"${ROW_A11Y} data-symbol="${esc(m.symbol)}" data-name="${esc(m.name.replace(/\*$/, ""))}"${staleTitle(m)}>
        <span class="mv-rank">${m.rank}</span>
        <span class="mv-name">${esc(m.name)}${kindTag(m)}</span>
        <span class="mono mv-px">${esc(m.price)}</span>
        <span class="mono mv-pct" style="color:${m.color}">${esc(m.pct)}</span>
        <span class="mono mt-vol">${esc(amt(m.tradingValue))}</span>
      </div>`).join(""));
  }

  function renderUsMovers() {
    if (!latest) return;
    const rows = usMoversTab === "gainers" ? latest.usGainers : latest.usLosers;
    setHtml($("us-movers-list"), rankHead() + rows.map((m) => `
      <div class="mv-row clickable-row"${ROW_A11Y} data-symbol="${esc(m.symbol)}" data-name="${esc(m.fullName)}" title="${esc(m.symbol)} · ${esc(m.fullName)}">
        <span class="mv-rank">${m.rank}</span>
        <span class="mv-name">${esc(m.name)}${kindTag(m)}</span>
        <span class="mono mv-px">${esc(m.price)}</span>
        <span class="mono mv-pct" style="color:${m.color}">${esc(m.pct)}</span>
      </div>`).join(""));
  }

  function renderUsMostActive() {
    if (!latest) return;
    setHtml($("us-most-active-list"), rankHead("h.volume") + latest.usMostActive.map((m) => `
      <div class="mt-row clickable-row"${ROW_A11Y} data-symbol="${esc(m.symbol)}" data-name="${esc(m.fullName)}" title="${esc(m.symbol)} · ${esc(m.fullName)}">
        <span class="mv-rank">${m.rank}</span>
        <span class="mv-name">${esc(m.name)}${kindTag(m)}</span>
        <span class="mono mv-px">${esc(m.price)}</span>
        <span class="mono mv-pct" style="color:${m.color}">${esc(m.pct)}</span>
        <span class="mono mt-vol">${esc(m.volume)}</span>
      </div>`).join(""));
  }

  function renderKrRates(rows) {
    if (!rows) return;
    setHtml($("kr-rates-list"), rows.map((r) => `
      <div class="fx-row clickable-row"${ROW_A11Y} data-kind="krrate" data-symbol="${esc(r.code)}" data-name="${esc(r.name)}"${staleTitle(r)}>
        <span class="fx-pair">${esc(tr(r.name))}</span>
        <span class="mono fx-px">${esc(r.value)}</span>
        <span class="mono fx-pct" style="color:${r.color}">${r.arrow} ${esc(r.chg)}</span>
      </div>`).join(""));
  }

  function renderFxNews(rows) {
    if (rows) fxNewsRows = rows;
    if (!fxNewsRows || isEn()) return;   // 네이버 한국어 뉴스 — 영문판에는 없다

    const totalPages = Math.max(1, Math.ceil(fxNewsRows.length / FX_NEWS_PAGE_SIZE));
    if (fxNewsPage > totalPages) fxNewsPage = totalPages;
    const start = (fxNewsPage - 1) * FX_NEWS_PAGE_SIZE;

    // 시각·제목이 목록 전체를 덮는 격자의 두 칸이다 (.nws-list) — 행마다
    // div 로 묶지 않아야 시각 칸 폭이 목록 전체에서 하나로 정해진다.
    setHtml($("fx-news-list"), fxNewsRows.slice(start, start + FX_NEWS_PAGE_SIZE).map((n) => `
      <span class="mono nws-when">${esc(n.time)}</span>
      <div class="nws-body">
        ${safeUrl(n.url) ? `<a href="${esc(safeUrl(n.url))}" target="_blank" rel="noopener" style="color:inherit;text-decoration:none">${esc(n.headline)}</a>` : esc(n.headline)}
        <span class="tag tag-accent" style="font-size:9px;padding:0 6px;vertical-align:middle">${esc(n.tag)}</span>
      </div>`).join(""));

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
      setHtml(table, `<div style="font-size:12px;color:var(--c-label);padding:10px 0">${esc(t("err.flow"))}</div>`);
      foot.textContent = "";
      return;
    }

    const head = `
      <div class="flow-row flow-head">
        <span>${esc(t("h.investor"))}</span>${periods.map((p) => `<span>${esc(tr(p.label))}</span>`).join("")}
      </div>`;

    // 기간별 배열은 모두 같은 투자자 순서라 첫 기간을 기준으로 행을 편다.
    const rows = data.periods[periods[0].key].map((_, i) => {
      const first = data.periods[periods[0].key][i];
      const cells = periods.map((p) => {
        const cell = data.periods[p.key][i];
        const v = fmtFlowValue(cell.raw, data.unitLabel) ?? cell.value;
        return `<span class="mono flow-val" style="color:${cell.color}">${esc(v)}</span>`;
      }).join("");
      return `
        <div class="flow-row${FLOW_SUB_ROWS.has(first.key) ? " flow-sub" : ""}">
          <span class="flow-name">${esc(tr(first.label))}</span>${cells}
        </div>`;
    }).join("");

    setHtml(table, head + rows);
    foot.textContent = (isEn()
      ? `As of ${data.asOf} · unit ${trUnit(data.unitLabel)} · cumulative over the last ${data.days} business days`
      : `${data.asOf} 기준 · 단위 ${data.unitLabel} · 최근 ${data.days}영업일 누적`)
      + (data.stale ? " " + t("foot.stale") : "");
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
    if (isEn()) return I18N.fmtWon(v * 1e8, false);
    return Math.abs(v) >= 10000
      ? `${(v / 10000).toLocaleString("en-US", { maximumFractionDigits: 1 })}조`
      : `${Math.round(v).toLocaleString("en-US")}억`;
  }

  // 단위 라벨 — 값에 억·조(영문은 bn·tn)가 이미 붙어 있으므로 여기서는
  // 통화만 말한다. 계약 수인 선물만 다르다.
  function trUnit(unitLabel) {
    if (!isEn()) return unitLabel;
    return unitLabel === "계약" ? t("unit.contract") : t("unit.krw");
  }

  // 1 / 2 / 2.5 / 5 × 10^k 중 v 이상인 첫 값 — 눈금이 읽기 좋은 수로 떨어진다.
  function niceStep(v) {
    if (v <= 0) return 1;
    const exp = Math.floor(Math.log10(v));
    const base = Math.pow(10, exp);
    for (const m of [1, 2, 2.5, 5, 10]) if (v <= m * base) return m * base;
    return 10 * base;
  }

  // 막대에 찍히는 주체명과 값도 화면 언어를 따른다. 서버 문자열을 그대로
  // 쓰면 표는 영문인데 차트만 한글로 남는다.
  function localizeCells(cells, unitLabel) {
    if (!isEn()) return cells;
    return cells.map((c) => Object.assign({}, c, {
      label: tr(c.label),
      value: fmtFlowValue(c.raw, unitLabel) ?? c.value,
    }));
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
      grid += `<line x1="${x0}" y1="${y}" x2="${x1}" y2="${y}" style="stroke:${i === 0 ? "var(--rule-mid)" : "var(--rule-hair)"}" stroke-width="1"/>`
        + `<text x="${x0 - 8}" y="${y + 3.5}" text-anchor="end" font-size="10.5" style="fill:var(--c-label)">${esc(axisFmt(v))}</text>`;
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
          ${h < 0.5 ? "" : `<path d="${path}" style="fill:${c.color}"/>`}
          <text x="${cx}" y="${labelY}" text-anchor="middle" font-size="11" font-weight="500" style="fill:${c.color}">${esc(c.value)}</text>
          <text x="${cx}" y="${y1 + 17}" text-anchor="middle" font-size="11" style="fill:var(--c-dim)">${esc(c.label)}</text>
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
    // 선물은 금액이 아니라 계약 수라 단위를 막대 바로 옆에 붙여둔다 —
    // 억원 표를 보다 넘어오면 같은 단위로 착각하기 쉽다.
    $("flow-chart-period").textContent = period
      ? (isEn()
          ? `· ${tr(period.label)} cumulative (unit ${trUnit(data.unitLabel)})`
          : `· ${period.label} 누적 (단위 ${data.unitLabel})`)
      : "";

    if (!cells.length) { setHtml(box, ""); flowChartKey = ""; return; }

    // 스냅샷은 10초마다 오지만 수급 원본은 5분마다 갱신된다. 값이 그대로면
    // SVG를 다시 만들지 않는다 — 매번 새로 그리면 눈에 띄게 깜빡인다.
    const key = `${lang}|${data.label}|${flowChartPeriod}|${cells.map((c) => c.raw).join(",")}`;
    if (key === flowChartKey) return;
    flowChartKey = key;

    setHtml(box, netBuyBarsSvg(localizeCells(cells, data.unitLabel),
      (v) => flowAxisLabel(v, data.unitLabel),
      isEn() ? `${tr(data.label)} net buying by investor`
             : `${data.label} 주체별 순매수`));
  }

  // -- 채권 수급 (KOFIA 장외채권) ----------------------------------------

  // 채권 수급은 서버가 이미 억원으로 준다 — 주식 쪽(백만원)과 단위가 달라
  // 축 포맷터를 따로 둔다.
  function bondAxisLabel(v) {
    if (v === 0) return "0";
    if (isEn()) return I18N.fmtWon(v * 1e8, false);
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
      setHtml(table, `<div style="font-size:12px;color:var(--c-label);padding:10px 0">${esc(t("err.bflow"))}</div>`);
      $("bflow-foot").textContent = "";
      $("bflow-chart").innerHTML = "";
      return;
    }

    if (!bf.bondTypes.includes(bondFlowType)) bondFlowType = bf.bondTypes[0];

    // 채권종류 탭은 서버가 준 목록으로 만든다.
    const seg = $("bflow-seg");
    if (seg.dataset.keys !== `${lang}|${bf.bondTypes.join("|")}`) {
      seg.dataset.keys = `${lang}|${bf.bondTypes.join("|")}`;
      seg.innerHTML = bf.bondTypes.map((bt) => `
        <label class="seg-opt" style="padding:4px 10px;font-size:11.5px">
          <input type="radio" name="bflowsel" value="${esc(bt)}"${bt === bondFlowType ? " checked" : ""}><span>${esc(tr(bt))}</span>
        </label>`).join("");
      seg.querySelectorAll("input").forEach((el) => {
        el.addEventListener("change", () => { bondFlowType = el.value; renderBondFlow(); });
      });
    }

    const head = `<div class="flow-row bflow-row flow-head"><span>${esc(t("h.investor"))}</span>${
      periods.map((p) => `<span>${esc(tr(p.label))}</span>`).join("")}</div>`;
    // 주식 수급 표와 세로로 붙어 있어서 단위를 헷갈리기 쉽다.

    const first = bf.periods[periods[0].key][bondFlowType] || [];
    const rows = first.map((_, i) => {
      const cells = periods.map((p) => {
        const c = (bf.periods[p.key][bondFlowType] || [])[i];
        return c
          ? `<span class="mono flow-val" style="color:${c.color}">${esc(fmtFlowValue(c.raw, "원") ?? c.value)}</span>`
          : `<span class="mono flow-val" style="color:var(--c-empty)">—</span>`;
      }).join("");
      return `<div class="flow-row bflow-row"><span class="flow-name">${esc(tr(first[i].label))}</span>${cells}</div>`;
    }).join("");

    setHtml(table, head + rows);
    const d = bf.asOf;
    const bfDate = `${d.slice(0, 4)}.${d.slice(4, 6)}.${d.slice(6)}`;
    $("bflow-foot").textContent =
      (isEn()
        ? `As of ${bfDate} · unit KRW · ${t("foot.bflow")}`
        : `${bfDate} 기준 · 단위 원 · ${t("foot.bflow")}`)
      + (bf.stale ? " " + t("foot.stale") : "");

    const period = periods.find((p) => p.key === bondFlowPeriod);
    $("bflow-chart-label").textContent = period
      ? (isEn()
          ? `· ${tr(bondFlowType)} · ${tr(period.label)} cumulative (unit KRW)`
          : `· ${bondFlowType} · ${period.label} 누적 (단위 원)`)
      : "";
    const cells = bf.periods[bondFlowPeriod][bondFlowType] || [];
    if (!cells.length) { $("bflow-chart").innerHTML = ""; bondFlowChartKey = ""; return; }

    const key = `${lang}|${bondFlowType}|${bondFlowPeriod}|${cells.map((c) => c.raw).join(",")}`;
    if (key === bondFlowChartKey) return;
    bondFlowChartKey = key;
    $("bflow-chart").innerHTML = netBuyBarsSvg(localizeCells(cells, "원"), bondAxisLabel,
      isEn() ? `${tr(bondFlowType)} net buying by investor`
             : `${bondFlowType} 주체별 순매수`);
  }

  // -- 지표종목 최종호가 (채권 커브 패널 상단) ----------------------------
  // 금리 레벨에는 색을 안 입힌다 — 오르내림이 아니라 수준이라서다.
  // 전일대비만 색·화살표를 달아 방향을 나른다.

  function renderBondQuotes() {
    if (!latest) return;
    const q = latest.bondQuotes;
    const list = $("quotes-list");
    if (!q || !q.rows.length) {
      setHtml(list, `<div style="font-size:12px;color:var(--c-label);padding:8px 0">${esc(t("err.quotes"))}</div>`);
      $("quotes-asof").textContent = "";
      return;
    }

    const head = `<div class="q-row q-head">
      <span>${esc(t("h.issue"))}</span><span>${esc(t("h.residual"))}</span><span class="q-num">${esc(t("h.yield"))}</span>
      <span class="q-num">${esc(t("h.chgPrev"))}</span><span class="q-num">${esc(t("h.yearRange"))}</span></div>`;

    setHtml(list, head + q.rows.map((r) => `
      <div class="q-row">
        <span style="font-weight:500;white-space:nowrap;overflow:hidden;text-overflow:ellipsis">${esc(tr(r.label))}</span>
        <span style="color:var(--c-dim);font-size:11px">${esc(tr(r.term))}</span>
        <span class="mono q-num" style="font-weight:500">${esc(r["yield"])}</span>
        <span class="mono q-num" style="color:${r.changeColor}">${r.arrow} ${esc(r.changeBp)}</span>
        <span class="mono q-num" style="color:var(--c-dim);font-size:11px">${esc(r.range)}</span>
      </div>`).join(""));

    const d = q.asOf;
    $("quotes-asof").textContent =
      `· ${d.slice(0, 4)}.${d.slice(4, 6)}.${d.slice(6)} · ${t("foot.quotes")}`;
  }

  // -- 원화 스왑 (FX 스왑포인트 · IRS/CRS) --------------------------------
  // 포인트·금리는 레벨이라 색을 안 입힌다. 통화베이시스만 부호가 뜻을
  // 가지므로(음수 = 원화 조달 프리미엄) 색을 준다.

  function renderKrwSwap() {
    if (!latest) return;
    const sp = latest.swapPoints, ic = latest.irsCrs;

    const swapBox = $("swap-table");
    if (!sp || !sp.rows.length) {
      setHtml(swapBox, `<div style="font-size:12px;color:var(--c-label);padding:8px 0">${esc(t("err.swap"))}</div>`);
      $("swap-spot").textContent = "";
    } else {
      // 양대 중개사(서울외국환중개·한국자금중개)를 나란히 — 같은 상품의
      // 호가 폭이 비교되고 연율 Mid 는 서로 검증이 된다. 어느 열이 누구
      // 고시인지는 바탕색·테두리(sw-s/sw-k)와 머리의 중개사 띠로 가른다.
      // 묶음 안 네 칸: Bid | Offer | Mid | 연율.
      const edge = (i) => (i === 0 ? " sw-edge-l" : i === 3 ? " sw-edge-r" : "");
      const group = (o, k) =>
        [o.bid, o.offer, o.mid, o.annualized].map((v, i) =>
          `<span class="mono sw-num sw-${k}${edge(i)}" style="${
            v === "—" ? "color:var(--c-empty)" : i >= 2 ? "font-weight:500" : "color:var(--c-sub)"
          }">${esc(v)}</span>`).join("");
      const gHead = (k) =>
        ["Bid", "Offer", "Mid", t("h.annual")].map((h, i) =>
          `<span class="sw-num sw-${k}${edge(i)}">${esc(h)}</span>`).join("");

      const head = `<div class="sw-row sw-grouphead">
          <span></span>
          <span class="sw-grp sw-s sw-edge-l sw-edge-r">${esc(t("sw.smbs"))}</span>
          <span class="sw-grp sw-k sw-edge-l sw-edge-r">${esc(t("sw.kmb"))}</span>
        </div>
        <div class="sw-row sw-head"><span>${esc(t("h.tenor"))}</span>
          ${gHead("s")}${gHead("k")}</div>`;
      setHtml(swapBox, head + sp.rows.map((r) => `
        <div class="sw-row">
          <span style="font-weight:500">${esc(r.label)}</span>
          ${group(r.smbs, "s")}${group(r.kmb, "k")}
        </div>`).join(""));
      const spotSrc = sp.spotSource
        ? ` (${tr(sp.spotSource)}${sp.spotAsOf ? ` ${sp.spotAsOf}` : ""})` : "";
      $("swap-spot").textContent =
        (isEn()
          ? `· ${t("foot.spotUsdkrw")} ${sp.spot}${spotSrc}`
          : `· ${t("foot.spotUsdkrw")} ${sp.spot}${spotSrc} 기준`)
        + (sp.spotLive && sp.spotLive !== sp.spot ? ` · ${t("foot.live")} ${sp.spotLive}` : "")
        + ` · ${t("foot.swapNote")}`;
    }

    // FX-implied 원화금리 · CCS 베이시스 — 스왑포인트를 금리로 편 것.
    // 레벨 칸(swap rate/USD/yield/IRS)은 색이 없고 basis 두 칸만 색을 준다:
    // 음수 = 스왑시장 원화 조달이 IRS 보다 싸다. 6M 은 IRS 고시가 없어
    // 보간이라 행에 표시를 달고, 보간 폭과 경고를 표 아래에 붙인다.
    const fi = latest.fxImplied;
    const fiBox = $("fximplied-table");
    if (!fi || !fi.rows.length) {
      setHtml(fiBox, `<div style="font-size:12px;color:var(--c-label);padding:8px 0">${esc(t("err.fximplied"))}</div>`);
      setHtml($("fximplied-variants"), "");
      setHtml($("fximplied-warn"), "");
      $("fximplied-src").textContent = "";
    } else {
      // 화면 값은 par yield 하나다. 단리 zero 는 payload 에 그대로 있지만
      // (pricer `KRW Zero` 대조·회귀테스트용) 표에는 안 올린다 — 데스크가
      // 읽는 건 IRS 와 같은 물건인 par 쪽이고, 두 금리를 나란히 두면 어느 게
      // 결론인지 흐려진다.
      const head = `<div class="fi-row fi-head"><span>${esc(t("h.tenor"))}</span><span class="sw-num">${esc(t("h.days"))}</span>
        <span class="sw-num">swap rate</span><span class="sw-num">USD</span>
        <span class="sw-num">par yield</span><span class="sw-num">KRW IRS</span>
        <span class="sw-num">basis</span></div>`;
      setHtml(fiBox, head + fi.rows.map((r) => `
        <div class="fi-row${r.interpolated ? " fi-interp" : ""}">
          <span style="font-weight:500">${esc(r.label)}</span>
          <span class="mono sw-num" style="color:var(--c-label)">${esc(r.days)}</span>
          <span class="mono sw-num" style="color:var(--c-sub)">${esc(r.swapRate)}</span>
          <span class="mono sw-num" style="color:var(--c-sub)">${esc(r.usdRate)}</span>
          <span class="mono sw-num" style="font-weight:500">${esc(r.yield)}</span>
          <span class="mono sw-num" style="color:var(--c-sub)">${esc(r.irs)}${
            r.interpolated ? `<span class="fi-flag">INTERP</span>` : ""}</span>
          <span class="mono sw-num" style="color:${r.basisColor};font-weight:500">${esc(r.basis)}</span>
        </div>`).join(""));

      // 6M 보간은 값 하나가 아니라 폭으로 읽어야 해서 방식별 값을 다 편다.
      setHtml($("fximplied-variants"), fi.sixVariants.length
        ? `<span style="color:var(--c-sub)">${esc(t("fi.sixInterp"))}</span>`
          + fi.sixVariants.map((v) =>
              `<span>${esc(tr(v.name))} <b style="font-weight:500">${esc(v.value)}</b></span>`).join("")
          + `<span>${esc(t("fi.spread"))} ${esc(fi.sixSpreadBp)}</span>`
        : "");
      setHtml($("fximplied-warn"),
        (fi.warnings || []).map((w) => `<div>${esc(tr(w))}</div>`).join(""));
      $("fximplied-src").textContent =
        `· ${t("foot.swapIrsQuoted")} ${fi.pointSource} ${fi.quoteDate || ""} ${t("foot.quoted")}`
        + ` · ${t("foot.spotDate")} ${fi.spotDate} ${t("foot.spotDateNote")}`
        + (fi.spotSource ? ` · ${t("foot.spot")} ${tr(fi.spotSource)}${fi.spotAsOf ? ` ${fi.spotAsOf}` : ""}` : "")
        + ` · USD ${tr(fi.usdSource)} ${fi.usdAsOf}`;
    }

    const icBox = $("irscrs-table");
    if (!ic || !ic.rows.length) {
      setHtml(icBox, `<div style="font-size:12px;color:var(--c-label);padding:8px 0">${esc(t("err.irscrs"))}</div>`);
    } else {
      const head = `<div class="ic-row ic-head"><span>${esc(t("h.tenor"))}</span>
        <span class="sw-num">IRS</span><span class="sw-num">CRS</span>
        <span class="sw-num">${esc(t("h.basis"))}</span></div>`;
      setHtml(icBox, head + ic.rows.map((r) => `
        <div class="ic-row">
          <span style="font-weight:500">${esc(r.label)}</span>
          <span class="mono sw-num">${esc(r.irs)}</span>
          <span class="mono sw-num">${esc(r.crs)}</span>
          <span class="mono sw-num" style="color:${r.basisColor};font-weight:500">${esc(r.basis)}</span>
        </div>`).join(""));
    }

    if (ic && ic.source) {
      $("irscrs-src").textContent =
        `${t("swap.irscrs.note")} · ${ic.source} ${t("foot.midQuote")}`;
    }

    // 국고채(현물) − IRS 스프레드 — 채권과 스왑 커브 사이의 괴리.
    const bi = latest.bondIrs;
    const biBox = $("bondirs-table");
    if (!bi || !bi.rows.length) {
      setHtml(biBox, `<div style="font-size:12px;color:var(--c-label);padding:8px 0">${esc(t("err.bondirs"))}</div>`);
    } else {
      const head = `<div class="bi-row bi-head"><span>${esc(t("h.tenor"))}</span>
        <span class="sw-num">${esc(t("h.ktb"))}</span><span class="sw-num">IRS</span>
        <span class="sw-num">${esc(t("h.spread"))}</span></div>`;
      setHtml(biBox, head + bi.rows.map((r) => `
        <div class="bi-row">
          <span style="font-weight:500">${esc(r.label)}</span>
          <span class="mono sw-num">${esc(r.ktb)}</span>
          <span class="mono sw-num">${esc(r.irs)}</span>
          <span class="mono sw-num" style="color:${r.spreadColor};font-weight:500">${esc(r.spread)}</span>
        </div>`).join(""));
    }

    const stamp = (sp && sp.asOf) || (ic && ic.asOf) || "";
    $("swap-asof").textContent = stamp ? `${stamp} ${t("foot.quoted")}` : "";
  }

  // -- 단기금융시장 금리 (CP · 전단채) ------------------------------------

  function renderShortTermRates() {
    if (!latest) return;
    const st = latest.shortTermRates;
    const table = $("strate-table");
    if (!st || !st.rows.length) {
      setHtml(table, `<div style="font-size:12px;color:var(--c-label);padding:10px 0">${esc(t("err.strate"))}</div>`);
      $("strate-asof").textContent = "";
      $("strate-foot").textContent = "";
      return;
    }

    const head = `<div class="st-row st-head"><span>${esc(t("h.kind"))}</span>${
      st.tenors.map((tn) => `<span class="st-num">${esc(tr(tn))}</span>`).join("")
      }<span class="st-num">${esc(t("h.dayAmount"))}</span></div>`;

    setHtml(table, head + st.rows.map((r) => `
      <div class="st-row">
        <span style="font-weight:500;white-space:nowrap">${esc(tr(r.label))}</span>
        ${r.rates.map((v) => `<span class="mono st-num"${v === "—" ? ' style="color:var(--c-empty)"' : ""}>${esc(v)}</span>`).join("")}
        <span class="mono st-num" style="color:var(--c-dim)">${esc(amt(r.amount))}</span>
      </div>`).join(""));

    const d = st.asOf;
    $("strate-asof").textContent =
      `${d.slice(0, 4)}.${d.slice(4, 6)}.${d.slice(6)} ${t("foot.quoted")}`;
    $("strate-foot").textContent = t("foot.strate");
  }

  // -- 외환보유액 단기 유출예정액 (IMF 표 II) ----------------------------

  function renderReserveDrains() {
    if (!latest) return;
    const rd = latest.reserveDrains;
    const table = $("drain-table");
    if (!rd || !rd.rows.length) {
      setHtml(table, `<div style="font-size:12px;color:var(--c-label);padding:10px 0">${esc(t("err.drain"))}</div>`);
      $("drain-asof").textContent = "";
      return;
    }

    // 머리행의 "구분" 칸도 rd-name 이다 — 가로로 밀 때 라벨 열과 같이 서 있어야
    // 어느 열이 어느 만기구간인지 계속 읽힌다.
    const head = `<div class="rd-row rd-head"><span class="rd-name">${esc(t("h.kind"))}</span>${
      rd.columns.map((c) => `<span class="rd-num">${esc(tr(c))}</span>`).join("")}</div>`;

    setHtml(table, head + rd.rows.map((r) => {
      const cells = r.values.map((v, i) => {
        const sign = r.signs[i];
        const color = v === "—" ? "var(--c-empty)"
          : sign === "neg" ? "var(--c-up)"
          : sign === "pos" ? "var(--c-down)" : "var(--c-dim)";
        return `<span class="mono rd-num" style="color:${color}">${esc(v)}</span>`;
      }).join("");
      const cls = "rd-row"
        + (r.level === 0 ? " rd-top" : " rd-sub")
        + (r.highlight ? " rd-hi" : "");
      return `<div class="${cls}"><span class="rd-name">${esc(tr(r.label))}</span>${cells}</div>`;
    }).join(""));

    $("drain-asof").textContent = isEn()
      ? `As of ${rd.asOf} · published ${rd.updated}`
      : `${rd.asOf} 기준 · ${rd.updated} 공표`;
  }

  // -- 채권 수익률 곡선 --------------------------------------------------

  function renderBondCurve() {
    if (!latest) return;
    const bc = latest.bondCurve;
    const table = $("curve-table");
    if (!bc || !bc.curves.length) {
      setHtml(table, `<div style="font-size:12px;color:var(--c-label);padding:10px 0">${esc(t("err.curve"))}</div>`);
      $("curve-asof").textContent = "";
      $("curve-chart").innerHTML = "";
      return;
    }

    const head = `<div class="cv-row cv-head"><span>${esc(t("h.bondType"))}</span>${
      bc.tenors.map((tn) => `<span>${esc(tr(tn))}</span>`).join("")}</div>`;

    const rows = bc.curves.map((c, ci) => {
      const byTenor = new Map(c.points.map((p) => [p.label, p]));
      const cells = bc.tenors.map((t) => {
        const p = byTenor.get(t);
        if (!p) return `<span class="mono cv-val" style="color:var(--c-empty)">—</span>`;
        // 국고채(기준) 행은 스프레드가 0이므로 값만 보여준다.
        const spread = p.spread == null ? "" : `<small>${p.spread >= 0 ? "+" : ""}${p.spread}bp</small>`;
        return `<span class="mono cv-val">${esc(p.value)}${spread}</span>`;
      }).join("");
      return `<div class="cv-row${ci === 0 ? " cv-base" : ""}"><span class="cv-name">${esc(tr(c.label))}</span>${cells}</div>`;
    }).join("");

    setHtml(table, head + rows);
    const d = bc.asOf;
    $("curve-asof").textContent = d.length === 8
      ? `${d.slice(0, 4)}.${d.slice(4, 6)}.${d.slice(6)} ${t("foot.quoted")}` : d;
    $("curve-foot").textContent = t("foot.curve")
      + (bc.stale ? " " + t("foot.stale") : "");

    // 종류 선택 탭은 서버가 준 커브 목록에서 만든다 (종류가 바뀌어도 따라간다)
    const seg = $("curve-seg");
    const segKey = `${lang}|${bc.curves.map((c) => c.key).join("|")}`;
    if (seg.dataset.keys !== segKey) {
      seg.dataset.keys = segKey;
      // 고른 커브가 목록에서 사라졌으면(종류명이 바뀌었을 때) 첫 줄로 돌린다.
      // 탭을 그리기 전에 정해야 어느 탭에 표시가 붙을지가 맞는다.
      if (!bc.curves.some((c) => c.key === curveKey)) curveKey = bc.curves[0].key;
      seg.innerHTML = bc.curves.map((c) => `
        <label class="seg-opt" style="padding:3px 10px;font-size:11.5px">
          <input type="radio" name="curvesel" value="${esc(c.key)}"${c.key === curveKey ? " checked" : ""}><span>${esc(tr(c.label))}</span>
        </label>`).join("");
      seg.querySelectorAll("input").forEach((el) => {
        el.addEventListener("change", () => { curveKey = el.value; renderBondCurve(); });
      });
    }

    renderCurveChart(bc.curves.find((c) => c.key === curveKey) || bc.curves[0]);
  }

  // 만기(x)에 대한 수익률(y) 한 줄짜리 선 그래프. 계열이 하나뿐이라 색은
  // 식별이 아니라 강조만 하고, 범례 없이 제목이 무엇을 그린 건지 말한다.
  function renderCurveChart(curve) {
    const box = $("curve-chart");
    $("curve-chart-label").textContent = `· ${tr(curve.label)}`;
    const pts = curve.points;
    if (pts.length < 2) { setHtml(box, ""); return; }

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
      grid += `<line x1="${x0}" y1="${y}" x2="${x1}" y2="${y}" style="stroke:var(--rule-hair)" stroke-width="1"/>`
        + `<text x="${x0 - 8}" y="${y + 3.5}" text-anchor="end" font-size="10.5" style="fill:var(--c-label)">${v.toFixed(2)}</text>`;
    }

    const line = pts.map((p, i) => `${i ? "L" : "M"}${xFor(i)} ${yFor(p.yield)}`).join(" ");
    const area = `${line} L${xFor(pts.length - 1)} ${y1} L${x0} ${y1} Z`;

    const marks = pts.map((p, i) => {
      const cx = xFor(i), cy = yFor(p.yield);
      return `
        <g>
          <title>${esc(tr(p.label))} ${esc(p.value)}%</title>
          <circle cx="${cx}" cy="${cy}" r="8" fill="transparent"/>
          <circle cx="${cx}" cy="${cy}" r="4" style="fill:var(--color-text);stroke:var(--color-bg)" stroke-width="2"/>
          <text x="${cx}" y="${cy - 12}" text-anchor="middle" font-size="10.5" style="fill:var(--c-dim)">${esc(p.value)}</text>
          <text x="${cx}" y="${y1 + 17}" text-anchor="middle" font-size="10.5" style="fill:var(--c-dim)">${esc(tr(p.label))}</text>
        </g>`;
    }).join("");

    setHtml(box, `
      <svg viewBox="0 0 ${W} ${H}" width="100%" height="${H}" preserveAspectRatio="xMidYMid meet"
           role="img" aria-label="${esc(isEn() ? `${tr(curve.label)} yields by tenor` : `${curve.label} 만기별 수익률`)}">
        ${grid}
        <path d="${area}" style="fill:color-mix(in srgb,var(--color-text) 8%,transparent)"/>
        <path d="${line}" fill="none" style="stroke:var(--color-text)" stroke-width="2" stroke-linejoin="round" stroke-linecap="round"/>
        ${marks}
      </svg>`);
  }

  // -- 외화표시채 발행 ---------------------------------------------------

  function renderFxBondIssues() {
    if (!latest) return;
    const rows = latest.fxBondIssues || [];
    $("fxbond-empty").style.display = rows.length ? "none" : "block";
    $("fxbond-count").textContent = rows.length ? fmtCount(rows.length) : "";

    const head = `<div class="fb-row fb-head">
      <span>${esc(t("h.issueDate"))}</span><span>${esc(t("h.issuer"))}</span><span>${esc(t("h.issue"))}</span><span>${esc(t("h.currency"))}</span>
      <span class="fb-num">${esc(t("h.amount"))}</span><span class="fb-num">${esc(t("h.coupon"))}</span><span class="fb-num">${esc(t("h.maturity"))}</span></div>`;

    setHtml($("fxbond-list"), head + rows.map((r) => `
      <div class="fb-row">
        <span class="mono" style="color:var(--c-dim)">${esc(r.issueDate)}</span>
        <span class="fb-name" style="font-weight:500">${esc(r.issuer)}</span>
        <span class="fb-name" style="color:var(--c-sub)" title="${esc(r.name)}">${esc(r.name)}</span>
        <span class="mono fb-cur" style="font-size:11px">${esc(r.currency)}</span>
        <span class="mono fb-num">${esc(amt(r.amount))}</span>
        <span class="mono fb-num">${esc(r.coupon)}</span>
        <span class="mono fb-num" style="color:var(--c-dim)">${esc(r.maturity)}</span>
      </div>`).join(""));
  }

  // -- 코스피200 현·선물 베이시스 ---------------------------------------

  function renderBasis() {
    if (!latest) return;
    const b = latest.basis;
    const body = $("basis-body");
    const foot = $("basis-foot");

    if (!b) {
      setHtml(body, `<div style="font-size:12px;color:var(--c-label);padding:10px 0">${esc(t("err.basis"))}</div>`);
      foot.textContent = "";
      return;
    }

    const stat = (label, value, color) =>
      `<div class="bs-stat"><span>${esc(label)}</span><b class="mono"${color ? ` style="color:${color}"` : ""}>${esc(value)}</b></div>`;

    setHtml(body, `
      <div class="bs-big">
        <b class="mono" style="color:${b.basisColor}">${esc(b.basis)}</b>
        <span class="bs-tag" style="color:${b.basisColor}">${esc(tr(b.state))}</span>
        <span style="font-size:11px;color:var(--c-label);margin-left:auto">${esc(t("bs.label"))}</span>
      </div>
      <div class="bs-stats">
        ${stat(t("bs.spot"), b.spot)}
        ${stat(`${t("bs.futures")} ${b.contract ? `(${b.contract})` : ""}`.trim(), b.futures)}
        ${stat(t("bs.theoretical"), b.theoretical)}
        ${stat(t("bs.theoBasis"), b.theoBasis)}
        ${stat(t("bs.spread"), `${b.spread} · ${tr(b.valuation)}`, b.spreadColor)}
        ${stat(t("bs.toExpiry"), b.daysToExpiry != null ? `${b.daysToExpiry}${isEn() ? " " : ""}${t("bs.days")} (${b.expiry})` : "—")}
      </div>`);

    foot.textContent = `${tr(b.stamp)}\n${tr(b.assumption)}`;
  }

  // -- 키워드 뉴스 -------------------------------------------------------

  function renderKeywordNews() {
    if (!latest || isEn()) return;   // 네이버 한국어 검색 — 영문판에는 없다
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

    const empty = $("kwnews-empty");
    empty.style.display = items.length ? "none" : "block";
    if (!items.length) {
      // 클라우드에서 네이버 검색이 IP로 막히면 영영 안 채워진다. 그때
      // "불러오는 중"만 띄우면 고장인 걸 알아챌 수가 없다.
      empty.textContent = latest.keywordNewsStatus === "failed"
        ? "네이버 뉴스 검색에 접근하지 못했습니다 (서버 IP 차단 가능성 · NAVER_CLIENT_ID/SECRET 설정 시 공식 API로 전환)"
        : "키워드 뉴스를 불러오는 중입니다.";
    }
    setHtml($("kwnews-list"), shown.map((n) => `
      <div class="kwn-row">
        <span class="mono kwn-when">${esc(n.when)}</span>
        <div style="font-size:12.5px;line-height:1.45;text-wrap:pretty;word-break:keep-all">
          ${safeUrl(n.url) ? `<a href="${esc(safeUrl(n.url))}" target="_blank" rel="noopener" style="color:inherit;text-decoration:none">${esc(n.headline)}</a>` : esc(n.headline)}
          ${n.press ? `<span style="font-size:10.5px;color:var(--c-label);margin-left:5px">${esc(n.press)}</span>` : ""}
        </div>
      </div>`).join(""));

    renderPagination("kwnews-pagination", kwNewsPage, totalPages, (p) => {
      kwNewsPage = p;
      renderKeywordNews();
    });
  }

  // 행 활성화 — 마우스 클릭과 키보드(Enter·Space)를 한 자리에서 받는다.
  // 컨테이너에 위임해 두면 innerHTML 이 갈려도 리스너가 살아남는다.
  // Space 는 기본 동작이 페이지 스크롤이라 눌린 자리에서 막아야 한다.
  function onRowActivate(containerId, selector, handler) {
    const el = $(containerId);
    const fire = (ev) => {
      if (ev.target.closest("button, a")) return;
      const row = ev.target.closest(selector);
      if (row) handler(row);
    };
    el.addEventListener("click", fire);
    el.addEventListener("keydown", (ev) => {
      if (ev.key !== "Enter" && ev.key !== " ") return;
      if (!ev.target.closest(selector)) return;
      ev.preventDefault();
      fire(ev);
    });
  }

  // Clicking any stock row (movers lists) opens the detail popup.
  for (const containerId of ["movers-list", "kr-most-traded-list", "us-movers-list", "us-most-active-list"]) {
    onRowActivate(containerId, "[data-symbol]", (row) => {
      if (row.dataset.symbol) openStockPopup(row.dataset.symbol, row.dataset.name);
    });
  }

  // Same pattern for FX / index / commodity / rate rows -> the generic
  // indicator popup (kind carried in data-kind).
  for (const containerId of ["ticker-strip", "fx-regions", "idx-main", "rates-list", "kr-rates-list", "commodities-list"]) {
    onRowActivate(containerId, "[data-kind]", (row) => {
      openIndicatorPopup(row.dataset.kind, row.dataset.symbol, row.dataset);
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
    // 영문판은 네이버에서 온 한국어 헤드라인을 빼고 Yahoo 것만 남긴다.
    // origin 이 없는 옛 스냅샷이면 한글이 섞였는지로 가른다.
    const rows = isEn()
      ? latest.news.filter((n) =>
          n.origin ? n.origin !== "naver" : !/[가-힯]/.test(n.headline || ""))
      : latest.news;
    return rows.filter(newsMatchesKeywords);
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
    if (totalPages <= 1) { setHtml(box, ""); box.style.display = "none"; return; }
    box.style.display = "flex";
    const btn = (label, page, opts = {}) => `
      <button type="button" data-page="${page}" ${opts.disabled ? "disabled" : ""}
        style="min-width:24px;padding:3px 8px;font-size:11px;font-family:var(--font-mono);border:1px solid var(--rule-thin);background:${page === current ? "var(--color-text)" : "transparent"};color:${page === current ? "var(--color-bg)" : "inherit"};cursor:${opts.disabled ? "default" : "pointer"}">${label}</button>`;

    // 전체 페이지를 다 뿌리면 뉴스가 많은 날 버튼이 스무 개를 넘어 모바일에서
    // 두세 줄을 먹는다. 처음·끝과 현재 주변만 남기고 사이는 … 로 접는다.
    const keep = new Set([1, totalPages, current - 1, current, current + 1]);
    if (current <= 3) [2, 3, 4].forEach((p) => keep.add(p));
    if (current >= totalPages - 2) [totalPages - 3, totalPages - 2, totalPages - 1].forEach((p) => keep.add(p));
    const pages = [...keep].filter((p) => p >= 1 && p <= totalPages).sort((a, b) => a - b);

    const gap = '<span style="min-width:16px;text-align:center;font-size:11px;color:var(--c-label)">…</span>';
    let body = "";
    pages.forEach((p, i) => {
      if (i && p - pages[i - 1] > 1) body += gap;
      body += btn(p, p);
    });

    // 페이저 모양이 그대로면 버튼도 그대로다 — 다시 그리지 않았는데 또
    // 묶으면 같은 버튼에 리스너가 겹쳐 쌓인다.
    const changed = setHtml(box, btn("‹", Math.max(1, current - 1), { disabled: current === 1 })
      + body
      + btn("›", Math.min(totalPages, current + 1), { disabled: current === totalPages }));
    if (!changed) return;

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

    // 환율 뉴스와 같은 격자 (.nws-list) — 시각 칸이 목록 전체에서 한 폭이라
    // "08/17 14:05" 처럼 긴 시각이 섞여도 제목이 한 줄에서 시작한다.
    setHtml($("news-list"), shown.map((n) => `
      <span class="mono nws-when">${esc(n.time)}</span>
      <div class="nws-body">
        ${safeUrl(n.url) ? `<a href="${esc(safeUrl(n.url))}" target="_blank" rel="noopener" style="color:inherit;text-decoration:none">${esc(n.headline)}</a>` : esc(n.headline)}
        <span class="tag tag-accent" style="font-size:9px;padding:0 6px;vertical-align:middle">${esc(n.tag)}</span>
      </div>`).join(""));

    $("news-empty").style.display = (latest && latest.news && latest.news.length > 0 && all.length === 0) ? "block" : "none";
    renderPagination("news-pagination", newsPage, totalPages, (p) => { newsPage = p; renderNews(); });
    // 영문판은 목록에서 뺀 한국어 기사까지 세면 숫자가 화면과 안 맞는다 —
    // 키워드 필터 전, 언어 필터 후의 건수를 센다.
    const feed = !latest || !latest.news ? null
      : (isEn() ? latest.news.filter((n) =>
          n.origin ? n.origin !== "naver" : !/[가-힯]/.test(n.headline || "")) : latest.news);
    $("news-count").textContent = feed ? fmtCount(feed.length, true) : "";
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
    renderKrwSwap();
    renderShortTermRates();
    renderReserveDrains();
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
    // 화면에 보이는 이름(영문판이면 영문)으로 찍되, 원문도 alt 에 남겨
    // 영문 화면에서 "삼성" 처럼 한글로 쳐도 걸리게 한다.
    const push = (it, alt) => out.push(Object.assign(it, { alt: alt || "" }));
    (latest.fxRegions || []).forEach((g) => (g.rows || []).forEach((r) => push({
      type: "ind", kind: "fx", symbol: r.symbol, label: tr(r.pair), sub: tr(r.name),
      tag: t("tag.fx"), data: { name: r.name, pair: r.pair },
    }, `${r.pair} ${r.name}`)));
    (latest.idxMain || []).forEach((r) => push({
      type: "ind", kind: "index", symbol: r.symbol, label: tr(r.name), sub: "",
      tag: t("tag.index"), data: { name: r.name },
    }, r.name));
    (latest.rates || []).forEach((r) => push({
      type: "ind", kind: "rate", symbol: r.symbol, label: tr(r.name), sub: r.sub,
      tag: t("tag.usrate"), data: { name: r.name, sub: r.sub },
    }, r.name));
    (latest.krRates || []).forEach((r) => push({
      type: "ind", kind: "krrate", symbol: r.code, label: tr(r.name), sub: "",
      tag: t("tag.krrate"), data: { name: r.name },
    }, r.name));
    (latest.commAll || []).forEach((r) => push({
      type: "ind", kind: "commodity", symbol: r.symbol, label: tr(r.name), sub: r.contract,
      tag: t("tag.commodity"), data: { name: r.name, contract: r.contract },
    }, r.name));
    return out;
  }

  function gsHide() { gsResults.style.display = "none"; gsResults.innerHTML = ""; gsItems = []; }

  function gsRender(indMatches, stockMatches) {
    gsItems = [...indMatches, ...stockMatches];
    if (!gsItems.length) {
      gsResults.innerHTML = `<div style="padding:10px;font-size:12px;color:var(--c-label)">${esc(t("gs.noresult"))}</div>`;
      gsResults.style.display = "block";
      return;
    }
    const secLabel = (t) => `<div style="padding:6px 10px 3px;font-size:9.5px;letter-spacing:.12em;text-transform:uppercase;color:var(--color-accent);border-bottom:1px solid var(--rule-mid)">${t}</div>`;
    const row = (it, i) => `
      <div class="gs-row" data-i="${i}" style="padding:8px 10px;font-size:12.5px;cursor:pointer;display:flex;justify-content:space-between;align-items:baseline;gap:8px;border-bottom:1px dotted var(--rule-hair)">
        <span>${esc(it.label)}${it.sub ? ` <span style="color:var(--c-label);font-size:11px">${esc(it.sub)}</span>` : ""}</span>
        <span style="color:var(--c-label);font-size:11px;white-space:nowrap">${esc(it.tag)}</span>
      </div>`;
    let html = "";
    let i = 0;
    if (indMatches.length) {
      html += secLabel(esc(t("gs.indicators")));
      indMatches.forEach((it) => { html += row(it, i++); });
    }
    if (stockMatches.length) {
      html += secLabel(esc(t("gs.stocks")));
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
      || (it.alt || "").toLowerCase().includes(needle)
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
          label: it.name, sub: `${it.market} · ${it.symbol}`, tag: t("tag.stock"),
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
      $(ids.label).textContent = tr(chart.label || "");
      $(ids.hilo).textContent = tr(chart.hiloText || "");
      $(prefix + "-axis-l").textContent = chart.axisL || "";
      $(prefix + "-axis-r").textContent = chart.axisR || "";
    } else {
      drawPopChart(prefix, null);
      $(ids.hilo).textContent = t("chart.nodata");
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

  // 서울 시각. toLocaleString 으로 만든 문자열을 Date 로 되파싱하는 방법은
  // 브라우저마다 받아주는 형식이 달라 깨질 수 있다 — 포맷터가 쪼개준
  // 조각을 그대로 읽는다.
  const KST_FMT = new Intl.DateTimeFormat("en-US", {
    timeZone: "Asia/Seoul", hour12: false,
    year: "numeric", month: "2-digit", day: "2-digit",
    hour: "2-digit", minute: "2-digit", second: "2-digit",
  });

  function kstNow() {
    const out = {};
    for (const { type, value } of KST_FMT.formatToParts(new Date())) out[type] = value;
    // 자정은 로케일에 따라 24 로 나온다.
    if (out.hour === "24") out.hour = "00";
    return out;
  }

  function nowAsOf() {
    const k = kstNow();
    const clock = `${k.hour}:${k.minute}:${k.second} KST`;
    return isEn() ? `as of ${clock}` : `기준 ${clock}`;
  }

  // -- 모달 공통: 포커스 가두기 + 뒤 페이지 잠금 --------------------------
  // 팝업이 떠 있는데 Tab 이 뒤쪽 보드로 새 나가면 키보드 사용자는 자기가
  // 어디 있는지 알 수 없다. 열 때 포커스를 안으로 들여놓고, Tab 이 마지막
  // 요소를 넘어가면 처음으로 돌려보내고, 닫을 때 원래 자리로 되돌린다.

  const FOCUSABLE = 'button:not([disabled]), a[href], input, [tabindex]:not([tabindex="-1"])';
  let lastFocused = null;

  function lockScroll(on) {
    const html = document.documentElement;
    if (on) {
      // 스크롤바가 사라지며 생기는 폭 차이를 메워 배경이 튀지 않게 한다.
      const gap = window.innerWidth - html.clientWidth;
      if (gap > 0) document.body.style.paddingRight = `${gap}px`;
      html.classList.add("modal-open");
    } else {
      html.classList.remove("modal-open");
      document.body.style.paddingRight = "";
    }
  }

  function openModal(backdrop) {
    lastFocused = document.activeElement;
    backdrop.hidden = false;
    lockScroll(true);
    const first = backdrop.querySelector(FOCUSABLE);
    if (first) first.focus();
  }

  function closeModal(backdrop) {
    backdrop.hidden = true;
    lockScroll(false);
    if (lastFocused && lastFocused.focus) lastFocused.focus();
    lastFocused = null;
  }

  function trapFocus(backdrop) {
    backdrop.addEventListener("keydown", (ev) => {
      if (ev.key !== "Tab") return;
      const items = [...backdrop.querySelectorAll(FOCUSABLE)].filter((el) => el.offsetParent !== null);
      if (!items.length) return;
      const first = items[0], last = items[items.length - 1];
      if (ev.shiftKey && document.activeElement === first) {
        ev.preventDefault();
        last.focus();
      } else if (!ev.shiftKey && document.activeElement === last) {
        ev.preventDefault();
        first.focus();
      }
    });
  }

  function popupNewsHtml(news) {
    if (!news || !news.length) return `<div style="font-size:12px;color:var(--c-label);padding:6px 0">${esc(t("news.none"))}</div>`;
    return news.map((n) => `
      <div class="pop-news-row">
        ${safeUrl(n.url)
          ? `<a href="${esc(safeUrl(n.url))}" target="_blank" rel="noopener">${esc(n.headline)}</a>`
          : `<span style="font-family:var(--font-body);font-size:12.5px;line-height:1.4;color:var(--color-text);white-space:normal">${esc(n.headline)}</span>`}
      </div>`).join("");
  }

  // -- stock detail popup ------------------------------------------------

  const popupBackdrop = $("stock-popup-backdrop");
  let currentPopupSymbol = null;

  async function openStockPopup(symbol, name) {
    currentPopupSymbol = symbol;
    chartCtx.stk = { kind: "stock", symbol };
    openModal(popupBackdrop);
    setRangePressed("stk-range", "1D");
    $("stk-name").textContent = name || symbol;
    $("stk-code").textContent = "";
    $("stk-market").textContent = "";
    $("stk-price").textContent = "…";
    $("stk-price").style.color = "";
    $("stk-chg").textContent = "";
    $("stk-asof").textContent = "";
    $("stk-source").textContent = "";
    $("stk-chart-label").textContent = t("pop.intraday");
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
      $("stk-source").textContent = isKr ? t("src.naverLive") : t("src.yahooLive");
      applyChart("stk", d.chart);

      $("stk-volume").textContent = amt(d.volume);
      $("stk-tradingValue").textContent = amt(d.tradingValue);
      $("stk-marketCap").textContent = amt(d.marketCap);
      $("stk-open").textContent = d.open;
      $("stk-high").textContent = d.high;
      $("stk-low").textContent = d.low;
      $("stk-week52High").textContent = d.week52High;
      $("stk-week52Low").textContent = d.week52Low;
      $("stk-foreignRate").textContent = d.foreignRate;
      $("stk-per").textContent = tr(d.per);
      $("stk-pbr").textContent = tr(d.pbr);
      $("stk-prevClose").textContent = d.prevClose;

      $("stk-news").innerHTML = popupNewsHtml(d.news);
    } catch (e) {
      console.error("stock detail fetch failed", e);
      $("stk-price").textContent = t("err.generic");
      applyChart("stk", null);
    }
  }

  function closeStockPopup() {
    currentPopupSymbol = null;
    chartCtx.stk = null;
    closeModal(popupBackdrop);
  }

  trapFocus(popupBackdrop);
  $("stk-close").addEventListener("click", closeStockPopup);
  $("stk-close-btn").addEventListener("click", closeStockPopup);
  popupBackdrop.addEventListener("click", (ev) => { if (ev.target === popupBackdrop) closeStockPopup(); });
  document.addEventListener("keydown", (ev) => { if (ev.key === "Escape" && !popupBackdrop.hidden) closeStockPopup(); });

  // -- indicator (FX / index / commodity / 미국·한국 금리) detail popup ----

  const indicatorBackdrop = $("indicator-popup-backdrop");
  let currentIndicatorKey = null;

  const INDICATOR_SOURCES = {
    fx: "src.yahooLive",
    index: "src.yahooLive",
    commodity: "src.yahooLive",
    rate: "src.yahooLive",
    krrate: "src.naverIndex",
  };
  const NAVER_INDEX_SYMBOLS = ["^KS11", "^KQ11", "^KS200"];

  function renderIndicatorStats(stats) {
    $("ind-stats").innerHTML = stats.map((s) => `
      <div class="stat"><span>${esc(tr(s.label))}</span><b${s.color ? ` style="color:${s.color}"` : ""}>${esc(amt(tr(s.value)))}</b></div>
    `).join("");
  }

  async function openIndicatorPopup(kind, symbol, data) {
    const key = `${kind}:${symbol}`;
    currentIndicatorKey = key;
    chartCtx.ind = { kind, symbol };
    openModal(indicatorBackdrop);
    setRangePressed("ind-range", "1D");
    $("ind-title").textContent = tr(data.pair || data.name || symbol);
    $("ind-subtitle").textContent = kind === "fx" ? tr(data.name || "") : (data.sub || "");
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

      $("ind-title").textContent = tr(d.title);
      $("ind-subtitle").textContent = tr(d.subtitle || "");
      $("ind-tag").textContent = tr(d.tag || "");
      $("ind-price").textContent = d.price;
      $("ind-price").style.color = d.color;
      $("ind-chg").textContent = `${d.arrow} ${d.chg}${d.pct ? ` (${tr(d.pct)})` : ""}`;
      $("ind-chg").style.color = d.color;
      $("ind-asof").textContent = nowAsOf();
      $("ind-source").textContent = (kind === "index" && NAVER_INDEX_SYMBOLS.includes(symbol))
        ? t("src.naverLive")
        : (INDICATOR_SOURCES[kind] ? t(INDICATOR_SOURCES[kind]) : "");
      applyChart("ind", d.chart);
      renderIndicatorStats(d.stats || []);

      if (d.news && d.news.length) {
        $("ind-news-wrap").style.display = "";
        $("ind-news").innerHTML = popupNewsHtml(d.news);
      }
    } catch (e) {
      console.error("indicator detail fetch failed", e);
      $("ind-price").textContent = t("err.generic");
      applyChart("ind", null);
    }
  }

  function closeIndicatorPopup() {
    currentIndicatorKey = null;
    chartCtx.ind = null;
    closeModal(indicatorBackdrop);
  }

  trapFocus(indicatorBackdrop);
  $("ind-close").addEventListener("click", closeIndicatorPopup);
  $("ind-close-btn").addEventListener("click", closeIndicatorPopup);
  indicatorBackdrop.addEventListener("click", (ev) => { if (ev.target === indicatorBackdrop) closeIndicatorPopup(); });
  document.addEventListener("keydown", (ev) => { if (ev.key === "Escape" && !indicatorBackdrop.hidden) closeIndicatorPopup(); });

  // -- clock -----------------------------------------------------------

  function tickClock() {
    const t = kstNow();
    $("clock").textContent = `${t.hour}:${t.minute}:${t.second} KST`;
    $("today").textContent = `${t.year}.${t.month}.${t.day}`;
  }
  tickClock();
  setInterval(tickClock, 1000);

  // -- SEIBro 미국 주식 보관금액 (월별) ----------------------------------

  // USD 금액을 "1,493억 달러" 형태로. (억 = 1e8)
  function fmtCustody(v) {
    if (isEn()) return "$" + (v / 1e9).toFixed(1) + "B";
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
    return p > 0 ? "var(--c-up)" : p < 0 ? "var(--c-down)" : "var(--c-dim)";
  }

  function statTile(label, value, sub, subColor) {
    return `<div style="display:flex;flex-direction:column;gap:2px">
      <span style="font-size:9.5px;font-weight:600;letter-spacing:.14em;color:var(--c-label);text-transform:uppercase">${esc(label)}</span>
      <span class="mono" style="font-size:21px;font-weight:400;letter-spacing:-.03em;line-height:1.1">${esc(value)}</span>
      ${sub ? `<span class="mono" style="font-size:11px;color:${subColor || "var(--c-dim)"}">${esc(sub)}</span>` : ""}
    </div>`;
  }

  function custodyChartSvg(points, w = 1000, h = 240) {
    // padL 은 y축 눈금 글자("$149.3B")가 들어갈 만큼 잡는다.
    const padL = 62, padR = 34, padT = 34, padB = 30;
    // CSS 변수는 SVG 프레젠테이션 속성(fill="…")에는 안 먹지만 style 선언에는
    // 먹는다 — 이름 그대로 흘려 두면 밝은 판/다크 판이 바뀔 때 차트도 같이 간다.
    const accent = "var(--color-text)";     // 크롬은 무채색. 유채색은 데이터 몫이다
    const divider = "var(--rule-hair)";
    const bg = "var(--color-bg)";
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

    // y축 — 만기별 곡선과 같은 방식으로 네 단계 눈금에 값을 붙인다.
    // 눈금이 없으면 선의 높낮이만 보이고 그게 얼마인지는 hover 해야 알 수 있다.
    const grid = [0, 1, 2, 3].map((i) => {
      const v = lo + (rng * i) / 3;
      const y = ys(v);
      return `<line x1="${padL}" y1="${y.toFixed(1)}" x2="${padL + iw}" y2="${y.toFixed(1)}" style="stroke:${divider}" stroke-width="1"/>`
        + `<text x="${padL - 8}" y="${(y + 3.5).toFixed(1)}" text-anchor="end" font-size="10.5"`
        + ` style="fill:var(--c-label)" font-family="monospace">${esc(fmtUsdShort(v))}</text>`;
    }).join("");

    // 데이터 포인트 (기본은 빈 점)
    const dots = coords.map(([x, y]) =>
      `<circle cx="${x.toFixed(1)}" cy="${y.toFixed(1)}" r="2.8" style="fill:${bg};stroke:${accent}" stroke-width="1.4"/>`
    ).join("");

    // x축 월 라벨 (YY.MM)
    const labels = points.map((p, i) => {
      const [yy, mm] = p.month.split("-");
      return `<text x="${coords[i][0].toFixed(1)}" y="${(h - 8).toFixed(1)}" text-anchor="middle" font-size="10" style="fill:var(--c-label)" font-family="monospace">${yy.slice(2)}.${mm}</text>`;
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
      <circle class="hv-dot" r="4.5" style="fill:${accent};stroke:${bg}" stroke-width="1.6"/>
      <text class="hv-val" text-anchor="middle" font-family="monospace" font-size="13" font-weight="700"
        style="fill:${accent};paint-order:stroke;stroke:${bg};stroke-width:3.5px;stroke-linejoin:round"></text>
      <text class="hv-month" text-anchor="middle" font-family="monospace" font-size="10"
        style="fill:var(--c-dim);paint-order:stroke;stroke:${bg};stroke-width:3px;stroke-linejoin:round"></text>
    </g>`;

    return `<svg viewBox="0 0 ${w} ${h}" style="display:block;width:100%;height:auto">
      ${grid}
      <path d="${area}" style="fill:${accent}" fill-opacity="0.10"></path>
      <path d="${line}" fill="none" style="stroke:${accent}" stroke-width="1.8" stroke-linejoin="round" stroke-linecap="round"></path>
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
      setHtml(chart, "");
      stats.innerHTML = "";
      empty.style.display = "block";
      return;
    }
    empty.style.display = "none";
    $("custody-source").textContent = tr(d.source || "");

    const s = d.stats;
    const latestMonth = s.latest.month;
    const firstMonth = d.points[0].month;
    const momSub = isEn()
      ? `${s.change >= 0 ? "+" : "−"}${fmtUsdShort(Math.abs(s.change)).slice(1)}`
      : `${s.change >= 0 ? "+" : ""}${fmtCustody(Math.abs(s.change)).replace("억 달러", "")}억`;
    stats.innerHTML = [
      statTile(`${t("cu.latest")} (${latestMonth})`, fmtCustody(s.latest.amount), fmtUsdShort(s.latest.amount)),
      statTile(t("cu.mom"), fmtPct(s.changePct), momSub, pctColor(s.changePct)),
      statTile(`${t("cu.yoy")} (${firstMonth}→)`, fmtPct(s.yoyPct), fmtUsdShort(Math.abs(s.yoyChange)), pctColor(s.yoyPct)),
      statTile(t("cu.maxmin"), `${fmtUsdShort(s.max.amount)} / ${fmtUsdShort(s.min.amount)}`, `${s.max.month} / ${s.min.month}`),
    ].join("");

    // 같은 이유로, 차트를 새로 그렸을 때만 hover 를 다시 묶는다 —
    // 24개 밴드 × 3개 리스너가 30분마다 겹쳐 쌓이면 안 된다.
    if (setHtml(chart, custodyChartSvg(d.points))) wireCustodyHover(chart);
  }

  // 마지막으로 받은 보관금액. 30분마다 한 번 오는 데이터라 언어를 바꿨다고
  // 다시 받을 이유가 없다 — 들고 있던 걸로 다시 그린다.
  let custodyData = null;

  async function loadCustody() {
    try {
      const r = await fetch("/api/seibro-custody");
      if (!r.ok) throw new Error(r.status);
      custodyData = await r.json();
      renderCustody(custodyData);
    } catch (e) {
      console.error("custody load failed", e);
      custodyData = null;
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
    connState = state;
    text.textContent = state === "up" ? t("conn.up")
      : state === "connecting" ? t("conn.connecting") : t("conn.down");
  }

  function connect() {
    setConn("connecting");
    const proto = location.protocol === "https:" ? "wss" : "ws";
    const ws = new WebSocket(`${proto}://${location.host}/ws?scheme=kr&delta=1`);

    ws.onopen = () => { setConn("up"); backoff = 1000; };
    ws.onmessage = (ev) => {
      // 서버는 접속 직후 한 번만 전체(full)를 보내고, 이후엔 직전 틱과 달라진
      // 섹션만(patch) 보낸다 — 스냅샷 210KB 중 10초 사이에 실제로 바뀌는 건
      // 시세 몇 KB뿐이라 통째로 받으면 나머지가 그대로 낭비된다. render() 는
      // 완전한 스냅샷을 전제로 하므로 여기서 직전 상태에 덧발라 복원한다.
      try {
        const msg = JSON.parse(ev.data);
        if (msg.type === "patch") {
          if (!latest) return;  // full 보다 먼저 올 일은 없지만, 오면 버린다
          render(Object.assign({}, latest, msg.data));
        } else {
          render(msg.type === "full" ? msg.data : msg);
        }
      } catch (e) { console.error("bad snapshot", e); }
    };
    ws.onclose = () => {
      setConn("down");
      setTimeout(connect, backoff);
      backoff = Math.min(backoff * 1.7, MAX_BACKOFF);
    };
    ws.onerror = () => ws.close();
  }
  // -- 패널 접기 ------------------------------------------------------
  // 주식 수급 아래로는(HTML 에서 .collapsible 이 붙은 패널) 기본이 접힘이다.
  // 헤더를 누르면 펴지고, 편 패널 목록은 localStorage 에 남는다.
  //
  // 마크업을 여기서 한 겹 감싸는 이유: 패널마다 헤더 생김새가 제각각이라
  // (.panel-head 인 것, 맨 h4 인 것, 클래스 없는 flex div 인 것) HTML 을
  // 11군데 손으로 고치는 것보다 "첫 요소가 헤더, 나머지가 본문" 이라는
  // 한 가지 규칙으로 처리하는 편이 낫다. 패널이 늘어도 클래스만 붙이면 된다.
  const COLLAPSE_KEY = "fxdesk.openPanels";

  // 편 패널을 화면 맨 위로 데려온다. 아래쪽 색인을 눌러 폈을 때 내용이
  // 화면 밖에서 펼쳐지면 아무 일도 안 일어난 것처럼 보인다.
  // 헤더가 sticky 라 그 높이만큼 빼야 제목이 헤더에 가리지 않는다.
  //
  // 누르는 즉시 출발한다. 걸림돌은 접힌 패널들이 페이지 맨 아래에 색인처럼
  // 쌓여 있다는 것 — 누른 순간에는 문서가 목표 지점까지 갈 만큼 길지 않아
  // (아래쪽 패널은 600px 넘게 모자랐다) 브라우저가 갈 수 있는 데까지만 가고
  // 멈춘다. 그래서 스크롤을 걸기 **전에** 모자라는 만큼 꼬리 여백을 먼저
  // 달아 문서를 늘려 놓는다. 펼침이 끝나 내용이 자리를 차지하면 꼬리는
  // 그만큼 도로 줄어든다.
  function scrollPanelIntoView(panel) {
    fitScrollTail(panel);            // 먼저 길을 내고
    scrollToPanel(panel, 0.9);       // 곧바로 출발한다

    // 펼치는 동안 문서가 계속 자란다 — 다 자라면 꼬리를 그만큼 줄인다.
    // 전환이 취소되면 transitionend 가 안 오므로 타이머도 같이 건다.
    const body = panel.querySelector(".panel-body");
    if (!body) return;
    let done = false;
    const finish = () => {
      if (done) return;
      done = true;
      body.removeEventListener("transitionend", onEnd);
      fitScrollTail(panel.classList.contains("open") ? panel : null);
    };
    const onEnd = (ev) => {
      if (ev.target === body && ev.propertyName === "grid-template-rows") finish();
    };
    body.addEventListener("transitionend", onEnd);
    setTimeout(finish, 620);
  }

  function panelTargetY(panel) {
    const nav = document.querySelector(".nav");
    const pad = (nav ? nav.offsetHeight : 0) + 8;
    return Math.max(0, panel.getBoundingClientRect().top + window.scrollY - pad);
  }

  function scrollToPanel(panel, duration) {
    const to = panelTargetY(panel);
    if (Math.abs(to - window.scrollY) < 2) return;
    // Lenis 는 문서 길이를 스스로 재 두는데, 방금 꼬리를 달아 늘어난 참이면
    // 아직 옛 길이를 들고 있다. 가기 전에 다시 재게 한다.
    if (window.__lenis) {
      window.__lenis.resize();
      window.__lenis.scrollTo(to, { duration });
    } else {
      window.scrollTo({ top: to, behavior: "smooth" });
    }
  }

  // 맨 아래 패널들은 뒤에 남은 내용이 없어, 다 펼쳐도 제목을 화면 위로
  // 끌어올릴 만큼 페이지가 길지 않다. 모자라는 만큼만 꼬리 여백을 달아
  // 준다 — 어느 구획을 펴든 같은 자리에 서게 하려는 것이고, 접으면
  // 도로 걷어서 빈 공간이 남지 않는다.
  function scrollTail() {
    let tail = $("scroll-tail");
    if (!tail) {
      tail = document.createElement("div");
      tail.id = "scroll-tail";
      tail.setAttribute("aria-hidden", "true");
      tail.style.height = "0px";
      document.querySelector(".page-pad")?.appendChild(tail);
    }
    return tail;
  }

  // `hold` 는 "지금 서 있는 자리를 지켜라" 는 뜻이다. 펼치는 중에는 켠다 —
  // 꼬리를 줄이다 스크롤이 위로 딸려 올라가면 방금 연 패널이 도로 내려가
  // 보인다. 접을 때는 끈다: 내용이 줄었으니 화면이 따라 올라가는 게 맞고,
  // 그러지 않으면 접은 자리에 빈 꼬리만 덩그러니 남는다.
  function fitScrollTail(panel, hold = true) {
    const tail = scrollTail();
    const now = parseFloat(tail.style.height) || 0;
    // 꼬리를 뺀 문서 길이로 계산한다. 재기 위해 높이를 0 으로 되돌렸다가
    // 다시 넣으면, 그 찰나에 스크롤이 문서 끝으로 끌려 내려가 화면이 튄다.
    const bare = document.documentElement.scrollHeight - now - window.innerHeight;
    let need = panel && panel.classList.contains("open")
      ? Math.ceil(panelTargetY(panel) - bare)
      : 0;
    if (hold) need = Math.max(need, Math.ceil(window.scrollY - bare));
    tail.style.height = `${Math.max(0, need)}px`;
  }

  function initCollapsibles() {
    let open = [];
    try {
      open = JSON.parse(localStorage.getItem(COLLAPSE_KEY)) || [];
    } catch (e) { /* 저장값이 깨졌으면 기본(전부 접힘)으로 간다 */ }

    document.querySelectorAll(".panel.collapsible").forEach((panel) => {
      // .corner 는 .blueprint 의 직계 자식이어야 모서리 표시가 산다 — 그대로 둔다.
      const kids = [...panel.children].filter((el) => !el.classList.contains("corner"));
      const head = kids.shift();
      if (!head) return;
      head.classList.add("pnl-head");

      const body = document.createElement("div");
      body.className = "panel-body";
      const inner = document.createElement("div");
      kids.forEach((el) => inner.appendChild(el));
      body.appendChild(inner);
      panel.appendChild(body);

      if (open.includes(panel.dataset.panel)) panel.classList.add("open");
      head.setAttribute("role", "button");
      head.setAttribute("tabindex", "0");
      head.setAttribute("aria-expanded", panel.classList.contains("open"));

      const toggle = () => {
        panel.classList.toggle("open");
        head.setAttribute("aria-expanded", panel.classList.contains("open"));
        if (panel.classList.contains("open")) {
          // 접혀 있는 동안에는 높이가 0이라 리빌이 돌 수 없었다 —
          // 펴지는 지금이 그 안의 표가 처음 보이는 순간이다.
          document.dispatchEvent(new CustomEvent("panel-opened", { detail: panel }));
          scrollPanelIntoView(panel);
        } else {
          // 접었으면 꼬리도 걷는다. 접히는 데 0.5초 걸리므로 그 뒤에 한 번 더 —
          // 지금 재면 아직 안 줄어든 높이를 보고 꼬리를 남겨 둔다.
          fitScrollTail(null, false);
          setTimeout(() => {
            if (!panel.classList.contains("open")) fitScrollTail(null, false);
          }, 620);
        }
        const keys = [...document.querySelectorAll(".panel.collapsible.open")]
          .map((p) => p.dataset.panel);
        try {
          localStorage.setItem(COLLAPSE_KEY, JSON.stringify(keys));
        } catch (e) { /* 사파리 프라이빗 모드 등 — 저장만 못 할 뿐 여닫기는 된다 */ }
      };

      head.addEventListener("click", (e) => {
        // 헤더에 기간 탭(코스피/코스닥, 1일/1주…)이나 뉴스 검색창이 얹혀
        // 있는 패널이 있다. 그걸 누른 건 접으라는 뜻이 아니다.
        if (e.target.closest("input,button,a,label,.seg")) return;
        toggle();
      });
      head.addEventListener("keydown", (e) => {
        if (e.key === "Enter" || e.key === " ") { e.preventDefault(); toggle(); }
      });
    });
  }

  // -- 한/영 전환 -------------------------------------------------------

  $("lang-ko").addEventListener("change", () => setLang("ko"));
  $("lang-en").addEventListener("change", () => setLang("en"));

  initCollapsibles();
  // 저장된 언어를 화면에 입힌다. 여기까지 와야 아래 캐시 변수들이 다 서 있다.
  $(lang === "en" ? "lang-en" : "lang-ko").checked = true;
  applyLang();
  connect();
})();
