# -*- coding: utf-8 -*-
"""데이터 수집 에이전트 - 국내(pykrx) + 해외(yfinance) + 기술적 지표"""
import yfinance as yf
import pandas as pd
import pandas_ta as ta
import numpy as np
import requests, yaml, os
from datetime import datetime, timedelta
import warnings
warnings.filterwarnings('ignore')

try:
    from pykrx import stock as krx
    PYKRX_AVAILABLE = True
except ImportError:
    PYKRX_AVAILABLE = False

# ── KIS 실시간 시세 ──────────────────────────────────────────

_KIS_TOKEN = None
_KIS_TOKEN_EXPIRES = None

def _load_kis_config() -> dict:
    p = os.path.join(os.path.dirname(__file__), '..', 'config.yaml')
    with open(p, encoding='utf-8') as f:
        cfg = yaml.safe_load(f)
    return cfg.get('kis', {})

def _get_kis_token() -> str:
    global _KIS_TOKEN, _KIS_TOKEN_EXPIRES
    if _KIS_TOKEN and _KIS_TOKEN_EXPIRES and datetime.now() < _KIS_TOKEN_EXPIRES:
        return _KIS_TOKEN
    kis = _load_kis_config()
    if not kis.get('app_key') or not kis.get('app_secret'):
        return ''
    base = 'https://openapivts.koreainvestment.com:29443' if kis.get('mock') else \
           'https://openapi.koreainvestment.com:9443'
    resp = requests.post(f"{base}/oauth2/tokenP", json={
        'grant_type':   'client_credentials',
        'appkey':       kis['app_key'],
        'appsecret':    kis['app_secret'],
    }, timeout=10)
    if resp.status_code != 200:
        return ''
    data = resp.json()
    _KIS_TOKEN = data.get('access_token', '')
    expires_in = int(data.get('expires_in', 86400))
    _KIS_TOKEN_EXPIRES = datetime.now() + timedelta(seconds=expires_in - 60)
    return _KIS_TOKEN

def get_kis_realtime_price(ticker: str) -> dict:
    """KIS API로 국내 종목 실시간 현재가 조회"""
    kis = _load_kis_config()
    if not kis.get('app_key'):
        return {}
    token = _get_kis_token()
    if not token:
        return {}
    base = 'https://openapivts.koreainvestment.com:29443' if kis.get('mock') else \
           'https://openapi.koreainvestment.com:9443'
    headers = {
        'authorization': f'Bearer {token}',
        'appkey':        kis['app_key'],
        'appsecret':     kis['app_secret'],
        'tr_id':         'FHKST01010100',
        'content-type':  'application/json; charset=utf-8',
    }
    params = {
        'FID_COND_MRKT_DIV_CODE': 'J',
        'FID_INPUT_ISCD': ticker,
    }
    try:
        resp = requests.get(f"{base}/uapi/domestic-stock/v1/quotations/inquire-price",
                            headers=headers, params=params, timeout=10)
        if resp.status_code != 200:
            return {}
        out = resp.json().get('output', {})
        price      = int(out.get('stck_prpr', 0))
        prev_close = int(out.get('stck_sdpr', 0))
        change_pct = float(out.get('prdy_ctrt', 0))
        volume     = int(out.get('acml_vol', 0))
        prev_vol   = int(out.get('prdy_vol', 0))
        if price == 0:
            return {}
        return {
            'ticker':      ticker,
            'price':       price,
            'prev_close':  prev_close,
            'change_pct':  change_pct,
            'volume':      volume,
            'prev_volume': prev_vol,
            'date':        datetime.now().strftime('%Y-%m-%d'),
            'source':      'KIS실시간',
        }
    except Exception as e:
        print(f"[KIS] {ticker} 오류: {e}", flush=True)
        return {}

# ── 데이터 수집 ──────────────────────────────────────────────

