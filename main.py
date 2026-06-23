# -*- coding: utf-8 -*-
"""
실시간 투자 결정 보조 앱
스케줄: 08:00 아침브리핑 → 08:50 장전주문알림 → 장중 10분체크 → 15:20 장마감브리핑
"""
import yaml, schedule, time, sys, os
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from agents.data_agent        import (get_domestic_data, get_foreign_data, add_indicators,
                                      get_signals, get_current_price, score_stock,
                                      get_sl_tp, get_pivot_levels, get_entry_price,
                                      nearest_support, nearest_resistance, round_to_tick)
from agents.news_agent        import (get_portfolio_news, fetch_google_news,
                                      get_sentiment_summary, get_market_events,
                                      classify_event_impact, get_surge_candidates)
from agents.decision_agent    import get_decision
from agents.notify_agent      import send_telegram
from agents.correlation_agent import compute_betas, estimate_open_move, get_etf_overnight
from agents.signal_logger     import log_signal, get_signal_summary

# ── 공통 유틸 ────────────────────────────────────────────────

def load_config():
    p = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'config.yaml')
    with open(p, encoding='utf-8') as f:
        return yaml.safe_load(f)

def tg(cfg, msg: str):
    ok = send_telegram(msg, cfg['telegram']['bot_token'], cfg['telegram']['chat_id'])
    if not ok:
        print(msg, flush=True)

def _bar(score: int) -> str:
    filled = int(abs(score) / 10)
    bar    = '█' * filled + '░' * (10 - filled)
    sign   = '+' if score >= 0 else '-'
    return f"{sign}[{bar}]{score:+d}"

def _arrow(v: float) -> str:
    return '▲' if v > 0 else '▼'

def _icon(action: str) -> str:
    return {'BUY': '📈', 'SELL': '📉', 'HOLD': '➖'}.get(action, '➖')

def _fmt_price(price, is_dom: bool) -> str:
    if is_dom:
        return f"{int(price):,}원"
    return f"${float(price):,.2f}"

# ── 전 종목 분석 ──────────────────────────────────────────────

def analyze_all(cfg, betas: dict = None) -> list:
    """전 종목 데이터 수집 → 지표 계산 → 점수/신호 생성"""
    results = []
    items   = [(s, True)  for s in cfg['portfolio']['domestic']] + \
              [(s, False) for s in cfg['portfolio']['foreign']]

    for item, is_dom in items:
        ticker, name = item['ticker'], item['name']
        print(f"  [{name}] 분석...", flush=True)
        try:
            df         = get_domestic_data(ticker) if is_dom else get_foreign_data(ticker)
            if df.empty:
                continue
            df         = add_indicators(df)
            price_info = get_current_price(ticker, is_dom)
            if not price_info:
                price_info = {'price': float(df['Close'].iloc[-1]),
                              'prev_close': float(df['Close'].iloc[-2]) if len(df) > 1 else float(df['Close'].iloc[-1]),
                              'change_pct': 0.0, 'volume': 0, 'prev_volume': 0}

            signals    = get_signals(df, cfg['alerts'])
            score_info = score_stock(df, price_info)
            action     = score_info['action']
            sltp       = get_sl_tp(df, float(price_info['price']), action, is_dom)
            pivot      = get_pivot_levels(df)

            results.append({
                'name': name, 'ticker': ticker, 'is_dom': is_dom,
                'price':       float(price_info['price']),
                'prev_close':  float(price_info.get('prev_close', price_info['price'])),
                'change_pct':  float(price_info.get('change_pct', 0)),
                'volume':      int(price_info.get('volume', 0)),
                'prev_volume': int(price_info.get('prev_volume', 1)),
                'signals':     signals,
                'score_info':  score_info,
                'score':       score_info['score'],
                'action':      action,
                'adx':         score_info.get('adx', 0),
                'trend':       score_info.get('trend_strength', ''),
                'sltp':        sltp,
                'pivot':       pivot,
                'price_info':  price_info,
                'df':          df,
            })
        except Exception as e:
            print(f"  [{name}] 오류: {e}", flush=True)
    return results

# ══════════════════════════════════════════════════════════════
# 1. 아침 브리핑 (08:00) - 해외 시황 + 종목 스캔
# ══════════════════════════════════════════════════════════════

