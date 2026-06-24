# -*- coding: utf-8 -*-
"""
market_bot_gha.py — GitHub Actions 투자봇 (5분마다 실행)
기능: 종목 신호 감지 → 텔레그램 알림 + 텔레그램 질문 응답 + 속보 뉴스
의존성: yfinance pandas numpy requests (pandas-ta 불필요)
"""
import os, json, sys, requests, re
from datetime import datetime, timezone, timedelta
import xml.etree.ElementTree as ET
import yfinance as yf
import pandas as pd
import numpy as np

# ── 설정 ─────────────────────────────────────────────────────
TG_TOKEN   = os.environ.get("TG_BOT_TOKEN", "")
TG_CHAT    = os.environ.get("TG_CHAT_ID", "")
AVG_COST   = float(os.environ.get("KORU_AVG_COST", "773.96").strip('﻿').strip())
STATE_FILE = "market_state.json"

# 관심 종목 (yfinance 티커: 한국주식은 .KS 접미사)
WATCHLIST = {
    # 국내
    "005930.KS": "삼성전자",
    "000660.KS": "SK하이닉스",
    "267260.KS": "현대일렉트릭",
    "011070.KS": "LG이노텍",
    "042700.KS": "한미반도체",
    "036930.KS": "주성엔지니어링",
    "066570.KS": "LG전자",
    "012330.KS": "현대모비스",
    "307950.KS": "현대오토에버",
    "009150.KS": "삼성전기",
    "034730.KS": "SK",
    # 해외
    "SOXL":      "SOXL(반도체3X)",
    "KORU":      "KORU(한국3X)",
    "MU":        "마이크론",
    "NVDA":      "엔비디아",
    "AMD":       "AMD",
}

# 이름→티커 역매핑 (텔레그램 질문 파싱용)
NAME_TO_TICKER = {v.upper(): k for k, v in WATCHLIST.items()}
NAME_TO_TICKER.update({
    "삼성전자": "005930.KS", "SK하이닉스": "000660.KS", "하이닉스": "000660.KS",
    "현대일렉트릭": "267260.KS", "LG이노텍": "011070.KS", "이노텍": "011070.KS",
    "한미반도체": "042700.KS", "주성엔지니어링": "036930.KS", "주성": "036930.KS",
    "LG전자": "066570.KS", "현대모비스": "012330.KS", "모비스": "012330.KS",
    "현대오토에버": "307950.KS", "오토에버": "307950.KS", "삼성전기": "009150.KS",
    "마이크론": "MU", "엔비디아": "NVDA", "코루": "KORU", "솩슬": "SOXL",
    "KORU": "KORU", "SOXL": "SOXL", "MU": "MU", "NVDA": "NVDA", "AMD": "AMD",
})

# 뉴스 RSS 피드
NEWS_FEEDS = [
    ("https://news.google.com/rss/search?q=반도체+주가&hl=ko&gl=KR&ceid=KR:ko", "국내반도체"),
    ("https://news.google.com/rss/search?q=코스피+증시&hl=ko&gl=KR&ceid=KR:ko", "코스피"),
    ("https://news.google.com/rss/search?q=SOXL+KORU+semiconductor&hl=en&gl=US&ceid=US:en", "해외반도체"),
    ("https://news.google.com/rss/search?q=Micron+earnings+semiconductor&hl=en&gl=US&ceid=US:en", "마이크론"),
]

# ── 상태 관리 ─────────────────────────────────────────────────
def load_state() -> dict:
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {"last_update_id": 0, "sent_alerts": {}, "seen_news": [], "last_scores": {}}

def save_state(state: dict):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)

# ── 텔레그램 ─────────────────────────────────────────────────
def tg_send(msg: str, chat_id: str = None):
    if not TG_TOKEN:
        print(f"[TG] {msg[:80]}", flush=True)
        return
    cid = chat_id or TG_CHAT
    try:
        requests.post(
            f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
            json={"chat_id": cid, "text": msg, "parse_mode": "Markdown"},
            timeout=10,
        )
    except Exception as e:
        print(f"TG 오류: {e}", flush=True)

