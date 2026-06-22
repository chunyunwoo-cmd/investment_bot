# -*- coding: utf-8 -*-
"""Streamlit 대시보드"""
import streamlit as st
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import sys, os

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from agents.data_agent import get_domestic_data, get_foreign_data, add_indicators, get_signals

st.set_page_config(page_title="투자 보조 대시보드", layout="wide", page_icon="📈")
st.title("📈 실시간 투자 결정 보조 대시보드")

# ── 사이드바 ─────────────────────────────────────────────────
with st.sidebar:
    st.header("종목 선택")
    market = st.radio("시장", ["국내", "해외"])
    if market == "국내":
        options = {"삼성전자": "005930", "SK하이닉스": "000660",
                   "카카오": "035720", "NAVER": "035420"}
    else:
        options = {"NVIDIA": "NVDA", "Apple": "AAPL",
                   "Tesla": "TSLA", "Microsoft": "MSFT"}

    selected = st.selectbox("종목", list(options.keys()))
    ticker   = options[selected]
    is_dom   = (market == "국내")
    refresh  = st.button("🔄 새로고침")

# ── 데이터 로드 ───────────────────────────────────────────────
@st.cache_data(ttl=300)
def load(ticker, is_dom):
    df = get_domestic_data(ticker) if is_dom else get_foreign_data(ticker)
    return add_indicators(df)

with st.spinner("데이터 로딩 중..."):
    df = load(ticker, is_dom)

if df.empty:
    st.error("데이터를 불러올 수 없습니다.")
    st.stop()

last   = df.iloc[-1]
price  = last['Close']
change = (price - df.iloc[-2]['Close']) / df.iloc[-2]['Close'] * 100 if len(df) > 1 else 0

# ── 현재가 카드 ───────────────────────────────────────────────
c1, c2, c3, c4 = st.columns(4)
c1.metric("현재가", f"{price:,.0f}" + ("원" if is_dom else "$"),
          f"{change:+.2f}%", delta_color="normal")

rsi_col = next((c for c in df.columns if c.startswith('RSI_')), None)
if rsi_col:
    rsi = last[rsi_col]
    c2.metric("RSI (14)", f"{rsi:.1f}",
              "과매도" if rsi < 30 else ("과매수" if rsi > 70 else "중립"))

sma5  = last.get('SMA_5')
sma20 = last.get('SMA_20')
if sma5 and sma20:
    c3.metric("5일 SMA", f"{sma5:,.0f}", f"vs 20일 {sma20:,.0f}")

c4.metric("거래량", f"{int(last['Volume']):,}")

# ── 캔들 차트 + 지표 ─────────────────────────────────────────
fig = make_subplots(rows=3, cols=1, shared_xaxes=True,
                    row_heights=[0.6, 0.2, 0.2],
                    subplot_titles=("가격 & 이동평균 & 볼린저밴드", "거래량", "RSI"))

df_plot = df.tail(60)

# 캔들
fig.add_trace(go.Candlestick(
    x=df_plot.index, open=df_plot['Open'], high=df_plot['High'],
    low=df_plot['Low'], close=df_plot['Close'], name="가격"), row=1, col=1)

# 이동평균
for col, color, label in [('SMA_5','orange','MA5'),('SMA_20','blue','MA20'),('SMA_60','purple','MA60')]:
    if col in df_plot.columns:
        fig.add_trace(go.Scatter(x=df_plot.index, y=df_plot[col],
                                 line=dict(color=color, width=1),
                                 name=label), row=1, col=1)

# 볼린저 밴드
bbl = next((c for c in df_plot.columns if 'BBL_' in c), None)
bbm = next((c for c in df_plot.columns if 'BBM_' in c), None)
bbu = next((c for c in df_plot.columns if 'BBU_' in c), None)
if bbl and bbu:
    fig.add_trace(go.Scatter(x=df_plot.index, y=df_plot[bbu],
                             line=dict(color='gray', dash='dot', width=1), name='BB상단'), row=1, col=1)
    fig.add_trace(go.Scatter(x=df_plot.index, y=df_plot[bbl],
                             line=dict(color='gray', dash='dot', width=1),
                             fill='tonexty', fillcolor='rgba(128,128,128,0.1)', name='BB하단'), row=1, col=1)

# 거래량
colors = ['red' if c < o else 'blue' for c, o in zip(df_plot['Close'], df_plot['Open'])]
fig.add_trace(go.Bar(x=df_plot.index, y=df_plot['Volume'],
                     marker_color=colors, name='거래량'), row=2, col=1)

# RSI
if rsi_col:
    fig.add_trace(go.Scatter(x=df_plot.index, y=df_plot[rsi_col],
                             line=dict(color='purple', width=1.5), name='RSI'), row=3, col=1)
    fig.add_hline(y=70, line_dash='dash', line_color='red',   row=3, col=1)
    fig.add_hline(y=30, line_dash='dash', line_color='green', row=3, col=1)

fig.update_layout(height=700, showlegend=True,
                  xaxis_rangeslider_visible=False,
                  template='plotly_dark')
st.plotly_chart(fig, use_container_width=True)

# ── 투자 신호 ─────────────────────────────────────────────────
signals = get_signals(df, {'rsi_oversold': 30, 'rsi_overbought': 70})
st.subheader("⚡ 현재 투자 신호")
if signals:
    for sig_type, reason in signals:
        color = 'green' if sig_type == 'BUY' else ('red' if sig_type == 'SELL' else 'gray')
        st.markdown(f":{color}[**{sig_type}**] {reason}")
else:
    st.info("현재 특이 신호 없음 (HOLD)")

st.caption(f"마지막 업데이트: {df.index[-1].strftime('%Y-%m-%d')} | 데이터: {'pykrx' if is_dom else 'yfinance'}")