def morning_brief(cfg):
    print(f"\n[{datetime.now():%H:%M}] 아침 브리핑", flush=True)
    dt      = datetime.now().strftime('%m/%d(%a)')
    api_key = cfg.get('claude', {}).get('api_key', '')

    # 뉴스 수집
    us_news    = fetch_google_news("US stock futures semiconductor overnight",
                                   lang='en', max_items=5, hours_limit=12)
    semi_news  = fetch_google_news("반도체 주가 전망 오늘", lang='ko',
                                   max_items=5, hours_limit=12)
    macro_news = fetch_google_news("코스피 증시 오늘 전망 환율", lang='ko',
                                   max_items=3, hours_limit=12)

    # ETF 시세
    etf_info = get_etf_overnight(cfg)
    soxl_chg = etf_info.get('SOXL', {}).get('change_pct', 0.0)
    koru_chg = etf_info.get('KORU', {}).get('change_pct', 0.0)

    # 베타 캐시 로드 (재계산 없이)
    from agents.correlation_agent import _load_cache
    betas = _load_cache()

    L = []
    L.append(f"🌅 *[아침 브리핑] {dt} 08:00*")
    L.append("━" * 32)

    # ① 해외 시황 뉴스
    L.append("\n① 해외 시황 (간밤)")
    us_sent = get_sentiment_summary(us_news)
    sent_icon = '🟢' if us_sent['score'] > 0 else ('🔴' if us_sent['score'] < 0 else '⚪')
    L.append(f"  분위기: {sent_icon} {us_sent['label']} "
             f"(긍정 {us_sent['pos']}건 / 부정 {us_sent['neg']}건)")
    for i, n in enumerate(us_news[:3], 1):
        icon = '📈' if n['sentiment']['score'] > 0 else ('📉' if n['sentiment']['score'] < 0 else '📰')
        L.append(f"  {i}. {icon} {n['title'][:60]}")

    # ② ETF 전일 종가 + 반도체 영향 예상
    L.append("\n② 보유 ETF (전일 종가)")
    for tk, info in etf_info.items():
        chg   = info['change_pct']
        arrow = _arrow(chg)
        L.append(f"  • {info['name']}: ${info['price']:,.2f}  {arrow}{abs(chg):.2f}%")

    # SOXL 영향 해석
    if abs(soxl_chg) >= 1.0:
        direction = "상승 호재" if soxl_chg > 0 else "하락 우려"
        L.append(f"\n  📌 SOXL {soxl_chg:+.2f}% → 국내 반도체 {direction} 예상")

    # ③ 반도체/거시 뉴스
    L.append("\n③ 국내 동향")
    for i, n in enumerate((semi_news + macro_news)[:4], 1):
        icon = '📈' if n['sentiment']['score'] > 0 else ('📉' if n['sentiment']['score'] < 0 else '📰')
        L.append(f"  {i}. {icon} {n['title'][:60]}")

    # ④ 전 종목 점수 스캔
    L.append("\n④ 종목 스캔 (종합점수 순)")
    results = analyze_all(cfg, betas)

    for r in sorted(results, key=lambda x: -abs(x['score'])):
        score  = r['score']
        action = r['action']
        chg    = r['change_pct']
        icon   = _icon(action)
        arrow  = _arrow(chg)
        trend  = f" [{r['trend'][:4]}]" if r['adx'] < 20 else ""
        L.append(f"  {icon} {r['name']}: {_bar(score)}  {arrow}{abs(chg):.1f}%{trend}")

        # 강한 신호 종목: 손절/목표 표시
        if abs(score) >= 40 and r['signals']:
            sig = r['signals'][0]
            L.append(f"     └ [{sig[0]}] {sig[1]}")
            if r['sltp']:
                sl_p = r['sltp']['sl_pct']
                tp_p = r['sltp']['tp_pct']
                rr   = r['sltp']['rr']
                L.append(f"     └ 손절 {sl_p:+.1f}% / 목표 {tp_p:+.1f}% (R:R={rr})")

    # ⑤ 오늘/내일 국내외 경제 일정
    events = get_market_events()
    if events:
        L.append("\n⑤ 주요 경제 일정 (48h 이내)")
        for n in events[:5]:
            impact = classify_event_impact(n['title'])
            age_str = f"{n['age_hours']:.0f}h전" if n['age_hours'] < 24 else f"{int(n['age_hours']/24)}일전"
            if impact:
                L.append(f"  • {n['title'][:55]}  [{age_str}]")
                L.append(f"    └ {impact}")
            else:
                L.append(f"  • {n['title'][:60]}  [{age_str}]")

    L.append("\n━" * 16)
    L.append("📌 08:50 예약주문 알림 예정 | 09:00 장 시작")
    tg(cfg, '\n'.join(L))
    print("  아침 브리핑 완료", flush=True)

