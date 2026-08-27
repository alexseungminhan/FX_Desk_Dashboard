// FX Desk Board — 한/영 문구 사전.
//
// 보드의 문구는 두 갈래로 들어온다.
//
//   1) HTML·app.js 에 박혀 있는 고정 문구 (패널 제목·표 머리·각주…)
//      → STR 에서 키로 찾는다. t("idx.title") / data-i18n="idx.title".
//      값에 <span class="kop"> 같은 마크업이 들어 있다 — 한국어 화면은
//      "글로벌 지수 Global Indices" 처럼 국문 옆에 영문을 작게 달아 두는데
//      영문 화면에서는 그 짝이 통째로 사라져야 해서, 문구가 아니라 조각째
//      갈아 끼운다. 사전 값은 개발자가 쓴 것이라 innerHTML 로 넣어도 된다.
//
//   2) 서버 스냅샷에 실려 오는 라벨 (투자자 주체·채권 종류·만기·통계 항목…)
//      → DYN 에서 원문 그대로 찾는다. tr("외국인") -> "Foreign".
//      찾지 못하면 원문을 그대로 돌려준다 — 종목명·발행사처럼 번역할 수
//      없는 고유명사가 대부분이라, 못 찾는 게 정상인 값도 많다.
//
// 금액 단위(억·조)는 문구가 아니라 자릿수가 달라 따로 다룬다 — trAmount 참고.

