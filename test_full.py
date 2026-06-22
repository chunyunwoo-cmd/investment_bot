# -*- coding: utf-8 -*-
import sys, os, yaml
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

with open('config.yaml', encoding='utf-8') as f:
    cfg = yaml.safe_load(f)

from agents.data_agent import get_domestic_data, get_foreign_data, add_indicators, get_signals, get_current_price

print('=== 전체 종목 분석 테스트 ===', flush=True)
items = [(s, True) for s in cfg['portfolio']['domestic']] + \
        [(s, False) for s in cfg['portfolio']['foreign']]

for item, is_dom in items:
    ticker, name = item['ticker'], item['name']
    try:
        df = get_domestic_data(ticker) if is_dom else get_foreign_data(ticker)
        if df.empty:
            print(f'  [{name}] 데이터 없음', flush=True)
            continue
        df = add_indicators(df)
        price = get_current_price(ticker, is_dom)
        signals = get_signals(df, cfg['alerts'])
        chg = float(price.get('change_pct', 0)) if price else 0.0
        p = price['price'] if price else '?'
        sig_str = f" | 신호: {[s[0] for s in signals]}" if signals else ""
        print(f"  OK {name}: {p} ({chg:+.2f}%){sig_str}", flush=True)
    except Exception as e:
        print(f"  ERR [{name}]: {e}", flush=True)

print('=== 완료 ===', flush=True)