# ══════════════════════════════════════════════════════════════
# 2. 장전 예약주문 알림 (08:50) ← 핵심 알림
# ══════════════════════════════════════════════════════════════

def pre_market_brief(cfg):
    print(f"\n[{datetime.now():%H:%M}] 장전 주문 알림", flush=True)
    dt      = datetime.now().strftime('%m/%d')
    api_key = cfg.get('claude', {}).get('api_key', '')

    # ETF 시세 + 베타 계산 (강제 재계산)
    etf_info = get_etf_overnight(cfg)
    soxl_chg = etf_info.get('SOXL', {}).get('change_pct', 0.0)
    koru_chg = etf_info.get('KORU', {}).get('change_pct', 0.0)
    betas    = compute_betas(cfg['portfolio'])   # 매일 아침 1회 재계산

    # 뉴스 (최신 6시간 이내)
    hot_news = fetch_google_news("반도체 주가 오늘 급등 급락", lang='ko',
                                  max_items=3, hours_limit=6)

    results  = analyze_all(cfg, betas)
    api_key  = cfg.get('claude', {}).get('api_key', '')

    # 주목 종목 = 점수 절대값 30 이상 OR 신호 2개 이상
    watch = [r for r in results if abs(r['score']) >= 30 or len(r['signals']) >= 2]
    # 점수 절대값 내림차순 정렬
    watch = sorted(watch, key=lambda x: -abs(x['score']))

    L = []
    L.append(f"⏰ *[장전 주문 알림] {dt} 08:50*")
    L.append("━" * 32)

    # ETF 요약
    soxl_icon = '🟢' if soxl_chg > 0 else '🔴'
    koru_icon = '🟢' if koru_chg > 0 else '🔴'
    L.append(f"\n📊 SOXL {soxl_icon} {soxl_chg:+.2f}%  |  KORU {koru_icon} {koru_chg:+.2f}%")

    if not watch:
        L.append("\n✅ 현재 강한 신호 없음 — 오늘은 관망 추천")
        L.append("   (신호 발생 시 장중 즉시 알림 예정)")
        L.append("\n━" * 16)
        L.append("📌 동시호가 마감 09:00 | 주문 서두르세요!")
        tg(cfg, '\n'.join(L))
        return

    L.append(f"\n🎯 *오늘 예약주문 추천 ({len(watch)}종목)*")
    L.append("─" * 28)

    for rank, r in enumerate(watch, 1):
        ticker     = r['ticker']
        name       = r['name']
        action     = r['action']
        score      = r['score']
        is_dom     = r['is_dom']
        prev_close = r['prev_close']
        price      = r['price']
        signals    = r['signals']
        sltp       = r['sltp']
        pivot      = r['pivot']

        # 시가 예상 등락 (SOXL/KORU 기반)
        if is_dom:
            exp_move = estimate_open_move(ticker, betas, soxl_chg, koru_chg)
        else:
            exp_move = 0.0   # 해외 ETF은 이미 시세 반영됨

        # 예약 진입가 계산
        entry = get_entry_price(prev_close, action, exp_move, is_dom)

        # R:R 재계산 (진입가 기준)
        if sltp:
            sl_pct = round((sltp['sl'] - entry) / entry * 100, 1) if entry > 0 else sltp['sl_pct']
            tp_pct = round((sltp['tp'] - entry) / entry * 100, 1) if entry > 0 else sltp['tp_pct']
            rr     = round(abs(tp_pct) / abs(sl_pct), 1) if sl_pct != 0 else 0
        else:
            sl_pct, tp_pct, rr = -3.0, 6.0, 2.0

        # 종목 알림 블록
        medal = ['🥇', '🥈', '🥉', '4️⃣', '5️⃣'][min(rank - 1, 4)]
        icon  = _icon(action)
        L.append(f"\n{medal} {icon} *{name}*  [{action}]  {_bar(score)}")

        # 현재가 + 예상 시가
        exp_open = round_to_tick(prev_close * (1 + exp_move / 100), is_dom)
        if is_dom:
            L.append(f"  • 전일종가: {_fmt_price(prev_close, is_dom)}")
            if abs(exp_move) >= 0.3:
                L.append(f"  • 예상시가: {_fmt_price(exp_open, is_dom)}  ({exp_move:+.1f}%)")
            L.append(f"  • ✅ 예약{action}가: *{_fmt_price(entry, is_dom)}*")
        else:
            L.append(f"  • 현재가: {_fmt_price(price, is_dom)}")
            L.append(f"  • ✅ 진입가: *{_fmt_price(entry, is_dom)}*")

        # 손절/목표
        if sltp:
            sl_str = _fmt_price(sltp['sl'], is_dom)
            tp_str = _fmt_price(sltp['tp'], is_dom)
            L.append(f"  • 손절: {sl_str} ({sl_pct:+.1f}%)")
            L.append(f"  • 목표: {tp_str} ({tp_pct:+.1f}%)")
            if rr >= 1.5:
                L.append(f"  • R:R = 1:{rr}  {'✅ 양호' if rr >= 2 else '⚠️ 주의'}")
            else:
                L.append(f"  • R:R = 1:{rr}  ❌ 불리 — 진입 재검토")

        # 신호 근거
        if signals:
            L.append(f"  • 근거:")
            for sig_type, reason in signals[:3]:
                bullet = '  ↑' if sig_type == 'BUY' else '  ↓'
                L.append(f"  {bullet} {reason}")

        # 추세 경고
        if r['adx'] < 20:
            L.append(f"  ⚠️ 추세 약함 (ADX={r['adx']:.0f}) — 소액 or 관망 권장")

        # AI / 규칙 기반 한 줄 의견
        news_for_stock = []  # 속도 위해 빈 리스트 (뉴스는 장중 알림에서)
        decision = get_decision(name, r['price_info'], signals, news_for_stock,
                                api_key, r['score_info'])
        for line in decision.split('\n'):
            L.append(f"  ▶ {line}")

        # 신호 로깅
        if sltp and action != 'HOLD':
            log_signal(name, ticker, action, price, entry,
                       sltp['sl'], sltp['tp'], score, signals)

    # 최근 신호 적중률
    acc_summary = get_signal_summary()
    if '건' in acc_summary:
        L.append(f"\n📊 {acc_summary}")

    L.append("\n━" * 16)
    L.append("📌 동시호가 마감 09:00 — 서두르세요! ⏳")
    tg(cfg, '\n'.join(L))
    print(f"  장전 알림 완료 ({len(watch)}종목)", flush=True)

