# -*- coding: utf-8 -*-
"""
KORU 모니터 — GitHub Actions 전용
환경변수에서 설정 읽음 / playwright 없이 yfinance + KIS API 사용
상태 파일(state.json)을 artifact로 관리해 중복 알림 방지
"""
import os, json, requests, sys
from datetime import datetime, timezone

import yfinance as yf
import pandas as pd
import pandas_ta as ta
import numpy as np

# ── 설정 (환경변수 우선, 없으면 기본값) ──────────────────────
AVG_COST    = float(os.environ.get("KORU_AVG_COST", "773.96"))
ADD_BUY_LV  = AVG_COST * 0.93
STOP_LOSS   = AVG_COST * 0.88
PROFIT_1    = AVG_COST * 1.10
PROFIT_2    = AVG_COST * 1.20

TG_TOKEN    = os.environ.get("TG_BOT_TOKEN", "")
TG_CHAT     = os.environ.get("TG_CHAT_ID", "")
KIS_KEY     = os.environ.get("KIS_APP_KEY", "")
KIS_SECRET  = os.environ.get("KIS_APP_SECRET", "")
KIS_ACCT    = os.environ.get("KIS_ACCOUNT_NO", "")
KIS_MOCK    = os.environ.get("KIS_MOCK", "true").lower() == "true"

STATE_FILE  = "koru_state.json"   # GitHub Actions artifact로 관리

# ── 상태 파일 로드/저장 ────────────────────────────────────────
def load_state() -> dict:
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {"last_alerts": [], "last_score": 0}

def save_state(state: dict):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)

# ── 텔레그램 ─────────────────────────────────────────────────
def tg(msg: str):
    if not TG_TOKEN or not TG_CHAT:
        print(f"[TG 미설정] {msg}", flush=True)
        return
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
            json={"chat_id": TG_CHAT, "text": msg, "parse_mode": "Markdown"},
            timeout=10,
        )
        if not r.ok:
            print(f"TG 오류: {r.text}", flush=True)
    except Exception as e:
        print(f"TG 예외: {e}", flush=True)

# ── KIS 실시간 시세 ────────────────────────────────────────────
def get_kis_price(ticker: str) -> dict:
    if not KIS_KEY or not KIS_SECRET:
        return {}
    base = ("https://openapivts.koreainvestment.com:29443" if KIS_MOCK
            else "https://openapi.koreainvestment.com:9443")
    try:
        r = requests.post(f"{base}/oauth2/tokenP", json={
            "grant_type": "client_credentials",
            "appkey": KIS_KEY, "appsecret": KIS_SECRET,
        }, timeout=10)
        if not r.ok:
            return {}
        token = r.json().get("access_token", "")
        if not token:
            return {}
    except Exception:
        return {}

    # 해외 ETF는 KIS 해외주식 API 사용
    try:
        headers = {
            "authorization": f"Bearer {token}",
            "appkey": KIS_KEY, "appsecret": KIS_SECRET,
            "tr_id": "HHDFS00000300",
            "content-type": "application/json; charset=utf-8",
        }
        params = {
            "AUTH": "", "EXCD": "NAS",
            "SYMB": ticker, "GUBN": "0", "BYMD": "", "MODP": "1",
        }
        resp = requests.get(
            f"{base}/uapi/overseas-price/v1/quotations/dailyprice",
            headers=headers, params=params, timeout=10,
        )
        if not resp.ok:
            return {}
        out = resp.json().get("output2", [{}])[0]
        price = float(out.get("clos", 0))
        if price == 0:
            return {}
        return {"price": price, "source": "KIS"}
    except Exception:
        return {}

# ── yfinance 시세 ─────────────────────────────────────────────
def get_yf_price(ticker: str) -> dict:
    try:
        hist = yf.Ticker(ticker).history(period="5d")
        if hist.empty:
            return {}
        close      = float(hist["Close"].iloc[-1])
        prev_close = float(hist["Close"].iloc[-2]) if len(hist) > 1 else close
        chg        = (close - prev_close) / prev_close * 100
        return {
            "price":      round(close, 2),
            "prev_close": round(prev_close, 2),
            "change_pct": round(chg, 2),
            "source":     "yfinance",
        }
    except Exception as e:
        print(f"yfinance 오류: {e}", flush=True)
        return {}

