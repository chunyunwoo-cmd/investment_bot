# -*- coding: utf-8 -*-
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

print("1. 패키지 임포트 테스트...")
import yfinance as yf
import pandas_ta as ta
import yaml, schedule, feedparser
print("   OK")

print("2. 해외 데이터 테스트 (NVDA)...")
from agents.data_agent import get_foreign_data, add_indicators, get_signals
df = get_foreign_data('NVDA', '1mo')
df = add_indicators(df)
last = df.iloc[-1]
rsi_col = [c for c in df.columns if c.startswith('RSI_')]
price = last['Close']
rsi = last[rsi_col[0]] if rsi_col else 0
print(f"   현재가: {price:.2f}  RSI: {rsi:.1f}")

signals = get_signals(df, {'rsi_oversold': 30, 'rsi_overbought': 70})
print(f"   신호: {signals if signals else '없음'}")
print("   OK")

print("3. 국내 데이터 테스트 (삼성전자)...")
try:
    from agents.data_agent import get_domestic_data
    df_kr = get_domestic_data('005930', 30)
    if not df_kr.empty:
        print(f"   삼성전자 현재가: {df_kr['Close'].iloc[-1]:,.0f}원")
        print("   OK")
    else:
        print("   데이터 없음 (pykrx 미설치 또는 장 외)")
except Exception as e:
    print(f"   SKIP: {e}")

print("4. 뉴스 테스트...")
from agents.news_agent import fetch_google_news
news = fetch_google_news("NVIDIA stock", lang='en', max_items=3)
print(f"   뉴스 {len(news)}건 수집")
if news:
    print(f"   최신: {news[0]['title'][:50]}")
print("   OK")

print("\n모든 테스트 완료!")