# ══════════════════════════════════════════════════════════════
# 3. 장중 신호 체크 (10분마다, 신호 있을 때만)
# ══════════════════════════════════════════════════════════════

def intraday_check(cfg):
    now_str = datetime.now().strftime('%H:%M')
    if not (cfg['schedule']['market_open'] <= now_str <= cfg['schedule']['market_close']):
        return

    print(f"\n[{now_str}] 장중 체크", flush=True)
    api_key = cfg.get('claude', {}).get('api_key', '')
    results = analyze_all(cfg)
    min_sig = cfg['alerts'].get('min_signals_to_notify', 2)

    # 강한 신호 종목: 신호 2개 이상 OR 점수 절대값 50 이상
    alert_items = [r for r in results
                   if len(r['signals']) >= min_sig or abs(r['score']) >= 50]
    if not alert_items:
        print("  신호 없음", flush=True)
        return

    # 뉴스 (알림 대상 종목만, 최신 6시간)
    alert_pf  = [{'ticker': r['ticker'], 'name': r['name']} for r in alert_items]
    news_dict = get_portfolio_news(alert_pf, hours_limit=6)

    L = []
    L.append(f"⚡ *[장중 신호] {now_str}*")
    L.append("━" * 32)

    for r in alert_items:
        chg    = r['change_pct']
        score  = r['score']
        action = r['action']
        sltp   = r['sltp']
        icon   = _icon(action)
        arrow  = _arrow(chg)

        L.append(f"\n{icon} *{r['name']}*  [{action}]")
        L.append(f"  • 현재가: {_fmt_price(r['price'], r['is_dom'])}  {arrow}{abs(chg):.2f}%")
        L.append(f"  • 종합점수: {_bar(score)}  ADX={r['adx']:.0f}")

        for sig_type, reason in r['signals']:
            bullet = '↑' if sig_type == 'BUY' else '↓'
            L.append(f"  {bullet} {reason}")

        if sltp:
            sl_str = _fmt_price(sltp['sl'], r['is_dom'])
            tp_str = _fmt_price(sltp['tp'], r['is_dom'])
            L.append(f"  • 손절 {sl_str} ({sltp['sl_pct']:+.1f}%) | 목표 {tp_str} ({sltp['tp_pct']:+.1f}%)")
            L.append(f"  • R:R = 1:{sltp['rr']}")

        news_list = news_dict.get(r['name'], [])
        if news_list:
            sent = get_sentiment_summary(news_list)
            n0   = news_list[0]
            sent_icon = '🟢' if sent['score'] > 0 else ('🔴' if sent['score'] < 0 else '⚪')
            L.append(f"  {sent_icon} 뉴스: {n0['title'][:50]}")

        decision = get_decision(r['name'], r['price_info'], r['signals'],
                                news_list, api_key, r['score_info'])
        for line in decision.split('\n'):
            L.append(f"  ▶ {line}")

        if r['adx'] < 20:
            L.append(f"  ⚠️ 추세 약함 (횡보) — 신중히")

    tg(cfg, '\n'.join(L))
    print(f"  장중 알림: {len(alert_items)}종목", flush=True)