def tg_get_updates(offset: int) -> list:
    if not TG_TOKEN:
        return []
    try:
        r = requests.get(
            f"https://api.telegram.org/bot{TG_TOKEN}/getUpdates",
            params={"offset": offset, "timeout": 3, "limit": 10},
            timeout=10,
        )
        if r.ok:
            return r.json().get("result", [])
    except Exception:
        pass
    return []

# ── 순수 pandas 지표 계산 ─────────────────────────────────────
def _rsi(c: pd.Series, n=14) -> pd.Series:
    d = c.diff()
    g = d.clip(lower=0).ewm(alpha=1/n, adjust=False).mean()
    l = (-d.clip(upper=0)).ewm(alpha=1/n, adjust=False).mean()
    return 100 - 100 / (1 + g / l.replace(0, np.nan))

def _macd_hist(c: pd.Series) -> pd.Series:
    m = c.ewm(span=12, adjust=False).mean() - c.ewm(span=26, adjust=False).mean()
    return m - m.ewm(span=9, adjust=False).mean()

def _bb(c: pd.Series, n=20):
    m = c.rolling(n).mean(); s = c.rolling(n).std()
    return m - 2*s, m + 2*s

def _adx(h, l, c, n=14) -> pd.Series:
    tr = pd.concat([h-l, (h-c.shift()).abs(), (l-c.shift()).abs()], axis=1).max(axis=1)
    dp = (h-h.shift()).clip(lower=0); dm = (l.shift()-l).clip(lower=0)
    dp = dp.where(dp > dm, 0); dm = dm.where(dm > dp, 0)
    atr = tr.ewm(alpha=1/n, adjust=False).mean()
    di_p = 100*dp.ewm(alpha=1/n, adjust=False).mean()/atr.replace(0, np.nan)
    di_m = 100*dm.ewm(alpha=1/n, adjust=False).mean()/atr.replace(0, np.nan)
    dx = 100*(di_p-di_m).abs()/(di_p+di_m).replace(0, np.nan)
    return dx.ewm(alpha=1/n, adjust=False).mean()

def analyze_ticker(ticker: str, name: str) -> dict:
    """종목 분석 → {score, action, signals, price, change_pct, rsi, adx}"""
    try:
        df = yf.download(ticker, period="6mo", progress=False, auto_adjust=True, timeout=15)
        if df.empty or len(df) < 30:
            return {}
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        c, h, l = df["Close"], df["High"], df["Low"]

        rsi_s   = _rsi(c)
        macdh_s = _macd_hist(c)
        bbl_s, bbu_s = _bb(c)
        adx_s   = _adx(h, l, c)
        sma5    = c.rolling(5).mean()
        sma20   = c.rolling(20).mean()
        sma60   = c.rolling(60).mean()

        rsi   = float(rsi_s.iloc[-1])
        mh    = float(macdh_s.iloc[-1]); pmh = float(macdh_s.iloc[-2])
        bbl   = float(bbl_s.iloc[-1]);   bbu = float(bbu_s.iloc[-1])
        adx   = float(adx_s.iloc[-1]) if not np.isnan(adx_s.iloc[-1]) else 0
        s5    = float(sma5.iloc[-1]); s20 = float(sma20.iloc[-1]); s60 = float(sma60.iloc[-1])
        ps5   = float(sma5.iloc[-2]); ps20 = float(sma20.iloc[-2])
        price = float(c.iloc[-1])
        prev  = float(c.iloc[-2])
        chg   = (price - prev) / prev * 100

        mult  = 1.2 if adx >= 30 else (1.0 if adx >= 20 else 0.7)
        score = 0
        sigs  = []

        if not np.isnan(rsi):
            if rsi <= 30:   score += int(25*mult); sigs.append(("BUY",  f"RSI 과매도 {rsi:.0f}"))
            elif rsi >= 70: score -= int(25*mult); sigs.append(("SELL", f"RSI 과매수 {rsi:.0f}"))

        if mh > 0 and pmh <= 0:   score += int(20*mult); sigs.append(("BUY",  "MACD 상향반전"))
        elif mh < 0 and pmh >= 0: score -= int(20*mult); sigs.append(("SELL", "MACD 하향반전"))
        elif mh > 0: score += int(10*mult)
        else:        score -= int(10*mult)

        if price <= bbl * 1.005:   score += 15; sigs.append(("BUY",  "볼린저 하단"))
        elif price >= bbu * 0.995: score -= 15; sigs.append(("SELL", "볼린저 상단"))

        if s5 > s20 > s60:   score += int(20*mult)
        elif s5 < s20 < s60: score -= int(20*mult)
        if ps5 <= ps20 and s5 > s20: sigs.append(("BUY",  "골든크로스"))
        elif ps5 >= ps20 and s5 < s20: sigs.append(("SELL", "데드크로스"))

        score = max(-100, min(100, score))
        action = "BUY" if score >= 30 else ("SELL" if score <= -30 else "HOLD")

        return {
            "ticker": ticker, "name": name,
            "price": price, "change_pct": round(chg, 2),
            "score": score, "action": action,
            "signals": sigs, "adx": round(adx, 1),
            "rsi": round(rsi, 1) if not np.isnan(rsi) else 0,
            "bbl": round(bbl, 2), "bbu": round(bbu, 2),
            "sma5": round(s5, 1), "sma20": round(s20, 1),
        }
    except Exception as e:
        print(f"  [{name}] 분석 오류: {e}", flush=True)
        return {}