def get_domestic_data(ticker: str, period_days: int = 120) -> pd.DataFrame:
    if not PYKRX_AVAILABLE:
        return pd.DataFrame()
    try:
        end   = datetime.today().strftime('%Y%m%d')
        start = (datetime.today() - timedelta(days=period_days)).strftime('%Y%m%d')
        df = krx.get_market_ohlcv_by_date(start, end, ticker)
        col_map = {'시가': 'Open', '고가': 'High', '저가': 'Low',
                   '종가': 'Close', '거래량': 'Volume'}
        df = df.rename(columns=col_map)
        avail = [c for c in ['Open', 'High', 'Low', 'Close', 'Volume'] if c in df.columns]
        df = df[avail].copy()
        df.index = pd.to_datetime(df.index)
        return df[df['Volume'] > 0]   # 거래 없는 날 제거
    except Exception as e:
        print(f"[pykrx] {ticker} 오류: {e}")
        return pd.DataFrame()

def get_foreign_data(ticker: str, period: str = '6mo') -> pd.DataFrame:
    try:
        df = yf.download(ticker, period=period, progress=False, auto_adjust=True)
        if df.empty:
            return pd.DataFrame()
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        return df[['Open', 'High', 'Low', 'Close', 'Volume']].copy()
    except Exception as e:
        print(f"[yfinance] {ticker} 오류: {e}")
        return pd.DataFrame()

def get_current_price(ticker: str, is_domestic: bool) -> dict:
    if is_domestic:
        # 1순위: KIS 실시간
        kis_data = get_kis_realtime_price(ticker)
        if kis_data:
            return kis_data
        # 2순위: playwright → 네이버 금융 실시간
        try:
            from agents.playwright_agent import get_naver_price
            pw_data = get_naver_price(ticker)
            if pw_data:
                return pw_data
        except Exception:
            pass
        # 3순위: pykrx 전일 종가
        df = get_domestic_data(ticker, period_days=10)
        if df.empty:
            return {}
        row  = df.iloc[-1]
        prev = df.iloc[-2] if len(df) > 1 else row
        chg  = (float(row['Close']) - float(prev['Close'])) / float(prev['Close']) * 100
        return {
            'ticker':       ticker,
            'price':        int(row['Close']),
            'prev_close':   int(prev['Close']),
            'open':         int(row.get('Open', row['Close'])),
            'high':         int(row.get('High', row['Close'])),
            'low':          int(row.get('Low', row['Close'])),
            'change_pct':   round(chg, 2),
            'volume':       int(row['Volume']),
            'prev_volume':  int(prev['Volume']),
            'date':         df.index[-1].strftime('%Y-%m-%d'),
            'source':       'pykrx',
        }
    else:
        # 1순위: yfinance
        hist = yf.Ticker(ticker).history(period='5d')
        if not hist.empty:
            close      = float(hist['Close'].iloc[-1])
            prev_close = float(hist['Close'].iloc[-2]) if len(hist) > 1 else close
            chg        = (close - prev_close) / prev_close * 100
            return {
                'ticker':      ticker,
                'price':       round(close, 2),
                'prev_close':  round(prev_close, 2),
                'change_pct':  round(chg, 2),
                'volume':      int(hist['Volume'].iloc[-1]),
                'prev_volume': int(hist['Volume'].iloc[-2]) if len(hist) > 1 else 0,
                'date':        hist.index[-1].strftime('%Y-%m-%d'),
            }
        # 2순위: playwright → 야후 파이낸스 실시간
        try:
            from agents.playwright_agent import get_yahoo_price
            pw_data = get_yahoo_price(ticker)
            if pw_data:
                return pw_data
        except Exception:
            pass
        return {}

# ── 한국 주식 호가 단위 ──────────────────────────────────────

def round_to_tick(price: float, is_domestic: bool = True) -> float:
    """한국거래소 호가 단위로 반올림"""
    if not is_domestic:
        return round(price, 2)
    p = float(price)
    if p < 1_000:       step = 1
    elif p < 5_000:     step = 5
    elif p < 10_000:    step = 10
    elif p < 50_000:    step = 50
    elif p < 100_000:   step = 100
    elif p < 500_000:   step = 500
    else:               step = 1_000
    return int(round(p / step) * step)

