# -*- coding: utf-8 -*-
"""
SOXL/KORU 대비 국내 종목 베타 계산 + 시가 예상 등락 추정
- 미국 반도체 ETF(SOXL) 전일 등락 → 국내 반도체 종목 시가 예상
- 한국 3x ETF(KORU) 전일 등락 → 국내 종목 전반적 시가 예상
"""
import numpy as np
import pandas as pd
import json, os
from datetime import datetime

from agents.data_agent import get_domestic_data, get_foreign_data

BETA_CACHE = os.path.join(os.path.dirname(__file__), '..', 'beta_cache.json')
CACHE_TTL  = 24 * 3600  # 하루에 한 번 재계산

# ── 캐시 ──────────────────────────────────────────────────────

def _load_cache() -> dict:
    if not os.path.exists(BETA_CACHE):
        return {}
    try:
        with open(BETA_CACHE, encoding='utf-8') as f:
            d = json.load(f)
        updated = datetime.fromisoformat(d.get('_ts', '2000-01-01'))
        if (datetime.now() - updated).total_seconds() < CACHE_TTL:
            return d
    except:
        pass
    return {}

def _save_cache(d: dict):
    d['_ts'] = datetime.now().isoformat()
    with open(BETA_CACHE, 'w', encoding='utf-8') as f:
        json.dump(d, f, ensure_ascii=False, indent=2)

# ── 베타 계산 ─────────────────────────────────────────────────

def _norm_idx(s: pd.Series) -> pd.Series:
    """KR/US DatetimeIndex를 timezone-naive date 로 통일"""
    try:
        idx = pd.to_datetime(s.index).tz_localize(None)
    except TypeError:
        idx = pd.to_datetime(s.index).tz_convert(None)
    s = s.copy()
    s.index = idx.normalize()
    return s

def _beta_corr(a: pd.Series, b: pd.Series):
    """날짜 정규화 후 교집합 기준 베타/상관계수 계산"""
    a = _norm_idx(a)
    b = _norm_idx(b)
    combined = pd.concat([a.rename('a'), b.rename('b')], axis=1).dropna()
    if len(combined) < 15:
        return 0.0, 0.0
    v = combined['b'].var()
    if v == 0:
        return 0.0, 0.0
    beta = combined['a'].cov(combined['b']) / v
    corr = combined['a'].corr(combined['b'])
    return round(float(beta), 4), round(float(corr), 4)

def compute_betas(portfolio_cfg: dict, force: bool = False) -> dict:
    """전 종목 SOXL/KORU 베타 계산. 캐시 유효 시 재사용."""
    if not force:
        cached = _load_cache()
        if cached:
            return cached

    print("  [상관관계] ETF 기준 데이터 수집 중...", flush=True)
    soxl_df = get_foreign_data('SOXL', period='6mo')
    koru_df = get_foreign_data('KORU', period='6mo')
    if soxl_df.empty or koru_df.empty:
        return {}

    # SOXL은 3배 레버리지 → 실질 반도체 지수 수익률로 역산
    soxl_ret = soxl_df['Close'].pct_change().dropna()   # 3x raw
    koru_ret = koru_df['Close'].pct_change().dropna()   # 3x raw

    result = {}
    for item in portfolio_cfg.get('domestic', []):
        ticker, name = item['ticker'], item['name']
        try:
            df = get_domestic_data(ticker, period_days=180)
            if df.empty or len(df) < 20:
                continue
            ret = df['Close'].pct_change().dropna()
            sb, sc = _beta_corr(ret, soxl_ret)
            kb, kc = _beta_corr(ret, koru_ret)
            result[ticker] = {
                'name': name,
                'soxl_beta': sb, 'soxl_corr': sc,
                'koru_beta': kb, 'koru_corr': kc,
            }
            print(f"    {name}: β_SOXL={sb:.3f}(r={sc:.2f}) | β_KORU={kb:.3f}(r={kc:.2f})",
                  flush=True)
        except Exception as e:
            print(f"    [{name}] 오류: {e}", flush=True)

    if result:
        _save_cache(result)
    return result

def estimate_open_move(ticker: str, betas: dict,
                       soxl_chg: float, koru_chg: float) -> float:
    """
    SOXL/KORU 전일 등락(%) 기반 국내 종목 시가 예상 등락률(%) 계산.
    soxl_chg, koru_chg: 3x ETF 실제 등락% 입력
    """
    SEMI_TICKERS = {'000660', '005930', '042700', '011070', '036930'}

    if ticker in betas:
        b  = betas[ticker]
        sb = b.get('soxl_beta', 0.0)
        kb = b.get('koru_beta', 0.0)
        sc = abs(b.get('soxl_corr', 0.0))
        kc = abs(b.get('koru_corr', 0.0))
    else:
        # 기본값: 반도체 종목 vs 그 외
        sb = 0.12 if ticker in SEMI_TICKERS else 0.05
        kb = 0.20
        sc = 0.6  if ticker in SEMI_TICKERS else 0.3
        kc = 0.5

    soxl_contrib = sb * soxl_chg
    koru_contrib  = kb * koru_chg

    # 상관계수 크기로 가중 평균
    total_w = sc + kc + 1e-9
    expected = (soxl_contrib * sc + koru_contrib * kc) / total_w

    # 과도한 추정 방지: ±5% 클리핑
    return round(max(-5.0, min(5.0, expected)), 2)

def get_etf_overnight(cfg: dict) -> dict:
    """SOXL/KORU 최신 등락률 조회"""
    from agents.data_agent import get_current_price
    result = {}
    for item in cfg['portfolio']['foreign']:
        info = get_current_price(item['ticker'], False)
        if info:
            result[item['ticker']] = {
                'name':       item['name'],
                'price':      info['price'],
                'change_pct': float(info['change_pct']),
            }
    return result