# ══════════════════════════════════════════════════════════════
# 4. 장마감 브리핑 (15:20)
# ══════════════════════════════════════════════════════════════

def eod_brief(cfg):
    print(f"\n[{datetime.now():%H:%M}] 장마감 브리핑", flush=True)
    dt      = datetime.now().strftime('%m/%d(%a)')
    api_key = cfg.get('claude', {}).get('api_key', '')

    results      = analyze_all(cfg)
    all_pf       = cfg['portfolio']['domestic'] + cfg['portfolio']['foreign']
    news_dict    = get_portfolio_news(all_pf, hours_limit=8)
    after_news   = fetch_google_news("한국 증시 내일 전망", lang='ko',
                                     max_items=4, hours_limit=12)
    us_after     = fetch_google_news("US market afterhours futures", lang='en',
                                     max_items=3, hours_limit=6)

    L = []
    L.append(f"🔔 *[장마감 브리핑] {dt} 15:20*")
    L.append("━" * 32)

    # ① 오늘 전 종목 등락 (등락률 순)
    L.append("\n① 오늘 등락 (전 종목)")
    for r in sorted(results, key=lambda x: -x['change_pct']):
        chg    = r['change_pct']
        arrow  = _arrow(chg)
        vol_r  = r['volume'] / r['prev_volume'] if r['prev_volume'] > 0 else 1
        vol_mk = f"  📊{vol_r:.1f}배" if vol_r >= 1.5 else ""
        L.append(f"  • {r['name']}: {arrow}{abs(chg):.2f}%  "
                 f"{_fmt_price(r['price'], r['is_dom'])}{vol_mk}")

    # ② 내일 주목 종목 (강한 신호)
    strong = [r for r in results if abs(r['score']) >= 30]
    if strong:
        L.append(f"\n② 내일 주목 종목 ({len(strong)}개)")
        for r in sorted(strong, key=lambda x: -abs(x['score']))[:5]:
            action = r['action']
            score  = r['score']
            icon   = _icon(action)
            L.append(f"\n  {icon} *{r['name']}*  [{action}]  {_bar(score)}")

            for _, reason in r['signals'][:2]:
                L.append(f"    └ {reason}")

            if r['sltp']:
                L.append(f"    └ 손절 {r['sltp']['sl_pct']:+.1f}% / 목표 {r['sltp']['tp_pct']:+.1f}%"
                         f" (R:R={r['sltp']['rr']})")

            news_list = news_dict.get(r['name'], [])
            if news_list:
                sent = get_sentiment_summary(news_list)
                sent_icon = '🟢' if sent['score'] > 0 else ('🔴' if sent['score'] < 0 else '⚪')
                L.append(f"    {sent_icon} {news_list[0]['title'][:50]}")

            decision = get_decision(r['name'], r['price_info'], r['signals'],
                                    news_list, api_key, r['score_info'])
            for line in decision.split('\n'):
                L.append(f"    ▶ {line}")

    # ③ 내일 전망 뉴스
    L.append("\n③ 내일 전망 뉴스")
    all_outlook = after_news + us_after
    all_sent    = get_sentiment_summary(all_outlook)
    overall_icon = '🟢' if all_sent['score'] > 0 else ('🔴' if all_sent['score'] < 0 else '⚪')
    L.append(f"  전반 분위기: {overall_icon} {all_sent['label']}"
             f" (긍정 {all_sent['pos']} / 부정 {all_sent['neg']})")
    for i, n in enumerate(all_outlook[:4], 1):
        icon = '📈' if n['sentiment']['score'] > 0 else ('📉' if n['sentiment']['score'] < 0 else '📰')
        L.append(f"  {i}. {icon} {n['title'][:60]}")

    # ④ 신호 성과 요약
    acc = get_signal_summary()
    if '건' in acc:
        L.append(f"\n④ 최근 신호 성과\n  {acc}")

    # ⑤ 내일 국내외 경제 일정 + 증시 영향
    events = get_market_events()
    if events:
        L.append("\n⑤ 내일 주요 경제 일정")
        for n in events[:5]:
            impact = classify_event_impact(n['title'])
            age_str = f"{n['age_hours']:.0f}h전" if n['age_hours'] < 24 else f"{int(n['age_hours']/24)}일전"
            icon = '📈' if n['sentiment']['score'] > 0 else ('📉' if n['sentiment']['score'] < 0 else '📅')
            L.append(f"  {icon} {n['title'][:55]}  [{age_str}]")
            if impact:
                L.append(f"     └ 증시영향: {impact}")

    # ⑥ 급등 예상 후보 (큰 뉴스 있는 종목)
    surge = get_surge_candidates(hours_limit=12)
    if surge:
        L.append(f"\n⑥ 내일 급등 후보 뉴스 ({len(surge)}건)")
        for n in surge[:5]:
            L.append(f"  🚀 {n['title'][:65]}")
            L.append(f"     └ {n['age_hours']:.0f}시간 전 | {n['sentiment']['label']}")

    L.append("\n━" * 16)
    L.append("📌 내일 07:50 아침브리핑 | 08:50 예약주문 알림")
    tg(cfg, '\n'.join(L))
    print("  장마감 브리핑 완료", flush=True)

