# -*- coding: utf-8 -*-
"""
신호 로깅 - CSV에 신호 기록하고 성과 추적
- 신호 발생 시 기록: 종목, 행동, 진입가, 손절가, 목표가, 점수
- 다음 거래일 종가 업데이트 → 신호 적중률 계산
"""
import csv, os, json
from datetime import datetime

LOG_FILE = os.path.join(os.path.dirname(__file__), '..', 'signals_log.csv')
HEADERS  = ['date', 'time', 'name', 'ticker', 'action', 'price',
            'entry', 'sl', 'tp', 'score', 'signals', 'outcome', 'outcome_pct']

def _ensure_log():
    if not os.path.exists(LOG_FILE):
        with open(LOG_FILE, 'w', newline='', encoding='utf-8-sig') as f:
            csv.writer(f).writerow(HEADERS)

def log_signal(name: str, ticker: str, action: str, price: float,
               entry: float, sl: float, tp: float, score: int, signals: list):
    """신호 발생 기록"""
    _ensure_log()
    now = datetime.now()
    sig_str = ' / '.join(f"{s[0]}:{s[1]}" for s in signals)
    row = [
        now.strftime('%Y-%m-%d'),
        now.strftime('%H:%M'),
        name, ticker, action,
        price, entry, sl, tp, score,
        sig_str, '', ''   # outcome은 나중에 채움
    ]
    with open(LOG_FILE, 'a', newline='', encoding='utf-8-sig') as f:
        csv.writer(f).writerow(row)

def get_recent_accuracy(days: int = 14) -> dict:
    """최근 N일 신호 적중률 계산"""
    _ensure_log()
    rows = []
    try:
        with open(LOG_FILE, encoding='utf-8-sig') as f:
            reader = csv.DictReader(f)
            rows = list(reader)
    except:
        return {}

    if not rows:
        return {}

    cutoff = datetime.now().date()
    from datetime import timedelta
    cutoff -= timedelta(days=days)

    recent = [r for r in rows
              if r.get('date') and r['date'] >= cutoff.isoformat()
              and r.get('outcome')]

    if not recent:
        return {'total': 0}

    total = len(recent)
    wins  = sum(1 for r in recent if r.get('outcome') == 'WIN')
    losses = sum(1 for r in recent if r.get('outcome') == 'LOSS')
    avg_pct = 0.0
    valid_pct = [float(r['outcome_pct']) for r in recent if r.get('outcome_pct')]
    if valid_pct:
        avg_pct = round(sum(valid_pct) / len(valid_pct), 2)

    return {
        'total': total,
        'wins': wins,
        'losses': losses,
        'win_rate': round(wins / total * 100, 1) if total > 0 else 0,
        'avg_pct': avg_pct,
    }

def get_signal_summary() -> str:
    """텔레그램용 신호 성과 요약"""
    acc = get_recent_accuracy(14)
    if not acc or acc.get('total', 0) == 0:
        return "최근 신호 데이터 없음"
    return (f"최근 14일 신호: {acc['total']}건 | "
            f"적중 {acc['win_rate']}% | 평균 {acc['avg_pct']:+.2f}%")