# ── 기술적 지표 + 점수 ─────────────────────────────────────────
def get_tech_score(ticker: str) -> dict:
    try:
        df = yf.download(ticker, period="6mo", progress=False, auto_adjust=True)
        if df.empty or len(df) < 30:
            return {}
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)

        df.ta.rsi(length=14, append=True)
        df.ta.macd(fast=12, slow=26, signal=9, append=True)
        df.ta.bbands(length=20, std=2, append=True)
        df.ta.sma(length=5, append=True)
        df.ta.sma(length=20, append=True)
        df.ta.sma(length=60, append=True)
        df.ta.adx(length=14, append=True)
        df.ta.stoch(k=14, d=3, append=True)
        df["VOL_MA20"]  = df["Volume"].rolling(20).mean()
        df["VOL_RATIO"] = df["Volume"] / df["VOL_MA20"].replace(0, np.nan)

        last = df.iloc[-1]
        prev = df.iloc[-2]
        score = 0
        signals = []

        rsi_col  = next((c for c in df.columns if "RSI_14" in c), None)
        hist_col = next((c for c in df.columns if c.startswith("MACDh_")), None)
        bbl      = next((c for c in df.columns if "BBL_" in c), None)
        bbu      = next((c for c in df.columns if "BBU_" in c), None)
        adx_col  = next((c for c in df.columns if c.startswith("ADX_")), None)
        stk      = next((c for c in df.columns if c.startswith("STOCHk_")), None)
        std      = next((c for c in df.columns if c.startswith("STOCHd_")), None)

        adx = float(last.get(adx_col, 0) or 0) if adx_col else 0
        adx_mult = 1.2 if adx >= 30 else (1.0 if adx >= 20 else 0.7)

        if rsi_col and pd.notna(last.get(rsi_col)):
            rsi = float(last[rsi_col])
            if rsi <= 30:
                score += int(25 * adx_mult); signals.append(("BUY", f"RSI 과매도 ({rsi:.1f})"))
            elif rsi >= 70:
                score -= int(25 * adx_mult); signals.append(("SELL", f"RSI 과매수 ({rsi:.1f})"))

        if hist_col:
            h = float(last.get(hist_col, 0) or 0)
            ph = float(prev.get(hist_col, 0) or 0)
            if h > 0 and ph <= 0:
                score += int(20 * adx_mult); signals.append(("BUY", "MACD 상향 반전"))
            elif h < 0 and ph >= 0:
                score -= int(20 * adx_mult); signals.append(("SELL", "MACD 하향 반전"))
            elif h > 0:
                score += int(10 * adx_mult)
            else:
                score -= int(10 * adx_mult)

        if bbl and bbu:
            l, u = float(last[bbl]), float(last[bbu])
            c = float(last["Close"])
            if c <= l * 1.005:
                score += 15; signals.append(("BUY", "볼린저 하단 이탈"))
            elif c >= u * 0.995:
                score -= 15; signals.append(("SELL", "볼린저 상단 이탈"))

        s5  = float(last.get("SMA_5",  0) or 0)
        s20 = float(last.get("SMA_20", 0) or 0)
        s60 = float(last.get("SMA_60", 0) or 0)
        p5  = float(prev.get("SMA_5",  0) or 0)
        p20 = float(prev.get("SMA_20", 0) or 0)
        if s5 > 0 and s20 > 0 and s60 > 0:
            if s5 > s20 > s60:   score += int(20 * adx_mult)
            elif s5 < s20 < s60: score -= int(20 * adx_mult)
        if p5 <= p20 and s5 > s20:
            signals.append(("BUY", "골든크로스"))
        elif p5 >= p20 and s5 < s20:
            signals.append(("SELL", "데드크로스"))

        if stk and std:
            k  = float(last.get(stk, 50) or 50)
            d  = float(last.get(std, 50) or 50)
            pk = float(prev.get(stk, 50) or 50)
            if k < 20 and k > d and pk <= d:
                score += 10; signals.append(("BUY", f"스토캐스틱 과매도 반전 (K={k:.0f})"))
            elif k > 80 and k < d and pk >= d:
                score -= 10; signals.append(("SELL", f"스토캐스틱 과매수 반전 (K={k:.0f})"))

        return {
            "score":   max(-100, min(100, score)),
            "signals": signals,
            "adx":     round(adx, 1),
            "rsi":     round(float(last[rsi_col]), 1) if rsi_col and pd.notna(last.get(rsi_col)) else 0,
        }
    except Exception as e:
        print(f"기술적 분석 오류: {e}", flush=True)
        return {}

