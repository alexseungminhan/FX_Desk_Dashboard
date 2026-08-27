/* 보드의 움직임.
 *
 * 무엇을 움직이고 무엇을 두는지가 이 파일의 요점이다. 이 화면은 마케팅
 * 페이지가 아니라 초 단위로 값이 바뀌는 시세판이라, 값 자체는 절대
 * 미끄러뜨리지 않는다 — 움직이는 건 구획이 시야에 들고 날 때뿐이다.
 * 화면을 완전히 벗어나면 다시 감췄다가, 되돌아오면 또 떠오른다.
 *
 * 실제 움직임은 전부 CSS 전환이 맡고(원본 페이지의 .reveal / .mask-word),
 * 여기서는 "언제 시작할지"만 정한다. requestAnimationFrame 으로 프레임마다
 * 값을 찍는 방식(Motion 의 animate 같은)은 탭이 백그라운드로 가면 그대로
 * 얼어붙어 내용이 opacity:0 에 갇힌다 — 시세판이 그러면 고장으로 보인다.
 * CSS 는 도착 상태가 선언으로 박혀 있어서 전환이 안 돌더라도 보이는 상태로
 * 끝난다.
 *
 * 그래서 남는 일은 IntersectionObserver 하나뿐이라 라이브러리를 쓰지 않는다.
 * 부드러운 스크롤(Lenis)만 vendor/ 에 두었다 — 그건 직접 만들 물건이 아니다.
 *
 * prefers-reduced-motion 을 켠 사람에게는 전부 끈다.
 */
const reduced = matchMedia("(prefers-reduced-motion: reduce)").matches;

/* — 실패해도 화면은 보여야 한다 —
   .js-motion 이 붙어 있는 동안 리빌 대상은 opacity:0 이다. 이 모듈이 못
   뜨면(문법 오류·파일 누락) 페이지가 영영 빈 채로 남으므로, head 의 인라인
   스크립트가 걸어 둔 실패 타이머를 여기서 끈다. */
function markReady() {
  clearTimeout(window.__motionFailsafe);
  document.documentElement.classList.add("motion-ready");
}

// 계단 순서(--i)는 한 번만 심는다. 프레임마다 값을 찍지 않고 CSS 의
// transition-delay 가 알아서 밀어 준다.
function indexChildren(el) {
  if (el.dataset.staggered) return;
  el.dataset.staggered = "1";
  let i = 0;
  for (const c of el.children) {
    if (!c.classList.contains("corner")) c.style.setProperty("--i", i++);
  }
}

function revealNow(el, startDelay = 0) {
  indexChildren(el);
  el.style.transitionDelay = startDelay ? `${startDelay}s` : "";
  el.classList.add("revealed");
}

// 화면 밖으로 완전히 나가면 다시 감춘다 — 되돌아오면 또 떠오르게 하려는 것.
// 계단 지연은 첫 등장에만 쓰고 여기서 지운다. 안 지우면 다시 들어올 때마다
// 처음 순서대로 밀려서, 스크롤을 되짚는 사람에게는 굼뜨게만 보인다.
function unrevealNow(el) {
  el.style.transitionDelay = "";
  el.classList.remove("revealed");
}

function inViewportNow(el) {
  const r = el.getBoundingClientRect();
  return r.top < innerHeight && r.bottom > 0 && r.width > 0;
}

/* — 스크롤 진입 리빌 —
   구획(.blueprint)과 제목이 시야에 들어오면 아래에서 위로 떠오른다.
   관찰기는 하나만 두고 대상을 전부 물린다 — 요소마다 새로 만들면 스무 개가
   화면에 계속 살아 있게 된다.

   문턱을 둘로 잡은 게 요점이다. 12% 보이면 떠오르고, 완전히 벗어났을 때만
   (0%) 도로 감춘다. 하나로 잡으면 그 언저리에 걸친 구획이 스크롤을 조금
   움직일 때마다 깜빡인다. 사이 구간에서는 아무것도 하지 않는다. */
let revealObserver = null;

function observeReveal(el) {
  if (!revealObserver) {
    revealObserver = new IntersectionObserver((entries) => {
      for (const e of entries) {
        if (e.intersectionRatio >= 0.12) revealNow(e.target);
        else if (e.intersectionRatio <= 0) unrevealNow(e.target);
      }
    }, { threshold: [0, 0.12] });
  }
  revealObserver.observe(el);
}

function wireReveals() {
  // 첫 화면에 이미 들어와 있는 구획도 관찰기에 물린다. 다만 지금 보이는
  // 것들은 관찰기의 첫 콜백을 기다리지 않고 곧바로 계단식으로 올린다.
  //
  // 감춤 스타일이 붙은 바로 그 프레임에 .revealed 까지 붙이면 브라우저가
  // "이전 값"을 가진 적이 없어 전환을 건너뛰고 툭 나타난다 — 첫 화면
  // 구획만 페이드가 안 걸리던 이유다. 그래서 사이에 한 번 끊어 줘야 하는데,
  // requestAnimationFrame 으로 끊으면 안 된다: 백그라운드 탭에서 열면 rAF 가
  // 아예 안 돌아서 첫 화면이 빈 채로 남는다. offsetHeight 를 읽어 스타일을
  // 그 자리에서 확정시키면, 프레임을 기다리지 않고도 전환이 걸린다.
  const first = [];
  for (const el of document.querySelectorAll(".reveal, .mask-reveal")) {
    observeReveal(el);
    if (inViewportNow(el)) first.push(el);
  }
  void document.body.offsetHeight;
  first.forEach((el, i) => revealNow(el, i * 0.06));

  // 탭을 백그라운드에 두고 열면 관찰기가 늦게 깰 수 있다. 다시 보이는 순간
  // 화면에 들어와 있는 것만 한 번 쓸어 준다 — 최후의 안전장치.
  document.addEventListener("visibilitychange", () => {
    if (document.hidden) return;
    for (const el of document.querySelectorAll(".reveal:not(.revealed), .mask-reveal:not(.revealed)")) {
      if (inViewportNow(el)) revealNow(el);
    }
  });
}