# ── 지표 계산 ────────────────────────────────────────────────

def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty or len(df) < 30:
        return df
    df = df.copy()

    # 모멘텀
    df.ta.rsi(length=14, append=True)
    df.ta.rsi(length=7,  append=True)
    df.ta.macd(fast=12, slow=26, signal=9, append=True)
    df.ta.stoch(k=14, d=3, append=True)

    # 추세
    df.ta.sma(length=5,  append=True)
    df.ta.sma(length=20, append=True)
    df.ta.sma(length=60, append=True)
    df.ta.ema(length=12, append=True)
    df.ta.ema(length=26, append=True)
    df.ta.adx(length=14, append=True)      # 추세 강도 (14 이하=횡보, 25 이상=추세)

    # 변동성
    df.ta.bbands(length=20, std=2, append=True)
    df.ta.atr(length=14, append=True)

    # 거래량
    df.ta.obv(append=True)
    df['VOL_MA20'] = df['Volume'].rolling(20).mean()
    df['VOL_RATIO'] = df['Volume'] / df['VOL_MA20'].replace(0, np.nan)

    # 52주 고저
    if len(df) >= 60:
        df['W52_HIGH'] = df['High'].rolling(min(252, len(df))).max()
        df['W52_LOW']  = df['Low'].rolling(min(252, len(df))).min()

    return df

# ── 피봇 포인트 (지지/저항) ──────────────────────────────────

def get_pivot_levels(df: pd.DataFrame) -> dict:
    """
    전일 고가/저가/종가 기반 클래식 피봇 포인트
    당일 지지/저항 레벨로 활용
    """
    if df.empty or len(df) < 2:
        return {}
    last = df.iloc[-2]   # 전일 데이터 기준
    H, L, C = float(last['High']), float(last['Low']), float(last['Close'])
    P  = (H + L + C) / 3
    R1 = 2 * P - L
    R2 = P + (H - L)
    R3 = H + 2 * (P - L)
    S1 = 2 * P - H
    S2 = P - (H - L)
    S3 = L - 2 * (H - P)
    return {
        'P':  round(P),  'R1': round(R1), 'R2': round(R2), 'R3': round(R3),
        'S1': round(S1), 'S2': round(S2), 'S3': round(S3),
    }

def nearest_support(price: float, pivot: dict) -> float:
    """현재가 아래 가장 가까운 지지선"""
    supports = [v for k, v in pivot.items() if k.startswith('S') and v < price]
    return max(supports) if supports else price * 0.97

def nearest_resistance(price: float, pivot: dict) -> float:
    """현재가 위 가장 가까운 저항선"""
    resis = [v for k, v in pivot.items()
             if (k.startswith('R') or k == 'P') and v > price]
    return min(resis) if resis else price * 1.03

# ── ATR 기반 손절/목표가 ──────────────────────────────────────

def get_sl_tp(df: pd.DataFrame, price: float, action: str,
              is_domestic: bool = True) -> dict:
    """ATR 기반 손절가/목표가 (R:R = 1:2 이상 유지)"""
    atr_col = next((c for c in df.columns if 'ATR' in c and 'r_' not in c.lower()), None)
    if not atr_col or pd.isna(df[atr_col].iloc[-1]):
        # ATR 없으면 비율 기반 fallback
        if action == 'BUY':
            return {'sl': round_to_tick(price * 0.97, is_domestic),
                    'tp': round_to_tick(price * 1.06, is_domestic),
                    'sl_pct': -3.0, 'tp_pct': +6.0, 'rr': 2.0, 'atr': 0}
        else:
            return {'sl': round_to_tick(price * 1.03, is_domestic),
                    'tp': round_to_tick(price * 0.94, is_domestic),
                    'sl_pct': +3.0, 'tp_pct': -6.0, 'rr': 2.0, 'atr': 0}

    atr = float(df[atr_col].iloc[-1])
    if action == 'BUY':
        sl  = round_to_tick(price - atr * 1.5, is_domestic)
        tp  = round_to_tick(price + atr * 3.0, is_domestic)
    else:
        sl  = round_to_tick(price + atr * 1.5, is_domestic)
        tp  = round_to_tick(price - atr * 3.0, is_domestic)

    sl_pct = round((sl - price) / price * 100, 1)
    tp_pct = round((tp - price) / price * 100, 1)
    rr     = round(abs(tp_pct) / abs(sl_pct), 1) if sl_pct != 0 else 0.0
    return {'sl': sl, 'tp': tp, 'sl_pct': sl_pct, 'tp_pct': tp_pct,
            'rr': rr, 'atr': round(atr, 1)}