def fmt_analysis(r: dict, avg_cost: float = None) -> str:
    """분석 결과 → 텔레그램 메시지 포맷"""
    is_usd = not r["ticker"].endswith(".KS")
    pfx = "$" if is_usd else ""
    sfx = "" if is_usd else "원"
    price_str = f"{pfx}{r['price']:.2f}{sfx}" if is_usd else f"{int(r['price']):,}원"

    action_icon = {"BUY": "📈", "SELL": "📉", "HOLD": "➖"}.get(r["action"], "➖")
    lines = [
        f"{action_icon} *{r['name']}* [{r['action']}]",
        f"  현재가: {price_str} ({r['change_pct']:+.2f}%)",
        f"  종합점수: {r['score']} | RSI: {r['rsi']} | ADX: {r['adx']}",
    ]
    if avg_cost and r["ticker"] == "KORU":
        pnl = (r["price"] - avg_cost) / avg_cost * 100
        lines.append(f"  평단대비: {pnl:+.2f}% (평단 ${avg_cost:.2f})")
    if r["signals"]:
        for s_type, s_txt in r["signals"][:3]:
            lines.append(f"  {'↑' if s_type=='BUY' else '↓'} {s_txt}")
    return "\n".join(lines)

# ── 뉴스 수집 ─────────────────────────────────────────────────
def fetch_news(max_age_hours: int = 1) -> list:
    """RSS 피드에서 최신 뉴스 수집"""
    items = []
    cutoff = datetime.now(timezone.utc) - timedelta(hours=max_age_hours)
    for url, category in NEWS_FEEDS:
        try:
            r = requests.get(url, timeout=8, headers={"User-Agent": "Mozilla/5.0"})
            if not r.ok:
                continue
            root = ET.fromstring(r.content)
            for item in root.findall(".//item")[:5]:
                title = (item.findtext("title") or "").strip()
                pub   = item.findtext("pubDate") or ""
                if not title:
                    continue
                # 발행 시간 파싱
                try:
                    from email.utils import parsedate_to_datetime
                    pub_dt = parsedate_to_datetime(pub).astimezone(timezone.utc)
                    if pub_dt < cutoff:
                        continue
                except Exception:
                    pass
                items.append({"title": title, "category": category})
        except Exception:
            pass
    return items

# ── 신호 알림 ─────────────────────────────────────────────────
def run_signal_check(state: dict) -> dict:
    """전 종목 신호 체크 → 강한 신호만 텔레그램 발송"""
    sent_alerts = state.get("sent_alerts", {})
    now_kst = (datetime.now(timezone.utc) + timedelta(hours=9)).strftime("%H:%M")
    alerts_sent = 0

    for ticker, name in WATCHLIST.items():
        print(f"  [{name}] 분석중...", flush=True)
        r = analyze_ticker(ticker, name)
        if not r:
            continue

        score  = r["score"]
        action = r["action"]

        # 신호 강도 기준: 점수 ±40 이상 + ADX 18 이상
        if abs(score) < 40 or r["adx"] < 18:
            sent_alerts.pop(ticker, None)   # 약해지면 쿨다운 초기화
            continue

        # 같은 방향 신호가 이미 발송됐으면 스킵 (쿨다운 30분)
        prev = sent_alerts.get(ticker, {})
        if prev.get("action") == action:
            last_time = prev.get("time", "")
            if last_time:
                try:
                    last_dt = datetime.fromisoformat(last_time)
                    if (datetime.now(timezone.utc) - last_dt).seconds < 1800:
                        continue
                except Exception:
                    pass

        avg = AVG_COST if ticker == "KORU" else None
        msg = (
            f"⚡ *[{now_kst} KST] 신호 발생!*\n"
            f"{fmt_analysis(r, avg)}"
        )
        tg_send(msg)
        sent_alerts[ticker] = {"action": action, "time": datetime.now(timezone.utc).isoformat()}
        alerts_sent += 1
        print(f"  [{name}] 알림 발송: {action} ({score}점)", flush=True)

    state["sent_alerts"] = sent_alerts
    return state