/* — 제목 단어 마스크 리빌 —
   글자를 잘라 내는 게 아니라, 넘치는 부분을 감춘 칸 안에서 단어가 아래에서
   올라온다. 제목만 건드린다 — 본문 숫자에 하면 읽는 데 방해가 된다. */
function wireHeadingMasks() {
  for (const h of document.querySelectorAll(".mask-reveal")) {
    if (h.dataset.masked) continue;
    h.dataset.masked = "1";

    const parts = [];
    for (const node of h.childNodes) {
      if (node.nodeType === Node.TEXT_NODE) {
        for (const w of node.textContent.split(/(\s+)/)) {
          if (w.trim()) parts.push({ html: w });
          else if (w) parts.push({ space: true });
        }
      } else if (node.nodeType === Node.ELEMENT_NODE) {
        // 자식 요소는 감싸지 않고 그대로 둔다. 두 가지 이유가 있다:
        //  · 접기 꺾쇠(.pnl-caret)는 app.js 의 ensureCarets 가 h4 의 직계
        //    자식으로 찾는다. 마스크 안에 넣으면 못 찾고 매번 하나씩 더 심는다.
        //  · 부제(.kop)는 "PREDETERMINED SHORT-TERM NET DRAINS…" 처럼 길다.
        //    통째로 inline-block 으로 감싸면 그 안에서 줄이 안 바뀌어,
        //    좁은 화면에서 제목이 칸을 밀어내고 페이지에 가로 스크롤이 생긴다.
        parts.push({ raw: node.outerHTML });
      }
    }
    h.innerHTML = parts.map((p) => {
      if (p.space) return " ";
      if (p.raw) return p.raw;
      return `<span class="mask-line"><span class="mask-word">${p.html}</span></span>`;
    }).join("");

    h.querySelectorAll(".mask-word").forEach((w, i) => w.style.setProperty("--i", i));
    // 언제 올릴지는 wireReveals 가 정한다 — 구획과 같은 관찰기를 쓰게 해서
    // 제목만 따로 노는 일이 없게 한다.
  }
}

/* — Lenis 스무스 스크롤 —
   휠 한 번이 툭 끊기지 않고 미끄러져 멎는다. 이 화면은 세로로 아주 길어서
   (패널을 다 펴면 1만 px 가까이) 체감 차이가 크다. */
function startLenis() {
  if (!window.Lenis) return;
  const lenis = new window.Lenis({
    duration: 1.05,
    easing: (t) => Math.min(1, 1.001 - Math.pow(2, -10 * t)),
    smoothWheel: true,
    // 터치는 브라우저 기본 관성이 이미 좋다 — 가로채면 오히려 굼떠진다
    smoothTouch: false,
  });
  function raf(time) {
    lenis.raf(time);
    requestAnimationFrame(raf);
  }
  requestAnimationFrame(raf);
  window.__lenis = lenis;

  // 팝업이 뜨면 뒤 페이지는 얼어 있어야 한다 (app.js 의 lockScroll 이
  // html.modal-open 을 붙인다). Lenis 는 그걸 모르므로 여기서 멈춘다.
  new MutationObserver(() => {
    document.documentElement.classList.contains("modal-open") ? lenis.stop() : lenis.start();
  }).observe(document.documentElement, { attributes: true, attributeFilter: ["class"] });
}

if (reduced) {
  markReady();
} else {
  startLenis();
  wireHeadingMasks();   // 단어를 먼저 쪼개 두고
  wireReveals();        // 그 다음에 언제 올릴지 정한다
  markReady();
  // 패널을 펴면 그 안의 표가 처음 나타난다 — 그때도 같은 리빌을 건다.
  document.addEventListener("panel-opened", (ev) => {
    ev.detail?.querySelectorAll?.(".reveal:not(.revealed), .mask-reveal:not(.revealed)")
      .forEach((el) => revealNow(el));
  });
  // 한/영을 바꾸면 app.js 가 제목 innerHTML 을 통째로 갈아 끼워 마스크가
  // 쓸려 나간다 — 새 문구로 다시 잘라 넣는다.
  document.addEventListener("lang-applied", () => {
    for (const h of document.querySelectorAll(".mask-reveal[data-masked]")) {
      delete h.dataset.masked;
      h.classList.remove("revealed");
    }
    wireHeadingMasks();
    // 다시 잘랐으니 지금 보이는 제목은 곧바로 올려 준다 — 안 그러면 화면
    // 밖으로 나갔다 들어올 때까지 빈 자리로 남는다.
    void document.body.offsetHeight;
    for (const h of document.querySelectorAll(".mask-reveal:not(.revealed)")) {
      if (inViewportNow(h)) revealNow(h);
    }
  });
}
