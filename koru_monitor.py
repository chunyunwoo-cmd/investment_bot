# -*- coding: utf-8 -*-
"""
KORU 독립 모니터 — Claude 토큰 제로, 10분마다 실행
평균단가 773.96 기준 매수/매도 신호 발생 시 텔레그램 알림
"""
import sys, os, time, yaml, schedule
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from agents.data_agent import get_foreign_data, add_indicators, score_stock, get_signals, get_current_price
from agents.notify_agent import send_telegram

AVG_COST    = 773.96
ADD_BUY_LV  = AVG_COST * 0.93   # 평단 -7%: 추가매수 고려
STOP_LOSS   = AVG_COST * 0.88   # 평단 -12%: 강제 손절
PROFIT_1    = AVG_COST * 1.10   # 평단 +10%: 1차 익절
PROFIT_2    = AVG_COST * 1.20   # 평단 +20%: 2차 익절

with open(os.path.join(os.path.dirname(__file__), "config.yaml"), encoding="utf-8") as f:
    CFG = yaml.safe_load(f)

TG_TOKEN = CFG["telegram"]["bot_token"]
TG_CHAT  = CFG["telegram"]["chat_id"]

_last_alerts = set()   # 중복 알림 방지


def tg(msg: str):
    ok = send_telegram(msg, TG_TOKEN, TG_CHAT)
    if not ok:
        print(msg, flush=True)


def check_koru():
    now = datetime.now().strftime("%H:%M")
    print(f"[{now}] KORU 체크...", flush=True)

    # 미국 장외 시간 (한국 기준 04:00~23:30은 프리/애프터마켓 or 장마감)
    # 24시간 모니터링으로 유지 (ETF 특성상 장외가도 중요)

    try:
        price_info = get_current_price("KORU", is_domestic=False)
        if not price_info or price_info.get("price", 0) == 0:
            print("  시세 조회 실패", flush=True)
            return

        price      = float(price_info["price"])
        change_pct = float(price_info.get("change_pct", 0))
        pnl_pct    = (price - AVG_COST) / AVG_COST * 100

        df = get_foreign_data("KORU", period="3mo")
        df = add_indicators(df)
        if df.empty or len(df) < 30:
            print("  데이터 부족", flush=True)
            return

        score_info = score_stock(df, price_info)
        signals    = get_signals(df, CFG["alerts"])
        score      = score_info["score"]
        action     = score_info["action"]
        adx        = score_info["adx"]

        print(f"  가격: ${price:.2f} ({change_pct:+.2f}%) | 점수: {score} | 평단대비: {pnl_pct:+.2f}%", flush=True)

        alerts = []

        # ── 평단 기준 가격 알림 ──────────────────────────────
        if price <= STOP_LOSS and "stop" not in _last_alerts:
            alerts.append(f"🚨 *강제손절 구간 진입!*\n  현재: ${price:.2f} (평단 대비 {pnl_pct:.1f}%)\n  손절선: ${STOP_LOSS:.2f}")
            _last_alerts.add("stop")
        elif price > STOP_LOSS and "stop" in _last_alerts:
            _last_alerts.discard("stop")

        if price <= ADD_BUY_LV and "add_buy" not in _last_alerts:
            alerts.append(f"💡 *추가매수 고려 구간*\n  현재: ${price:.2f} (평단 대비 {pnl_pct:.1f}%)\n  기준선: ${ADD_BUY_LV:.2f}")
            _last_alerts.add("add_buy")
        elif price > ADD_BUY_LV and "add_buy" in _last_alerts:
            _last_alerts.discard("add_buy")

        if price >= PROFIT_2 and "profit2" not in _last_alerts:
            alerts.append(f"🎯 *2차 익절 구간 도달!*\n  현재: ${price:.2f} (평단 +{pnl_pct:.1f}%)\n  목표: ${PROFIT_2:.2f}")
            _last_alerts.add("profit2")
        elif price < PROFIT_2 and "profit2" in _last_alerts:
            _last_alerts.discard("profit2")

        if price >= PROFIT_1 and "profit1" not in _last_alerts:
            alerts.append(f"✅ *1차 익절 구간 도달*\n  현재: ${price:.2f} (평단 +{pnl_pct:.1f}%)\n  목표: ${PROFIT_1:.2f}")
            _last_alerts.add("profit1")
        elif price < PROFIT_1 and "profit1" in _last_alerts:
            _last_alerts.discard("profit1")

        # ── 기술적 신호 알림 (신호 2개 이상 or 점수 ±50 이상) ──
        strong_signals = [s for s in signals if s[0] in ("BUY", "SELL")]
        if (len(strong_signals) >= 2 or abs(score) >= 50) and adx >= 20:
            sig_key = f"sig_{score}"
            if sig_key not in _last_alerts:
                sig_lines = "\n".join(f"  {'↑' if s[0]=='BUY' else '↓'} {s[1]}" for s in strong_signals[:3])
                alerts.append(
                    f"⚡ *KORU 기술적 신호 ({action})*\n"
                    f"  현재: ${price:.2f} ({change_pct:+.2f}%)  평단대비: {pnl_pct:+.2f}%\n"
                    f"  종합점수: {score}  ADX: {adx:.0f}\n"
                    f"{sig_lines}"
                )
                _last_alerts.add(sig_key)
                # 신호 키 자동 초기화 (점수 변하면 재알림 허용)
                old_keys = {k for k in _last_alerts if k.startswith("sig_") and k != sig_key}
                _last_alerts.difference_update(old_keys)

        for msg in alerts:
            full_msg = f"📊 *[KORU 모니터] {now}*\n{msg}"
            tg(full_msg)

    except Exception as e:
        print(f"  오류: {e}", flush=True)


def main():
    print("=" * 50, flush=True)
    print("  KORU 독립 모니터 시작 (Claude 토큰 제로)", flush=True)
    print(f"  평균단가: ${AVG_COST:.2f}", flush=True)
    print(f"  추가매수: ${ADD_BUY_LV:.2f} | 손절: ${STOP_LOSS:.2f}", flush=True)
    print(f"  1차익절: ${PROFIT_1:.2f} | 2차익절: ${PROFIT_2:.2f}", flush=True)
    print("  10분마다 체크 → 신호 시 텔레그램 알림", flush=True)
    print("=" * 50, flush=True)

    # 시작하자마자 1회 즉시 체크
    check_koru()
    schedule.every(10).minutes.do(check_koru)

    tg(f"🟢 *KORU 모니터 시작* ({datetime.now().strftime('%H:%M')})\n"
       f"  평단: ${AVG_COST:.2f} | 10분 체크 중")

    while True:
        schedule.run_pending()
        time.sleep(30)


if __name__ == "__main__":
    main()
