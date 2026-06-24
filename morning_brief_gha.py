# -*- coding: utf-8 -*-
"""
morning_brief_gha.py — 매일 07:50 KST 아침 브리핑
간밤 미국 시황 + 전 종목 점수 + 뉴스 + 오늘 일정
"""
import os, requests, sys, json
from datetime import datetime, timezone, timedelta
import xml.etree.ElementTree as ET
sys.stdout.reconfigure(encoding='utf-8')
import yfinance as yf
import pandas as pd
import numpy as np

TG_TOKEN  = os.environ.get("TG_BOT_TOKEN", "")
TG_CHAT   = os.environ.get("TG_CHAT_ID", "")
AVG_COST  = float(os.environ.get("KORU_AVG_COST", "773.96").strip('﻿').strip())

WATCHLIST = {
    "005930.KS": "삼성전자",   "000660.KS": "SK하이닉스",
    "267260.KS": "현대일렉트릭","011070.KS": "LG이노텍",
    "042700.KS": "한미반도체", "036930.KS": "주성엔지니어링",
    "066570.KS": "LG전자",     "012330.KS": "현대모비스",
    "307950.KS": "현대오토에버","009150.KS": "삼성전기",
    "034730.KS": "SK",
    "SOXL": "SOXL(반도체3X)",  "KORU": "KORU(한국3X)",
    "MU": "마이크론",           "NVDA": "엔비디아",
}

def tg(msg: str):
    if not TG_TOKEN:
        print(msg, flush=True); return
    # 텔레그램 최대 4096자 제한 → 초과 시 분할
    chunks = [msg[i:i+4000] for i in range(0, len(msg), 4000)]
    for chunk in chunks:
        try:
            payload = json.dumps(
                {"chat_id": TG_CHAT, "text": chunk, "parse_mode": "Markdown"},
                ensure_ascii=False
            ).encode("utf-8")
            r = requests.post(
                f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
                data=payload,
                headers={"Content-Type": "application/json; charset=utf-8"},
                timeout=10,
            )
            if not r.ok:
                print(f"TG 오류: {r.status_code} {r.text[:100]}", flush=True)
        except Exception as e:
            print(f"TG 예외: {e}", flush=True)

def _rsi(c, n=14):
    d = c.diff(); g = d.clip(lower=0).ewm(alpha=1/n, adjust=False).mean()
    l = (-d.clip(upper=0)).ewm(alpha=1/n, adjust=False).mean()
    return 100 - 100/(1 + g/l.replace(0, np.nan))

def _macd_hist(c):
    m = c.ewm(span=12,adjust=False).mean()-c.ewm(span=26,adjust=False).mean()
    return m - m.ewm(span=9,adjust=False).mean()

def _adx(h, l, c, n=14):
    tr = pd.concat([h-l,(h-c.shift()).abs(),(l-c.shift()).abs()],axis=1).max(axis=1)
    dp=(h-h.shift()).clip(lower=0); dm=(l.shift()-l).clip(lower=0)
    dp=dp.where(dp>dm,0); dm=dm.where(dm>dp,0)
    atr=tr.ewm(alpha=1/n,adjust=False).mean()
    dip=100*dp.ewm(alpha=1/n,adjust=False).mean()/atr.replace(0,np.nan)
    dim=100*dm.ewm(alpha=1/n,adjust=False).mean()/atr.replace(0,np.nan)
    dx=100*(dip-dim).abs()/(dip+dim).replace(0,np.nan)
    return dx.ewm(alpha=1/n,adjust=False).mean()

def analyze(ticker: str) -> dict:
    try:
        df = yf.download(ticker, period="6mo", progress=False, auto_adjust=True, timeout=15)
        if df.empty or len(df) < 30: return {}
        if isinstance(df.columns, pd.MultiIndex): df.columns = df.columns.get_level_values(0)
        c, h, l = df["Close"], df["High"], df["Low"]
        rsi  = float(_rsi(c).iloc[-1])
        mh   = float(_macd_hist(c).iloc[-1]); pmh = float(_macd_hist(c).iloc[-2])
        adx  = float(_adx(h,l,c).iloc[-1])
        s5   = float(c.rolling(5).mean().iloc[-1])
        s20  = float(c.rolling(20).mean().iloc[-1])
        s60  = float(c.rolling(60).mean().iloc[-1])
        price= float(c.iloc[-1]); prev = float(c.iloc[-2])
        if np.isnan(price) or np.isnan(prev) or prev == 0: return {}
        chg  = (price-prev)/prev*100

        mult = 1.2 if adx>=30 else (1.0 if adx>=20 else 0.7)
        score= 0
        if not np.isnan(rsi):
            if rsi<=30:   score+=int(25*mult)
            elif rsi>=70: score-=int(25*mult)
        if mh>0 and pmh<=0:    score+=int(20*mult)
        elif mh<0 and pmh>=0:  score-=int(20*mult)
        elif mh>0: score+=int(10*mult)
        else:      score-=int(10*mult)
        if s5>s20>s60:   score+=int(20*mult)
        elif s5<s20<s60: score-=int(20*mult)
        score = max(-100, min(100, score))

        return {"price": price, "chg": round(chg,2), "score": score,
                "action": "BUY" if score>=30 else ("SELL" if score<=-30 else "HOLD"),
                "rsi": round(rsi,1) if not np.isnan(rsi) else 0, "adx": round(adx,1)}
    except Exception as e:
        print(f"  [{ticker}] 오류: {e}", flush=True); return {}