# ── 뉴스 속보 체크 ────────────────────────────────────────────
def run_news_check(state: dict) -> dict:
    seen = set(state.get("seen_news", []))
    news = fetch_news(max_age_hours=1)
    new_items = [n for n in news if n["title"] not in seen]

    if new_items:
        now_kst = (datetime.now(timezone.utc) + timedelta(hours=9)).strftime("%H:%M")
        lines = [f"📰 *[{now_kst} KST] 속보*"]
        for n in new_items[:5]:
            lines.append(f"  [{n['category']}] {n['title'][:60]}")
        tg_send("\n".join(lines))
        seen.update(n["title"] for n in new_items)
        # seen_news는 최대 200개 유지
        state["seen_news"] = list(seen)[-200:]
        print(f"  속보 {len(new_items)}건 발송", flush=True)
    else:
        print("  새 뉴스 없음", flush=True)

    return state

# ── 텔레그램 봇 (질문 응답) ───────────────────────────────────
def parse_ticker_from_text(text: str) -> str | None:
    """메시지에서 종목명/티커 추출"""
    text_up = text.upper().strip()
    # 직접 티커 매칭
    for key, ticker in NAME_TO_TICKER.items():
        if key.upper() in text_up:
            return ticker
    # 6자리 숫자 → KRX 코드
    m = re.search(r'\b(\d{6})\b', text)
    if m:
        return m.group(1) + ".KS"
    return None

def handle_telegram_commands(state: dict) -> dict:
    """텔레그램 메시지 읽고 응답"""
    offset = state.get("last_update_id", 0) + 1
    updates = tg_get_updates(offset)
    if not updates:
        return state

    now_kst = (datetime.now(timezone.utc) + timedelta(hours=9)).strftime("%H:%M")

    for upd in updates:
        state["last_update_id"] = upd["update_id"]
        msg = upd.get("message", {})
        text = msg.get("text", "").strip()
        chat_id = str(msg.get("chat", {}).get("id", TG_CHAT))

        if not text:
            continue

        print(f"  TG 수신: {text[:50]}", flush=True)
        text_up = text.upper()

        # 전체 시황
        if any(k in text_up for k in ["시황", "전체", "포트폴리오", "모두", "스캔"]):
            tg_send(f"⏳ 전체 종목 분석 중... (약 30초)", chat_id)
            lines = [f"📊 *[{now_kst} KST] 전체 시황*\n"]
            for ticker, name in WATCHLIST.items():
                r = analyze_ticker(ticker, name)
                if not r:
                    continue
                icon = {"BUY": "📈", "SELL": "📉", "HOLD": "➖"}.get(r["action"], "➖")
                chg_str = f"{r['change_pct']:+.2f}%"
                lines.append(f"{icon} {name}: {r['score']}점 ({chg_str})")
            tg_send("\n".join(lines), chat_id)

        # 뉴스 요청
        elif any(k in text_up for k in ["뉴스", "속보", "NEWS"]):
            news = fetch_news(max_age_hours=3)
            if news:
                lines = [f"📰 *[{now_kst} KST] 최신 뉴스*"]
                for n in news[:8]:
                    lines.append(f"  [{n['category']}] {n['title'][:60]}")
                tg_send("\n".join(lines), chat_id)
            else:
                tg_send("최근 3시간 내 새 뉴스가 없습니다.", chat_id)

        # KORU 특별 분석 (평단 포함)
        elif "KORU" in text_up or "코루" in text_up:
            tg_send("⏳ KORU 분석 중...", chat_id)
            r = analyze_ticker("KORU", "KORU(한국3X)")
            if r:
                pnl = (r["price"] - AVG_COST) / AVG_COST * 100
                msg = (
                    f"📊 *[{now_kst} KST] KORU 분석*\n"
                    f"{fmt_analysis(r, AVG_COST)}\n"
                    f"\n  📌 추가매수: ${AVG_COST*0.93:.2f} | 손절: ${AVG_COST*0.88:.2f}"
                    f"\n  📌 1차익절: ${AVG_COST*1.10:.2f} | 2차: ${AVG_COST*1.20:.2f}"
                )
                tg_send(msg, chat_id)

        # 특정 종목 분석
        else:
            ticker = parse_ticker_from_text(text)
            if ticker:
                name = WATCHLIST.get(ticker, ticker)
                tg_send(f"⏳ {name} 분석 중...", chat_id)
                r = analyze_ticker(ticker, name)
                if r:
                    tg_send(f"📊 *[{now_kst} KST]*\n{fmt_analysis(r)}", chat_id)
                else:
                    tg_send(f"{name} 데이터를 가져올 수 없습니다.", chat_id)
            else:
                tg_send(
                    "📌 *사용법*\n"
                    "  종목명/티커 입력 → 즉시 분석\n"
                    "  예: `KORU`, `SK하이닉스`, `MU`, `NVDA`\n"
                    "  `시황` → 전체 포트폴리오 스캔\n"
                    "  `뉴스` → 최신 속보",
                    chat_id,
                )

    return state