# ── 예약주문 진입가 계산 ─────────────────────────────────────

def get_entry_price(prev_close: float, action: str,
                    expected_move_pct: float = 0.0,
                    is_domestic: bool = True) -> float:
    """
    장 시작 전 예약 주문 진입가 계산.
    - 예상 시가 = 전일종가 × (1 + expected_move%)
    - 매수: 예상 시가보다 0.3% 낮게 (일부 매도 물량 흡수 목적)
    - 매도: 예상 시가보다 0.3% 높게 (초반 급등 시 매도 포착)
    """
    expected_open = prev_close * (1 + expected_move_pct / 100)
    if action == 'BUY':
        entry = expected_open * 0.997
    else:
        entry = expected_open * 1.003
    return round_to_tick(entry, is_domestic)

# ── 종합 점수 시스템 (-100 ~ +100) ───────────────────────────

def score_stock(df: pd.DataFrame, price_info: dict) -> dict:
    """
    다중 지표 종합 점수 계산.
    ADX 필터 포함: 추세 약하면(ADX<20) 신호 신뢰도 감소.
    """
    if df.empty or len(df) < 30:
        return {'score': 0, 'details': {}, 'adx': 0, 'trend_strength': '횡보'}

    last    = df.iloc[-1]
    prev    = df.iloc[-2]
    score   = 0
    details = {}

    # ADX 추세 강도 (±0~10 페널티/보너스)
    adx_col = next((c for c in df.columns if c.startswith('ADX_')), None)
    adx     = float(last.get(adx_col, 0) or 0) if adx_col else 0
    if adx >= 30:
        trend_strength = '강한 추세'
        adx_mult = 1.2   # 신호 증폭
    elif adx >= 20:
        trend_strength = '추세 형성'
        adx_mult = 1.0
    else:
        trend_strength = '횡보/약추세'
        adx_mult = 0.7   # 신호 약화 (횡보 구간은 신뢰도 낮음)

    # 1) RSI-14 (±25점)
    rsi_col = next((c for c in df.columns if 'RSI_14' in c), None)
    if rsi_col and pd.notna(last.get(rsi_col)):
        rsi = float(last[rsi_col])
        if rsi <= 25:       pts = +25
        elif rsi <= 35:     pts = +18
        elif rsi <= 45:     pts = +8
        elif rsi <= 55:     pts = 0
        elif rsi <= 65:     pts = -8
        elif rsi <= 75:     pts = -18
        else:               pts = -25
        score += int(pts * adx_mult)
        details['RSI'] = {'값': round(rsi, 1), '점': int(pts * adx_mult)}

    # 2) MACD 히스토그램 방향 + 반전 (±20점)
    hist_col   = next((c for c in df.columns if c.startswith('MACDh_')), None)
    macd_col   = next((c for c in df.columns if c.startswith('MACD_') and 'h' not in c and 's' not in c.lower()), None)
    signal_col = next((c for c in df.columns if c.startswith('MACDs_')), None)
    if hist_col:
        h  = float(last.get(hist_col,  0) or 0)
        ph = float(prev.get(hist_col,  0) or 0)
        if macd_col and signal_col:
            m = float(last.get(macd_col, 0) or 0)
            s = float(last.get(signal_col, 0) or 0)
        else:
            m, s = h, 0
        if h > 0 and ph <= 0:   pts = +20   # 골든 히스토그램 반전
        elif h < 0 and ph >= 0: pts = -20   # 데드 히스토그램 반전
        elif h > 0:             pts = +10
        else:                   pts = -10
        score += int(pts * adx_mult)
        details['MACD'] = {'히스토': round(h, 4), '점': int(pts * adx_mult)}

    # 3) 볼린저밴드 위치 (±15점)
    bbl = next((c for c in df.columns if 'BBL_' in c), None)
    bbu = next((c for c in df.columns if 'BBU_' in c), None)
    if bbl and bbu:
        l, u = float(last[bbl]), float(last[bbu])
        c_price = float(last['Close'])
        bb_pct  = (c_price - l) / (u - l) * 100 if (u - l) > 0 else 50
        if bb_pct <= 10:   pts = +15
        elif bb_pct <= 25: pts = +8
        elif bb_pct <= 75: pts = 0
        elif bb_pct <= 90: pts = -8
        else:              pts = -15
        score += pts
        details['BB위치'] = {'%': round(bb_pct, 1), '점': pts}

    # 4) 이동평균 배열 (±20점)
    sma5  = float(last.get('SMA_5',  0) or 0)
    sma20 = float(last.get('SMA_20', 0) or 0)
    sma60 = float(last.get('SMA_60', 0) or 0)
    if sma5 > 0 and sma20 > 0 and sma60 > 0:
        if sma5 > sma20 > sma60:   pts = +20   # 완전 정배열
        elif sma5 > sma20:         pts = +10
        elif sma5 < sma20 < sma60: pts = -20   # 완전 역배열
        elif sma5 < sma20:         pts = -10
        else:                      pts = 0
        score += int(pts * adx_mult)
        배열 = '정배열' if pts > 0 else ('역배열' if pts < 0 else '혼재')
        details['MA배열'] = {'배열': 배열, '5일': round(sma5), '20일': round(sma20), '점': int(pts * adx_mult)}

    # 5) 거래량 (±10점)
    vol_ratio = float(last.get('VOL_RATIO', 1) or 1)
    chg       = float(price_info.get('change_pct', 0))
    if vol_ratio >= 2.5 and chg > 0:    pts = +10
    elif vol_ratio >= 1.5 and chg > 0:  pts = +6
    elif vol_ratio >= 2.5 and chg < 0:  pts = -10
    elif vol_ratio >= 1.5 and chg < 0:  pts = -6
    else:                               pts = 0
    score += pts
    details['거래량'] = {'배율': round(vol_ratio, 1), '등락': f"{chg:+.1f}%", '점': pts}

    # 6) 스토캐스틱 (±10점)
    stk = next((c for c in df.columns if c.startswith('STOCHk_')), None)
    std = next((c for c in df.columns if c.startswith('STOCHd_')), None)
    if stk and std:
        k = float(last.get(stk, 50) or 50)
        d = float(last.get(std, 50) or 50)
        if k < 20 and k > d:    pts = +10
        elif k > 80 and k < d:  pts = -10
        elif k > d:             pts = +5
        else:                   pts = -5
        score += pts
        details['스토캐스틱'] = {'K': round(k, 1), 'D': round(d, 1), '점': pts}

    # 7) 52주 고저 근접도 (±5점 보너스)
    if 'W52_HIGH' in df.columns and 'W52_LOW' in df.columns:
        w52h = float(last.get('W52_HIGH', 0) or 0)
        w52l = float(last.get('W52_LOW', 0) or 0)
        c_price = float(last['Close'])
        if w52h > 0 and c_price >= w52h * 0.97:
            pts = -5    # 52주 최고가 근처 → 부담
            score += pts
            details['52주고가'] = {'근접': f"{c_price/w52h*100:.1f}%", '점': pts}
        elif w52l > 0 and c_price <= w52l * 1.05:
            pts = +5    # 52주 최저가 근처 → 바닥 신호
            score += pts
            details['52주저가'] = {'근접': f"{c_price/w52l*100:.1f}%", '점': pts}

    final_score = max(-100, min(100, score))
    action      = 'BUY' if final_score >= 30 else ('SELL' if final_score <= -30 else 'HOLD')

    return {
        'score':          final_score,
        'action':         action,
        'details':        details,
        'adx':            round(adx, 1),
        'trend_strength': trend_strength,
    }