(() => {
  "use strict";

  // -- 1) 고정 문구 -----------------------------------------------------
  const STR = {
    // 헤더
    "nav.search.ph": {
      ko: "종목·지표 검색 (예: 하이닉스, 환율, 금)",
      en: "Search stocks & indicators (e.g. Hynix, USD/KRW, Gold)",
    },
    "conn.connecting": { ko: "연결 중…", en: "Connecting…" },
    "conn.up": { ko: "실시간 연결됨", en: "Live" },
    "conn.down": { ko: "연결 끊김 · 재연결 중", en: "Disconnected · reconnecting" },

    // 환율
    "fx.title": {
      ko: '환율 <span class="kop" style="font-size:11px">FX · 주요 통화쌍</span>',
      en: 'FX <span class="kop" style="font-size:11px">Major Pairs</span>',
    },
    "fx.sub": {
      ko: "지역별 · 실시간 (Yahoo Finance)",
      en: "By region · live (Yahoo Finance)",
    },
    "fxnews.label": {
      ko: '환율 주요 뉴스 <span style="color:var(--c-label);letter-spacing:.04em;text-transform:none">· 네이버 금융 마켓인덱스</span>',
      en: "FX Headlines",
    },

    // 지수 · 금리 · 원자재
    "idx.title": {
      ko: '글로벌 지수 <span class="kop" style="font-size:11px">Global Indices</span>',
      en: "Global Indices",
    },
    "usrates.title": {
      ko: '미국 금리 <span class="kop" style="font-size:11px">US Rates · Treasury Yield Curve</span>',
      en: 'US Rates <span class="kop" style="font-size:11px">Treasury Yield Curve</span>',
    },
    "krrates.title": {
      ko: '한국 금리 <span class="kop" style="font-size:11px">KR Rates · 국내 시장금리 · 네이버 금융</span>',
      en: 'KR Rates <span class="kop" style="font-size:11px">Domestic market rates · Naver Finance</span>',
    },
    "comm.title": {
      ko: '원자재 <span class="kop" style="font-size:11px">Commodities · 근월</span>',
      en: 'Commodities <span class="kop" style="font-size:11px">Front month</span>',
    },

    // 등락·거래 상위
    "krmv.title": {
      ko: '국내 등락 상위 <span class="kop" style="font-size:11px">KR Movers</span> <span style="font-size:10.5px;color:var(--c-label);font-weight:400">(* 코스닥)</span>',
      en: 'KR Movers <span style="font-size:10.5px;color:var(--c-label);font-weight:400">(* KOSDAQ)</span>',
    },
    "krmt.title": {
      ko: '국내 거래 상위 <span class="kop" style="font-size:11px">KR Most Traded · 거래대금 순</span> <span style="font-size:10.5px;color:var(--c-label);font-weight:400">(* 코스닥)</span>',
      en: 'KR Most Traded <span class="kop" style="font-size:11px">By trading value</span> <span style="font-size:10.5px;color:var(--c-label);font-weight:400">(* KOSDAQ)</span>',
    },
    "usmv.title": {
      ko: '미국 등락 상위 <span class="kop" style="font-size:11px">US Movers · 주식 + ETF</span>',
      en: 'US Movers <span class="kop" style="font-size:11px">Stocks + ETFs</span>',
    },
    "usma.title": {
      ko: '미국 Most Active <span class="kop" style="font-size:11px">US Most Active · 거래량 순 · 주식 + ETF</span>',
      en: 'US Most Active <span class="kop" style="font-size:11px">By volume · Stocks + ETFs</span>',
    },
    "seg.up": { ko: "상승", en: "Gainers" },
    "seg.down": { ko: "하락", en: "Losers" },

    // 주식 수급
    "flow.title": {
      ko: '주식 수급 · 투자자별 수급 현황 <span class="kop" style="font-size:11px">Equity Investor Flows · 네이버 증권</span>',
      en: 'Equity Investor Flows <span class="kop" style="font-size:11px">Naver Finance</span>',
    },
    "flow.kospi": { ko: "코스피", en: "KOSPI" },
    "flow.kosdaq": { ko: "코스닥", en: "KOSDAQ" },
    "flow.futures": { ko: "선물", en: "Futures" },
    "flow.netbuy": { ko: "주체별 순매수", en: "Net buying by investor" },
    "per.1d": { ko: "1일", en: "1D" },
    "per.1w": { ko: "1주", en: "1W" },
    "per.1m": { ko: "1개월", en: "1M" },
    "per.3m": { ko: "3개월", en: "3M" },

    // 채권 수급
    "bflow.title": {
      ko: '채권 수급 · 투자자별 순매수 <span class="kop" style="font-size:11px">Bond Net Buy · KOFIA 장외채권</span>',
      en: 'Bond Net Buying <span class="kop" style="font-size:11px">KOFIA OTC bonds</span>',
    },

    // 원화 스왑
    "swap.title": {
      ko: '원화 스왑 <span class="kop" style="font-size:11px">KRW Swaps · 서울외국환중개 FX Swap · IRS · CRS</span>',
      en: 'KRW Swaps <span class="kop" style="font-size:11px">FX Swap · IRS · CRS</span>',
    },
    "swap.pt": { ko: "FX 스왑포인트", en: "FX Swap Points" },
    "swap.fximplied": {
      ko: "FX-implied 원화금리 · CCS 베이시스",
      en: "FX-implied KRW Yield · CCS Basis",
    },
    "swap.irscrs": { ko: "IRS · CRS", en: "IRS · CRS" },
    "swap.irscrs.note": {
      ko: "· 통화베이시스 = CRS − IRS",
      en: "· Cross-currency basis = CRS − IRS",
    },
    "swap.bondirs": { ko: "국고채 − IRS", en: "KTB − IRS" },
    "swap.bondirs.note": {
      ko: "· 스프레드 = 국고채 현물 − IRS",
      en: "· Spread = KTB cash − IRS",
    },

    // 채권 커브
    "curve.title": {
      ko: '채권 수익률 곡선 <span class="kop" style="font-size:11px">Bond Yield Curve · SEIBro 만기수익률 + KOFIA 최종호가</span>',
      en: 'Bond Yield Curve <span class="kop" style="font-size:11px">SEIBro yields + KOFIA closing quotes</span>',
    },
    "curve.quotes": { ko: "지표종목 최종호가", en: "Benchmark Closing Quotes" },
    "curve.bytype": { ko: "종류별 만기수익률", en: "Yields by Bond Type" },
    "curve.bytenor": { ko: "만기별 곡선", en: "Curve by Tenor" },

    // 단기금리
    "strate.title": {
      ko: '단기금융시장 금리 <span class="kop" style="font-size:11px">Short-Term Rates · KOFIA CP · 전자단기사채 대표수익률</span>',
      en: 'Short-Term Money Market Rates <span class="kop" style="font-size:11px">KOFIA · CP &amp; electronic short-term bonds</span>',
    },

    // 외환보유액 유출예정액
    "drain.title": {
      ko: '외환보유액 단기 유출예정액 <span class="kop" style="font-size:11px">Predetermined Short-Term Net Drains (Unit: US$ million)</span>',
      en: 'Predetermined Short-Term Net Drains <span class="kop" style="font-size:11px">On foreign currency assets · Unit: US$ million</span>',
    },

    // 외화표시채
    "fxbond.title": {
      ko: '외화표시채 발행 <span class="kop" style="font-size:11px">FX-Denominated Bond Issuance · SEIBro · 최근 1년</span>',
      en: 'FX-Denominated Bond Issuance <span class="kop" style="font-size:11px">SEIBro · last 12 months</span>',
    },
    "fxbond.empty": {
      ko: "발행 내역을 불러오지 못했습니다.",
      en: "Could not load issuance records.",
    },

    // 베이시스
    "basis.title": {
      ko: '코스피200 현·선물 <span class="kop" style="font-size:11px">Basis · 저평 / 고평</span>',
      en: 'KOSPI 200 Cash vs. Futures <span class="kop" style="font-size:11px">Basis · under / over valuation</span>',
    },

    // 보관금액
    "custody.title": {
      ko: '국내 투자자 미국 주식 보관금액 <span class="kop" style="font-size:11px">Korean Investors · US Equity Custody · 월별 · SEIBro</span>',
      en: 'Korean Investors · US Equity Custody <span class="kop" style="font-size:11px">Monthly · SEIBro</span>',
    },
    "custody.empty": {
      ko: "보관금액 데이터를 불러오지 못했습니다.",
      en: "Could not load custody data.",
    },

    // 주요 뉴스
    "news.title": {
      ko: '주요 뉴스 <span class="kop" style="font-size:11px">Headlines · 최근 24시간 · 네이버 증권 · Yahoo Finance</span>',
      en: 'Headlines <span class="kop" style="font-size:11px">Last 24 hours · Yahoo Finance</span>',
    },
    "news.kw.ph": {
      ko: "키워드로 검색 (Enter로 추가 · 여러 개 가능 · 최근 24시간 전체 대상)",
      en: "Filter by keyword (Enter to add · multiple allowed · searches the full 24h feed)",
    },
    "news.empty": {
      ko: "일치하는 키워드의 뉴스가 없습니다.",
      en: "No headlines match those keywords.",
    },

    "footer": {
      ko: "FX DESK · 실시간 시황 대시보드 — 데이터 출처: 국내 Naver Finance · 해외 Yahoo Finance (yfinance) · 마지막 갱신",
      en: "FX DESK · live market dashboard — sources: Naver Finance (KR) · Yahoo Finance (global) · last updated",
    },

    // 팝업
    "pop.close": { ko: "닫기", en: "Close" },
    "pop.intraday": { ko: "당일 분봉 · Intraday", en: "Intraday" },
    "pop.relnews": { ko: "관련 뉴스 Headlines", en: "Related Headlines" },
    "stat.volume": { ko: "거래량 (주)", en: "Volume (shares)" },
    "stat.tradingValue": { ko: "거래대금", en: "Trading Value" },
    "stat.marketCap": { ko: "시가총액", en: "Market Cap" },
    "stat.open": { ko: "시가", en: "Open" },
    "stat.high": { ko: "고가", en: "High" },
    "stat.low": { ko: "저가", en: "Low" },
    "stat.w52h": { ko: "52주 최고", en: "52W High" },
    "stat.w52l": { ko: "52주 최저", en: "52W Low" },
    "stat.foreignRate": { ko: "외국인 소진율", en: "Foreign Ownership" },
    "stat.prevClose": { ko: "전일 종가", en: "Prev Close" },

    // -- app.js 가 만들어내는 문구 -------------------------------------
    // 표 머리
    "h.investor": { ko: "투자자", en: "Investor" },
    "h.issue": { ko: "종목", en: "Issue" },
    "h.residual": { ko: "잔존기간", en: "Residual" },
    "h.yield": { ko: "수익률", en: "Yield" },
    "h.chgPrev": { ko: "전일대비", en: "Chg (bp)" },
    "h.yearRange": { ko: "연중 최저~최고", en: "YTD Low~High" },
    "h.tenor": { ko: "만기", en: "Tenor" },
    "h.days": { ko: "일수", en: "Days" },
    "h.annual": { ko: "연율", en: "Ann." },
    "h.basis": { ko: "베이시스", en: "Basis" },
    "h.ktb": { ko: "국고채", en: "KTB" },
    "h.spread": { ko: "스프레드", en: "Spread" },
    "h.kind": { ko: "구분", en: "Type" },
    // 등락·거래 상위 표 머리 (app.js 의 rankHead)
    "h.name": { ko: "종목명", en: "Name" },
    "h.price": { ko: "주가", en: "Price" },
    "h.chgpct": { ko: "등락률", en: "Change" },
    "h.value": { ko: "거래대금", en: "Turnover" },
    "h.volume": { ko: "거래량", en: "Volume" },
    "h.dayAmount": { ko: "당일 거래대금", en: "Turnover" },
    "h.bondType": { ko: "종류", en: "Bond Type" },
    "h.issueDate": { ko: "발행일", en: "Issued" },
    "h.issuer": { ko: "발행사", en: "Issuer" },
    "h.currency": { ko: "통화", en: "Ccy" },
    "h.amount": { ko: "발행액", en: "Amount" },
    "h.coupon": { ko: "쿠폰", en: "Coupon" },
    "h.maturity": { ko: "만기", en: "Maturity" },

    "sw.smbs": { ko: "SMBS · 서울외국환중개", en: "SMBS · Seoul Money Brokerage" },
    "sw.kmb": { ko: "KMB · 한국자금중개", en: "KMB · Korea Money Broker" },
    "fi.sixInterp": { ko: "6M IRS 보간", en: "6M IRS interpolation" },
    "fi.spread": { ko: "폭", en: "spread" },

    // 빈 상태 · 오류
    "err.flow": { ko: "수급 데이터를 불러오지 못했습니다.", en: "Could not load investor flows." },
    "err.bflow": { ko: "채권 수급을 불러오지 못했습니다.", en: "Could not load bond flows." },
    "err.quotes": { ko: "최종호가를 불러오지 못했습니다.", en: "Could not load closing quotes." },
    "err.swap": { ko: "스왑포인트를 불러오지 못했습니다.", en: "Could not load swap points." },
    "err.fximplied": {
      ko: "스왑포인트·IRS 1Y·CD 91일·USD 텀금리가 모두 있어야 계산됩니다.",
      en: "Needs swap points, 1Y IRS, 91D CD, and USD term rates to compute.",
    },
    "err.irscrs": { ko: "IRS·CRS를 불러오지 못했습니다.", en: "Could not load IRS/CRS." },
    "err.bondirs": {
      ko: "국고채 또는 IRS 데이터를 기다리는 중입니다.",
      en: "Waiting for KTB or IRS data.",
    },
    "err.strate": { ko: "단기금리를 불러오지 못했습니다.", en: "Could not load short-term rates." },
    "err.drain": { ko: "유출예정액을 불러오지 못했습니다.", en: "Could not load net drains." },
    "err.curve": { ko: "수익률 곡선을 불러오지 못했습니다.", en: "Could not load the yield curve." },
    "err.basis": { ko: "베이시스를 불러오지 못했습니다.", en: "Could not load the basis." },
    "err.generic": { ko: "오류", en: "Error" },
    "chart.nodata": { ko: "차트 데이터 없음", en: "No chart data" },
    "news.none": { ko: "관련 뉴스 없음", en: "No related headlines" },

    // 각주
    "foot.stale": { ko: "· 갱신 실패(직전 값)", en: "· refresh failed (previous value)" },
    "foot.bflow": {
      ko: "순매수 = 매수 − 매도 · 장외 거래대금",
      en: "Net buying = buys − sells · OTC turnover",
    },
    "foot.curve": {
      ko: "단위 % · 값 아래는 국고채권 대비 스프레드",
      en: "Unit % · figure below each value is the spread vs. KTB",
    },
    "foot.strate": {
      ko: "단위 % · 만기구간별 가중평균금리 · 거래가 거의 없는 구간의 금리는 참고치",
      en: "Unit % · weighted-average rate per bucket · thinly traded buckets are indicative only",
    },
    "foot.quotes": { ko: "단위 %", en: "unit %" },
    "foot.quoted": { ko: "고시", en: "quoted" },
    "foot.spotUsdkrw": { ko: "현물 USD/KRW", en: "Spot USD/KRW" },
    "foot.swapNote": {
      ko: "포인트 단위 전(錢) · Mid=(Bid+Offer)/2 · 연율은 각사 Mid 를 ACT/360 실제일수로 환산",
      en: "Points in jeon (0.01 KRW) · Mid=(Bid+Offer)/2 · annualized from each broker's Mid on ACT/360 actual days",
    },
    "foot.live": { ko: "실시간", en: "live" },
    "foot.spotDate": { ko: "스팟일", en: "spot date" },
    "foot.spotDateNote": { ko: "(고시일 T+2)", en: "(T+2 from quote date)" },
    "foot.spot": { ko: "스팟", en: "spot" },
    "foot.swapIrsQuoted": { ko: "스왑포인트·IRS", en: "Swap points · IRS" },
    "foot.midQuote": { ko: "Mid 고시", en: "Mid quotes" },

    // 베이시스 통계
    "bs.label": { ko: "베이시스 (선물−현물)", en: "Basis (futures − cash)" },
    "bs.spot": { ko: "현물 KOSPI200", en: "Cash KOSPI 200" },
    "bs.futures": { ko: "선물", en: "Futures" },
    "bs.theoretical": { ko: "이론가", en: "Fair value" },
    "bs.theoBasis": { ko: "이론 베이시스", en: "Theoretical basis" },
    "bs.spread": { ko: "괴리율", en: "Divergence" },
    "bs.toExpiry": { ko: "만기까지", en: "To expiry" },
    "bs.days": { ko: "일", en: "days" },

    // 보관금액 통계
    "cu.latest": { ko: "최신", en: "Latest" },
    "cu.mom": { ko: "전월 대비", en: "MoM" },
    "cu.yoy": { ko: "1년 증감", en: "1Y change" },
    "cu.maxmin": { ko: "최고 / 최저", en: "High / Low" },

    // 통합 검색
    "gs.noresult": { ko: "검색 결과 없음", en: "No results" },
    "gs.indicators": { ko: "지표 Indicators", en: "Indicators" },
    "gs.stocks": { ko: "종목 Stocks", en: "Stocks" },
    "tag.fx": { ko: "환율", en: "FX" },
    "tag.index": { ko: "지수", en: "Index" },
    "tag.usrate": { ko: "미국 금리", en: "US Rate" },
    "tag.krrate": { ko: "한국 금리", en: "KR Rate" },
    "tag.commodity": { ko: "원자재", en: "Commodity" },
    "tag.stock": { ko: "종목", en: "Stock" },

    // 출처
    "src.naverLive": { ko: "Naver Finance · 실시간", en: "Naver Finance · live" },
    "src.yahooLive": { ko: "Yahoo Finance · 실시간", en: "Yahoo Finance · live" },
    "src.naverIndex": { ko: "Naver Finance · 마켓인덱스", en: "Naver Finance · market index" },

    "stale.title": {
      ko: "마지막 성공한 값 (일시적 갱신 실패)",
      en: "Last good value (refresh temporarily failed)",
    },
    "unit.contract": { ko: "계약", en: "contracts" },
    "unit.krw": { ko: "억원", en: "KRW" },
  };

  // -- 2) 서버가 실어 보내는 라벨 ---------------------------------------
  const DYN = {
    // 환율 지역 · 통화쌍
    "아시아·태평양 APAC": "Asia-Pacific",
    "유럽 Europe": "Europe",
    "북미 North America": "North America",
    "달러/원": "US Dollar / Korean Won",
    "달러/엔": "US Dollar / Japanese Yen",
    "달러/위안": "US Dollar / Offshore Yuan",
    "호주달러/달러": "Australian Dollar / US Dollar",
    "유로/달러": "Euro / US Dollar",
    "파운드/달러": "British Pound / US Dollar",
    "달러/스위스프랑": "US Dollar / Swiss Franc",
    "유로/파운드": "Euro / British Pound",
    "달러/캐나다달러": "US Dollar / Canadian Dollar",
    "달러/페소": "US Dollar / Mexican Peso",
    "DXY 달러지수": "DXY Dollar Index",
    "ICE 달러 인덱스": "ICE US Dollar Index",

    // 지수
    "한국 Korea": "Korea",
    "미국 US": "United States",
    "아시아 Asia": "Asia",
    "코스피": "KOSPI",
    "코스닥": "KOSDAQ",
    "코스피200": "KOSPI 200",
    "나스닥": "NASDAQ",
    "다우산업": "Dow Jones",
    "다우존스": "Dow Jones",
    "니케이225": "Nikkei 225",
    "닛케이 225": "Nikkei 225",
    "항셍 (HK)": "Hang Seng (HK)",
    "항셍": "Hang Seng",
    "상해종합": "Shanghai Composite",
    "유로스톡스 50": "EURO STOXX 50",

    // 미국 금리
    "미 국채 13주": "US 13-Week T-Bill",
    "미 국채 5년": "US 5-Year Treasury",
    "미 국채 10년": "US 10-Year Treasury",
    "미 국채 30년": "US 30-Year Treasury",

    // 원자재
    "금": "Gold",
    "은": "Silver",
    "백금": "Platinum",
    "WTI 원유": "WTI Crude",
    "브렌트유": "Brent Crude",
    "천연가스": "Natural Gas",
    "옥수수": "Corn",
    "대두": "Soybeans",

    // 한국 금리
    "CD금리 (91일)": "CD 91D",
    "콜금리": "Call Rate",
    "국고채 (3년)": "KTB 3Y",
    "회사채 (3년)": "Corporate 3Y (AA−)",
    "COFIX 잔액": "COFIX (Balance)",
    "COFIX 신규취급액": "COFIX (New)",

    // 주식 수급
    "코스피200 선물": "KOSPI 200 Futures",
    "개인": "Retail",
    "외국인": "Foreign",
    "기관계": "Institutions",
    "금융투자": "Securities",
    "보험": "Insurance",
    "투신": "Investment Trusts",
    "은행": "Banks",
    "기타금융": "Other Financial",
    "연기금": "Pension Funds",
    "기타법인": "Other Corporates",
    "기관": "Institutions",
    "계약": "contracts",
    "억원": "KRW",

    // 채권 수급
    "자산운용(공모)": "Asset Mgmt (Public)",
    "자산운용(사모)": "Asset Mgmt (Private)",
    "기금공제": "Funds & Mutual Aid",
    "종금상호": "Merchant / Mutual Savings",
    "국가지자체": "Government & Local",
    "선물": "Futures Cos.",
    "합계": "Total",
    "국채": "KTB",
    "통안증권": "MSB",
    "은행채": "Bank Bonds",
    "기타금융채": "Other Financial Bonds",
    "회사채": "Corporate",
    "특수채": "Special",
    "지방채": "Municipal",

    // 지표종목 최종호가
    "국고채권(1년)": "KTB 1Y",
    "국고채권(3년)": "KTB 3Y",
    "국고채권(5년)": "KTB 5Y",
    "국고채권(10년)": "KTB 10Y",
    "국고채권(30년)": "KTB 30Y",
    "통안증권(91일)": "MSB 91D",
    "통안증권(1년)": "MSB 1Y",
    "통안증권(2년)": "MSB 2Y",
    "회사채(무보증3년)AA-": "Corporate AA− 3Y",
    "회사채(무보증3년)BBB-": "Corporate BBB− 3Y",
    "CD수익률(91일)": "CD 91D",
    "CP(91일)": "CP 91D",

    // 채권 커브
    "국고채권": "KTB",
    "산금채 AAA": "KDB Bond AAA",
    "회사채 AAA": "Corporate AAA",
    "회사채 AA0": "Corporate AA0",
    "회사채 A0": "Corporate A0",
    "회사채 BBB0": "Corporate BBB0",

    // 단기금융시장
    "CP 할인": "CP Discount",
    "CP 매출": "CP Sales",
    "전단채 할인": "STEB Discount",
    "전단채 매출": "STEB Sales",
    "59일 이하": "≤ 59D",
    "60~90일": "60–90D",
    "91~180일": "91–180D",
    "181~270일": "181–270D",
    "271일~1년": "271D–1Y",

    // 외환보유액 유출예정액 — IMF 템플릿 원문으로 되돌린다
    "1개월 이내": "Up to 1 month",
    "1~3개월": "1 to 3 months",
    "3개월~1년": "3 months to 1 year",
    "1. 외화 대출·증권·예치금": "1. Foreign currency loans, securities, and deposits",
    "유출 (−)": "Outflows (−)",
    "유입 (+)": "Inflows (+)",
    "원금": "Principal",
    "이자": "Interest",
    "유출 (−) 원금": "Outflows (−) Principal",
    "유출 (−) 이자": "Outflows (−) Interest",
    "유입 (+) 원금": "Inflows (+) Principal",
    "유입 (+) 이자": "Inflows (+) Interest",
    "2. 선물환·통화선물 순포지션":
      "2. Aggregate short and long positions in forwards and futures",
    "(a) 매도 포지션 (−)": "(a) Short positions (−)",
    "(b) 매수 포지션 (+)": "(b) Long positions (+)",
    "3. 기타": "3. Other",
    "RP 매도 관련 유출 (−)": "Outflows related to repos (−)",
    "역RP 관련 유입 (+)": "Inflows related to reverse repos (+)",
    "무역신용 (−)": "Trade credit (−)",
    "무역신용 (+)": "Trade credit (+)",
    "기타 지급계정 (−)": "Other accounts payable (−)",
    "기타 수취계정 (+)": "Other accounts receivable (+)",

    // 베이시스
    "콘탱고": "Contango",
    "백워데이션": "Backwardation",
    "동일": "Flat",
    "고평": "Overvalued",
    "저평": "Undervalued",
    "적정": "Fair",

    // 스왑 · FX-implied 출처
    "고시일 매매기준율": "Quote-date MAR",
    "실시간": "live",
    "미 재무부 국채 CMT": "US Treasury CMT",
    "뉴욕연준 SOFR 평균": "NY Fed SOFR Averages",
    "수동 입력": "Manual entry",
    "데스크 수동 입력": "Desk manual entry",
    "일수 선형": "Linear (days)",

    // 보관금액
    "SEIBro 국제거래 · 시장별내역": "SEIBro cross-border · by market",

    // 팝업 통계 · 태그
    "매수 Bid": "Bid",
    "매도 Ask": "Ask",
    "스프레드": "Spread",
    "전일 종가": "Prev Close",
    "시가": "Open",
    "고가": "High",
    "저가": "Low",
    "거래대금": "Trading Value",
    "거래량": "Volume",
    "상승 종목": "Advancers",
    "하락 종목": "Decliners",
    "52주 최고": "52W High",
    "52주 최저": "52W Low",
    "만기 (Expiry)": "Expiry",
    "전일 정산": "Prev Settle",
    "미결제약정": "Open Interest",
    "전일 수익률": "Prev Yield",
    "30일 평균": "30D Avg",
    "30일 최고": "30D High",
    "30일 최저": "30D Low",
    "전일 고시": "Prev Fixing",
    "1개월 평균": "1M Avg",
    "1개월 최고": "1M High",
    "1개월 최저": "1M Low",
    "1개월 변화": "1M Change",
    "기준일": "As of",
    "환율": "FX",
    "지수": "Index",
    "KRX 지수": "KRX Index",
    "근월 선물": "Front-Month Future",
    "KR 금리": "KR Rate",
    "국내 시장금리 · 일별 고시": "KR market rate · daily fixing",
    "보합": "Unchanged",
    "당일 분봉 · Intraday": "Intraday",
    "1주 추이 · 1W": "1 week",
    "1개월 추이 · 1M": "1 month",
    "1년 추이 · 1Y": "1 year",
    "일별 고시 · Daily Fixing": "Daily fixing",

    // 기간 라벨 (서버가 수급 표 머리로 내려보낸다)
    "1일": "1D",
    "1주": "1W",
    "1개월": "1M",
    "3개월": "3M",

    // 커브 만기
    "6개월": "6M",
    "9개월": "9M",
    "1년": "1Y",
    "2년": "2Y",
    "3년": "3Y",
    "5년": "5Y",
    "10년": "10Y",
    "20년": "20Y",
    "30년": "30Y",
  };

  // 원문에 숫자가 섞여 통째로는 못 찾는 문구 — 조각을 갈아 끼운다.
  // 순서가 중요하다: 긴 패턴을 먼저 걸어야 짧은 패턴이 먼저 먹어 치우지 않는다.
  const RULES = [
    // 보간 방식 이름은 경고문 안에도 박혀 들어오므로 먼저 갈아 끼운다
    // ("6M IRS 는 고시가 없어 보간값이다 (일수 선형(3M-1Y))." 의 괄호 안).
    [/일수 선형보간/g, "linear-in-days"],
    [/일수 선형/g, "linear-in-days"],
    // 잔존기간 "2년 11개월" · KOFIA 표기 "2년6월" · 커브 만기 "3개월"
    [/(\d+)년\s*(\d+)개월/g, "$1Y $2M"],
    [/(\d+)개월/g, "$1M"],
    [/(\d+)년/g, "$1Y"],
    [/(\d+)월/g, "$1M"],
    [/(\d+)일(?!수)/g, "$1D"],
    // PER · PBR 의 "배"
    [/([\d.,])배/g, "$1x"],
    // 네이버 장 상태 표기 (베이시스 각주)
    [/장마감/g, "market closed"],
    [/장중/g, "market open"],
    [/(\d+)분 지연제공/g, "$1-min delayed"],
    // 팝업 차트 고·저
    [/^고\s/, "H "],
    [/\s·\s저\s/, " · L "],
    // 금리 팝업의 "전일 3.42%"
    [/^전일\s/, "Prev "],
    // 베이시스 각주
    [/조달금리 CD91/g, "Funding rate CD91"],
    [/\(대체값\)/g, " (fallback)"],
    [/배당수익률/g, "dividend yield"],
    [/가정$/g, "assumed"],
    // 스왑 · FX-implied 경고문
    [/USD 텀금리가 없어 (.+?) 의 yield·basis 를 계산하지 못했다\./g,
      "No USD term rate for $1 — yield and basis could not be computed."],
    [/6M IRS 는 고시가 없어 보간값이다 \((.+?)\)\.\s*보간 방식 간 폭 ([\d.]+)bp — 6M basis 는 ±([\d.]+)bp 의 보간 불확실성을 안고 읽을 것\./g,
      "6M IRS is not quoted, so it is interpolated ($1). Spread across methods is $2bp — read the 6M basis with ±$3bp of interpolation uncertainty."],
    [/6M IRS 는 고시가 없어 보간값이다 \((.+?)\)\./g,
      "6M IRS is not quoted, so it is interpolated ($1)."],
    [/\s*9M 호가를 넣으면 이 폭이 크게 줄어든다\./g,
      " Adding a 9M quote would narrow this materially."],
    [/3M\(CD\)-1Y\(IRS\) 구간 기울기가 가팔라 보간 편차가 ([\d.]+)bp 로 벌어졌다 — 6M 은 수치가 아니라 구간으로 볼 것\./g,
      "The 3M(CD)–1Y(IRS) segment is steep, widening interpolation dispersion to $1bp — read 6M as a range, not a number."],
    [/6M basis (.+?) 는 보간 불확실성 안에 있다 — 보간 방식만 바꿔도 부호가 뒤집힌다\. 방향으로 읽지 말 것\./g,
      "The 6M basis of $1 sits inside the interpolation uncertainty — the sign flips with the method alone. Do not read it directionally."],
    [/basis 는 par - par 이다 — FX-implied 커브를 KRW IRS 관습\(분기·ACT\/365\)의 par swap rate 로 다시 읽어 뺐으므로 복리·지급주기 왜곡이 없다\. 단리 zero \(yieldSimple\)는 pricer 대조용 검증열이다\. 다만 USD 텀금리 소스가 basis 를 통째로 밀어 올리거나 내리므로 절대값은 소스와 함께 읽을 것\./g,
      "Basis is par − par: the FX-implied curve is re-read as a par swap rate on KRW IRS conventions (quarterly, ACT/365), so there is no compounding or payment-frequency distortion. The simple zero (yieldSimple) is a check column against the desk pricer. Note that the USD term-rate source shifts the whole basis up or down — read the absolute level together with its source."],
  ];

  // 억(1e8)·조(1e12)는 영어권 자릿수와 어긋난다. 1,234억 은 "1,234 bn" 이
  // 아니라 123.4bn 이므로, 접미사만 갈아 끼우면 열 배가 틀린다 —
  // 원(圓) 금액으로 되돌린 뒤 다시 접는다.
  function fmtWon(won, signed) {
    const a = Math.abs(won);
    const sign = signed ? (won > 0 ? "+" : won < 0 ? "−" : "") : (won < 0 ? "−" : "");
    if (a >= 1e12) return `${sign}${(a / 1e12).toFixed(2)}tn`;
    if (a >= 1e9) return `${sign}${(a / 1e9).toFixed(1)}bn`;
    if (a >= 1e6) {
      // 백만 자리는 "1.5백만" 처럼 소수가 뜻을 갖는 구간이 있다 — 100 이
      // 넘어가면 그 소수가 유효자리 넷째라 의미가 없어 접는다.
      const m = a / 1e6;
      const shown = m >= 100 ? Math.round(m) : Math.round(m * 10) / 10;
      return `${sign}${shown.toLocaleString("en-US")}mn`;
    }
    return `${sign}${Math.round(a).toLocaleString("en-US")}`;
  }

  // "1.23조" · "+456억" · "4.00억 CNY" · "1.5백만 USD" 처럼 숫자에 한국식
  // 자릿수 단위가 붙은 문자열을 영어 표기로 되접는다. 통화 꼬리표는 그대로 둔다.
  //
  // 네이버는 "1조 2,345억원" 처럼 두 자릿수 단위를 이어 붙여 내려보낸다.
  // 조각마다 따로 접으면 "1tn 234.5bn" 이 되어 한 금액이 둘로 읽히므로,
  // 이어진 덩어리를 통째로 잡아 더한 뒤 한 번만 접는다. 꼬리의 '원'은
  // 단위가 이미 접미사에 들어가 있어 지운다.
  const UNIT_SCALE = { "조": 1e12, "억": 1e8, "백만": 1e6, "만": 1e4 };
  const AMOUNT_RE =
    /([+\-−]?[\d,]+(?:\.\d+)?\s*(?:조|억|백만|만)\s*)+원?/g;
  const PART_RE = /([+\-−]?[\d,]+(?:\.\d+)?)\s*(조|억|백만|만)/g;

  function trAmount(s) {
    return String(s ?? "").replace(AMOUNT_RE, (chunk) => {
      let total = 0;
      let signed = false;
      let neg = false;
      let ok = false;
      for (const [, num, unit] of chunk.matchAll(PART_RE)) {
        const v = parseFloat(num.replace(/[,+\-−]/g, ""));
        if (!isFinite(v)) return chunk;
        // 부호는 맨 앞 조각에만 붙는다 ("-1조 2,345억" 은 전체가 음수다).
        if (!ok) {
          signed = /^[+\-−]/.test(num);
          neg = /^[-−]/.test(num);
        }
        total += v * UNIT_SCALE[unit];
        ok = true;
      }
      if (!ok) return chunk;
      // "4.00억 CNY" 의 덩어리는 단위 뒤 공백까지 물고 온다 — 지우면 통화
      // 꼬리표가 숫자에 붙어 "400mnCNY" 가 된다.
      return fmtWon(neg ? -total : total, signed) + (/\s$/.test(chunk) ? " " : "");
    });
  }

  window.FXI18N = {
    STR,
    DYN,
    RULES,
    fmtWon,
    trAmount,
  };
})();