# ── 메인 ─────────────────────────────────────────────────────
def main():
    now_utc = datetime.now(timezone.utc)
    now_kst = now_utc + timedelta(hours=9)
    print(f"[{now_kst.strftime('%H:%M')} KST] 마켓봇 실행", flush=True)

    state = load_state()

    # 1) 텔레그램 명령 처리 (항상)
    print("▷ 텔레그램 명령 처리...", flush=True)
    state = handle_telegram_commands(state)

    # 2) 종목 신호 체크
    # 한국장: 09:00~15:30 KST (00:00~06:30 UTC)
    # 미국장: 09:30~16:00 ET = 14:30~21:00 UTC (서머타임 기준)
    kst_h = now_kst.hour
    utc_h = now_utc.hour
    in_kr_market = (0 <= utc_h < 7)    # KST 09:00~16:00
    in_us_market = (13 <= utc_h < 21)   # US 09:30~16:00 ET

    if in_kr_market or in_us_market:
        print("▷ 장중 신호 체크...", flush=True)
        state = run_signal_check(state)
    else:
        # 장외: 해외 ETF/주식만 체크 (24h 거래 가능)
        print("▷ 장외 핵심 종목 체크 (KORU/SOXL/MU/NVDA)...", flush=True)
        after_hours = {"KORU": "KORU(한국3X)", "SOXL": "SOXL(반도체3X)", "MU": "마이크론", "NVDA": "엔비디아"}
        sent_alerts = state.get("sent_alerts", {})
        now_kst_str = now_kst.strftime("%H:%M")
        for ticker, name in after_hours.items():
            r = analyze_ticker(ticker, name)
            if not r or abs(r["score"]) < 50 or r["adx"] < 20:
                sent_alerts.pop(ticker, None)
                continue
            prev = sent_alerts.get(ticker, {})
            if prev.get("action") == r["action"]:
                last_time = prev.get("time", "")
                if last_time:
                    try:
                        last_dt = datetime.fromisoformat(last_time)
                        if (datetime.now(timezone.utc) - last_dt).seconds < 3600:
                            continue
                    except Exception:
                        pass
            avg = AVG_COST if ticker == "KORU" else None
            tg_send(f"⚡ *[{now_kst_str} KST] 장외 신호*\n{fmt_analysis(r, avg)}")
            sent_alerts[ticker] = {"action": r["action"], "time": datetime.now(timezone.utc).isoformat()}
        state["sent_alerts"] = sent_alerts

    # 3) 뉴스 속보 (매 실행마다)
    print("▷ 속보 뉴스 체크...", flush=True)
    state = run_news_check(state)

    save_state(state)
    print("완료", flush=True)

if __name__ == "__main__":
    main()