# ── 신호 생성 ─────────────────────────────────────────────────

def get_signals(df: pd.DataFrame, cfg: dict) -> list:
    """기술적 지표 기반 구체적 신호 목록"""
    signals = []
    if df.empty or len(df) < 30:
        return signals

    last = df.iloc[-1]
    prev = df.iloc[-2]

    # RSI
    rsi_col = next((c for c in df.columns if 'RSI_14' in c), None)
    if rsi_col and pd.notna(last.get(rsi_col)):
        rsi = float(last[rsi_col])
        if rsi <= cfg.get('rsi_oversold', 30):
            signals.append(('BUY',  f'RSI 과매도 ({rsi:.1f}) - 반등 구간'))
        elif rsi >= cfg.get('rsi_overbought', 70):
            signals.append(('SELL', f'RSI 과매수 ({rsi:.1f}) - 조정 가능'))

    # 볼린저밴드
    bbl = next((c for c in df.columns if 'BBL_' in c), None)
    bbu = next((c for c in df.columns if 'BBU_' in c), None)
    if bbl and bbu:
        if float(last['Close']) <= float(last[bbl]) * 1.005:
            signals.append(('BUY',  '볼린저 밴드 하단 이탈 (과매도 극단)'))
        if float(last['Close']) >= float(last[bbu]) * 0.995:
            signals.append(('SELL', '볼린저 밴드 상단 이탈 (과매수 극단)'))

    # 골든/데드크로스
    s5, s20 = last.get('SMA_5'), last.get('SMA_20')
    p5, p20 = prev.get('SMA_5'), prev.get('SMA_20')
    if all(pd.notna(x) for x in [s5, s20, p5, p20]):
        if float(p5) <= float(p20) and float(s5) > float(s20):
            signals.append(('BUY',  '골든크로스 (5일 > 20일 돌파)'))
        elif float(p5) >= float(p20) and float(s5) < float(s20):
            signals.append(('SELL', '데드크로스 (5일 < 20일 이탈)'))

    # MACD 히스토그램 반전
    hist_col = next((c for c in df.columns if c.startswith('MACDh_')), None)
    if hist_col:
        h  = float(last.get(hist_col, 0) or 0)
        ph = float(prev.get(hist_col, 0) or 0)
        if h > 0 and ph <= 0:
            signals.append(('BUY',  'MACD 상향 반전 (모멘텀 전환)'))
        elif h < 0 and ph >= 0:
            signals.append(('SELL', 'MACD 하향 반전 (모멘텀 꺾임)'))

    # 거래량 급증
    vr = float(last.get('VOL_RATIO', 1) or 1)
    if vr >= 2.0:
        chg = (float(last['Close']) - float(prev['Close'])) / float(prev['Close']) * 100
        if chg > 0:
            signals.append(('BUY',  f'거래량 급증 ({vr:.1f}배) + 상승 {chg:+.1f}%'))
        else:
            signals.append(('SELL', f'거래량 급증 ({vr:.1f}배) + 하락 {chg:+.1f}%'))

    # 스토캐스틱 극단
    stk = next((c for c in df.columns if c.startswith('STOCHk_')), None)
    std = next((c for c in df.columns if c.startswith('STOCHd_')), None)
    if stk and std:
        k = float(last.get(stk, 50) or 50)
        d = float(last.get(std, 50) or 50)
        pk = float(prev.get(stk, 50) or 50)
        if k < 20 and k > d and pk <= d:   # 과매도 반전
            signals.append(('BUY',  f'스토캐스틱 과매도 반전 (K={k:.0f})'))
        elif k > 80 and k < d and pk >= d: # 과매수 반전
            signals.append(('SELL', f'스토캐스틱 과매수 반전 (K={k:.0f})'))

    return signals