# ══════════════════════════════════════════════════════════════
# 메인
# ══════════════════════════════════════════════════════════════

def main():
    cfg   = load_config()
    sched = cfg['schedule']
    api   = cfg.get('claude', {}).get('api_key', '')

    print("=" * 48, flush=True)
    print("  투자 결정 보조 앱", flush=True)
    print(f"  국내 {len(cfg['portfolio']['domestic'])}종목 | "
          f"해외 {len(cfg['portfolio']['foreign'])}종목", flush=True)
    print(f"  08:00 아침브리핑 | 08:50 장전주문 | "
          f"09:00~15:30 {sched['interval_minutes']}분 체크 | 15:20 마감브리핑", flush=True)
    print(f"  Claude AI: {'활성화' if api else '규칙 기반 모드'}", flush=True)
    print("=" * 48, flush=True)

    schedule.every().day.at(sched['morning_brief']).do(morning_brief,    cfg=cfg)
    schedule.every().day.at(sched['pre_market_brief']).do(pre_market_brief, cfg=cfg)
    schedule.every().day.at(sched['eod_brief']).do(eod_brief,             cfg=cfg)
    schedule.every(sched['interval_minutes']).minutes.do(intraday_check,  cfg=cfg)

    print("\n스케줄러 실행 중... (Ctrl+C 종료)\n", flush=True)
    while True:
        schedule.run_pending()
        time.sleep(30)

if __name__ == '__main__':
    main()