# 중요도 판단 키워드
IMPORTANT_KW = [
    "급등","급락","폭등","폭락","급변","급반등","급반락",
    "실적","어닝","서프라이즈","쇼크","어닝쇼크","깜짝실적",
    "금리","연준","Fed","FOMC","기준금리","인상","인하",
    "신고가","52주","역대최고","사상최고","신저가",
    "호재","악재","수급","외국인","기관","대규모",
    "AI","인공지능","반도체","HBM","엔비디아","마이크론",
    "삼성전자","SK하이닉스","한미반도체","현대일렉트릭",
    "수출","환율","달러","무역","관세","제재",
    "합병","인수","분할","상장","상폐","감산","증산",
    "주의","경고","위기","붕괴","충격","공포",
]

def score_news(title: str) -> int:
    """제목 중요도 점수 (키워드 매칭 수)"""
    t = title.upper()
    return sum(1 for kw in IMPORTANT_KW if kw.upper() in t)

def fetch_important_news(hours=14, top_n=5) -> list:
    """한국어 뉴스만, 중요도 순 정렬해서 반환"""
    queries = [
        "미국 증시 간밤 시황 반도체",
        "코스피 코스닥 증시 오늘 전망",
        "반도체 주가 뉴스 SK하이닉스 삼성전자",
        "미국 나스닥 다우 S&P 시황",
        "환율 달러 원 금리 연준",
        "SOXL KORU 미국 ETF",
    ]
    seen, candidates = set(), []
    for q in queries:
        url = f"https://news.google.com/rss/search?q={requests.utils.quote(q)}&hl=ko&gl=KR&ceid=KR:ko"
        try:
            r = requests.get(url, timeout=8, headers={"User-Agent": "Mozilla/5.0"})
            if not r.ok: continue
            root = ET.fromstring(r.content)
            for item in root.findall(".//item")[:6]:
                title = (item.findtext("title") or "").strip()
                # 영어 제목 필터링 (한글 포함 여부로 판단)
                if not title or title in seen: continue
                korean_ratio = sum(1 for c in title if '가' <= c <= '힣') / max(len(title), 1)
                if korean_ratio < 0.2: continue   # 한글 20% 미만 제목 제외
                seen.add(title)
                candidates.append((score_news(title), title))
        except Exception:
            continue

    # 중요도 높은 순 정렬, 중요도 0인 뉴스는 최대 2개만 포함
    candidates.sort(key=lambda x: -x[0])
    result = []
    zero_count = 0
    for score, title in candidates:
        if score == 0:
            if zero_count >= 2: continue
            zero_count += 1
        result.append((score, title))
        if len(result) >= top_n:
            break
    return result

def main():
    now_kst = (datetime.now(timezone.utc) + timedelta(hours=9))
    dt_str  = now_kst.strftime("%m/%d(%a)")
    print(f"[{now_kst.strftime('%H:%M')} KST] 아침 브리핑 시작", flush=True)

    lines = [f"🌅 *[아침 브리핑] {dt_str} 07:50*"]

    # ① 간밤 미국 ETF
    lines.append("\n① 간밤 미국 시황")
    for tk, nm in [("SOXL","SOXL"), ("KORU","KORU"), ("NVDA","엔비디아"), ("MU","마이크론")]:
        r = analyze(tk)
        if not r: continue
        icon = "🟢" if r["chg"] > 0 else "🔴"
        pnl_str = ""
        if tk == "KORU":
            pnl = (r["price"] - AVG_COST) / AVG_COST * 100
            pnl_str = f" | 평단대비 {pnl:+.1f}%"
        lines.append(f"  {icon} {nm}: ${r['price']:.2f} ({r['chg']:+.2f}%){pnl_str}")

    # ② 뉴스 요약 (한국어, 중요도 순)
    lines.append("\n② 주요 뉴스 (중요도 순)")
    news_items = fetch_important_news(hours=14, top_n=5)
    if news_items:
        for imp_score, title in news_items:
            star = "🔥" if imp_score >= 3 else ("⚡" if imp_score >= 1 else "📰")
            lines.append(f"  {star} {title[:70]}")
    else:
        lines.append("  📰 최근 주요 뉴스 없음")

    # ③ 전 종목 점수 스캔
    lines.append("\n③ 종목 스캔  [점수 기준: +40이상=강매수 / +30=매수 / -30=매도 / -40이하=강매도 / 나머지=관망]")
    results = []
    for ticker, name in WATCHLIST.items():
        print(f"  [{name}]...", flush=True)
        r = analyze(ticker)
        if r:
            results.append((name, ticker, r))

    results.sort(key=lambda x: -abs(x[2]["score"]))
    for name, ticker, r in results:
        icon = {"BUY":"📈","SELL":"📉","HOLD":"➖"}.get(r["action"],"➖")
        is_usd = not ticker.endswith(".KS")
        price_str = f"${r['price']:.2f}" if is_usd else f"{int(r['price']):,}원"
        score_str = f"+{r['score']}" if r["score"] >= 0 else str(r["score"])
        lines.append(f"  {icon} {name}: {score_str}점  {price_str} ({r['chg']:+.2f}%)")

    # ④ 강한 신호 종목 상세
    strong = [(nm, tk, r) for nm, tk, r in results if abs(r["score"]) >= 40]
    if strong:
        lines.append(f"\n④ 오늘 주목 종목 ({len(strong)}개)")
        for name, ticker, r in strong[:4]:
            icon = {"BUY":"📈","SELL":"📉","HOLD":"➖"}.get(r["action"],"➖")
            lines.append(f"\n  {icon} *{name}* [{r['action']}]  점수:{r['score']}  RSI:{r['rsi']}  ADX:{r['adx']}")

    lines.append("\n📌 09:00 한국장 시작 | 22:30 미국장 시작")
    lines.append("💬 종목명 보내면 즉시 분석  |  '시황' 보내면 전체 스캔")

    tg("\n".join(lines))
    print("아침 브리핑 완료", flush=True)

if __name__ == "__main__":
    main()
