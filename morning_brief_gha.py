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

def get_news(query: str, hours=12, max_n=4) -> list:
    url = f"https://news.google.com/rss/search?q={requests.utils.quote(query)}&hl=ko&gl=KR&ceid=KR:ko"
    try:
        r = requests.get(url, timeout=8, headers={"User-Agent":"Mozilla/5.0"})
        if not r.ok: return []
        root = ET.fromstring(r.content)
        titles = []
        for item in root.findall(".//item")[:max_n]:
            t = (item.findtext("title") or "").strip()
            if t: titles.append(t)
        return titles
    except Exception:
        return []

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

    # ② 뉴스 요약
    lines.append("\n② 간밤 뉴스")
    us_news = get_news("semiconductor stocks overnight futures", hours=12)
    kr_news = get_news("반도체 증시 오늘 전망", hours=12)
    all_news = (us_news[:2] + kr_news[:3])
    for n in all_news[:4]:
        lines.append(f"  📰 {n[:65]}")

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