# ── 메인 ──────────────────────────────────────────────────────
def main():
    now_kst = datetime.now(timezone.utc).strftime("%H:%M UTC")
    print(f"[{now_kst}] KORU 체크 시작", flush=True)

    state = load_state()
    last_alerts = set(state.get("last_alerts", []))

    # 시세 조회 (KIS 우선, yfinance fallback)
    price_info = get_kis_price("KORU") or get_yf_price("KORU")
    if not price_info or price_info.get("price", 0) == 0:
        print("시세 조회 실패 — 종료", flush=True)
        sys.exit(0)

    price      = float(price_info["price"])
    change_pct = float(price_info.get("change_pct", 0))
    pnl_pct    = (price - AVG_COST) / AVG_COST * 100
    source     = price_info.get("source", "?")
    print(f"  KORU: ${price:.2f} ({change_pct:+.2f}%) | 평단대비: {pnl_pct:+.2f}% [{source}]", flush=True)

    tech = get_tech_score("KORU")
    score   = tech.get("score", 0)
    signals = tech.get("signals", [])
    adx     = tech.get("adx", 0)
    rsi     = tech.get("rsi", 0)
    action  = "BUY" if score >= 30 else ("SELL" if score <= -30 else "HOLD")
    print(f"  점수: {score} ({action}) | ADX: {adx} | RSI: {rsi}", flush=True)

    alerts = []
    new_alerts = set(last_alerts)

    def alert_once(key: str, msg: str, clear_on_exit=None):
        if key not in last_alerts:
            alerts.append(msg)
            new_alerts.add(key)
        if clear_on_exit:
            new_alerts.discard(clear_on_exit)

    # ── 가격 레벨 알림 ───────────────────────────────────────
    if price <= STOP_LOSS:
        alert_once("stop",
            f"🚨 *강제손절 구간!*\n"
            f"  현재: ${price:.2f}  평단대비 {pnl_pct:.1f}%\n"
            f"  손절선: ${STOP_LOSS:.2f}")
    else:
        new_alerts.discard("stop")

    if price <= ADD_BUY_LV and price > STOP_LOSS:
        alert_once("add_buy",
            f"💡 *추가매수 고려 구간*\n"
            f"  현재: ${price:.2f}  평단대비 {pnl_pct:.1f}%\n"
            f"  기준: ${ADD_BUY_LV:.2f}")
    else:
        new_alerts.discard("add_buy")

    if price >= PROFIT_2:
        alert_once("profit2",
            f"🎯 *2차 익절 도달!*\n"
            f"  현재: ${price:.2f}  평단대비 +{pnl_pct:.1f}%\n"
            f"  목표: ${PROFIT_2:.2f}")
    else:
        new_alerts.discard("profit2")

    if PROFIT_1 <= price < PROFIT_2:
        alert_once("profit1",
            f"✅ *1차 익절 구간*\n"
            f"  현재: ${price:.2f}  평단대비 +{pnl_pct:.1f}%\n"
            f"  목표: ${PROFIT_1:.2f}")
    else:
        new_alerts.discard("profit1")

    # ── 기술적 신호 알림 ─────────────────────────────────────
    buy_sigs  = [s for s in signals if s[0] == "BUY"]
    sell_sigs = [s for s in signals if s[0] == "SELL"]

    if (len(buy_sigs) >= 2 or score >= 50) and adx >= 20:
        sig_key = f"sig_buy_{score}"
        old_sig_keys = {k for k in new_alerts if k.startswith("sig_buy_") and k != sig_key}
        new_alerts.difference_update(old_sig_keys)
        sig_lines = "\n".join(f"  ↑ {s[1]}" for s in buy_sigs[:3])
        alert_once(sig_key,
            f"⚡ *KORU 매수 신호*\n"
            f"  현재: ${price:.2f} ({change_pct:+.2f}%)  평단대비: {pnl_pct:+.2f}%\n"
            f"  점수: {score} | RSI: {rsi} | ADX: {adx}\n"
            f"{sig_lines}")

    if (len(sell_sigs) >= 2 or score <= -50) and adx >= 20:
        sig_key = f"sig_sell_{score}"
        old_sig_keys = {k for k in new_alerts if k.startswith("sig_sell_") and k != sig_key}
        new_alerts.difference_update(old_sig_keys)
        sig_lines = "\n".join(f"  ↓ {s[1]}" for s in sell_sigs[:3])
        alert_once(sig_key,
            f"⚡ *KORU 매도 신호*\n"
            f"  현재: ${price:.2f} ({change_pct:+.2f}%)  평단대비: {pnl_pct:+.2f}%\n"
            f"  점수: {score} | RSI: {rsi} | ADX: {adx}\n"
            f"{sig_lines}")

    # ── 알림 발송 ─────────────────────────────────────────────
    for msg in alerts:
        full_msg = f"📊 *[KORU 모니터] {now_kst}*\n{msg}"
        tg(full_msg)
        print(f"  TG 발송: {msg[:50]}...", flush=True)

    if not alerts:
        print("  신호 없음", flush=True)

    # 상태 저장
    state["last_alerts"] = list(new_alerts)
    state["last_score"]  = score
    state["last_price"]  = price
    state["last_check"]  = now_kst
    save_state(state)
    print("완료", flush=True)


if __name__ == "__main__":
    main()
