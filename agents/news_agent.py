# -*- coding: utf-8 -*-
"""뉴스 수집 + 감성 분석 에이전트"""
import feedparser
import requests
from datetime import datetime, timezone
import hashlib, json, os, re

SEEN_FILE = os.path.join(os.path.dirname(__file__), '..', 'seen_news.json')

# 감성 키워드
POS_WORDS = ['상승', '급등', '호실적', '매수', '신고가', '돌파', '수주', '흑자', '호재',
             '반등', '강세', '성장', '증가', '확대', '목표가 상향', 'beat', 'surge',
             'rally', 'upgrade', 'bullish', 'record', 'outperform']
NEG_WORDS = ['하락', '급락', '실적 부진', '매도', '신저가', '붕괴', '리콜', '적자', '악재',
             '약세', '감소', '축소', '목표가 하향', 'miss', 'plunge', 'downgrade',
             'bearish', 'underperform', 'loss', 'cut', '우려', '위기', '경고']

def _load_seen() -> set:
    if os.path.exists(SEEN_FILE):
        with open(SEEN_FILE, encoding='utf-8') as f:
            return set(json.load(f))
    return set()

def _save_seen(seen: set):
    with open(SEEN_FILE, 'w', encoding='utf-8') as f:
        json.dump(list(seen)[-500:], f)

def _news_id(title: str) -> str:
    return hashlib.md5(title.encode()).hexdigest()

def _sentiment(title: str) -> dict:
    """제목 기반 감성 점수 (-1: 부정, 0: 중립, +1: 긍정)"""
    t = title.lower()
    pos = sum(1 for w in POS_WORDS if w.lower() in t)
    neg = sum(1 for w in NEG_WORDS if w.lower() in t)
    if pos > neg:
        return {'label': '긍정', 'score': +1}
    elif neg > pos:
        return {'label': '부정', 'score': -1}
    return {'label': '중립', 'score': 0}

def fetch_google_news(query: str, lang: str = 'ko', max_items: int = 5,
                      hours_limit: int = 24) -> list:
    """Google News RSS 수집 + 감성 분석"""
    hl = 'ko' if lang == 'ko' else 'en'
    ceid = 'KR:ko' if lang == 'ko' else 'US:en'
    url = (f"https://news.google.com/rss/search"
           f"?q={requests.utils.quote(query)}&hl={hl}&gl=KR&ceid={ceid}")
    try:
        feed = feedparser.parse(url)
        results = []
        for entry in feed.entries[:max_items * 2]:   # 여유있게 가져와서 필터
            pub = entry.get('published_parsed')
            if pub:
                pub_dt    = datetime(*pub[:6], tzinfo=timezone.utc)
                age_hours = (datetime.now(timezone.utc) - pub_dt).total_seconds() / 3600
            else:
                age_hours = 999

            if age_hours > hours_limit:
                continue

            title = entry.get('title', '').split(' - ')[0].strip()
            sent  = _sentiment(title)
            results.append({
                'title':     title,
                'link':      entry.get('link', ''),
                'age_hours': round(age_hours, 1),
                'fresh':     age_hours < 6,
                'sentiment': sent,
            })
            if len(results) >= max_items:
                break
        return results
    except Exception as e:
        print(f"[뉴스] '{query}' 오류: {e}")
        return []

def get_portfolio_news(portfolio: list, hours_limit: int = 12) -> dict:
    """포트폴리오 전 종목 뉴스 + 중복 제거"""
    seen = _load_seen()
    all_news = {}

    for item in portfolio:
        name   = item['name']
        ticker = item['ticker']
        query  = f"{name} 주가" if len(ticker) == 6 else f"{ticker} stock"

        articles     = fetch_google_news(query, hours_limit=hours_limit)
        new_articles = []
        for art in articles:
            nid = _news_id(art['title'])
            if nid not in seen:
                new_articles.append(art)
                seen.add(nid)

        if new_articles:
            all_news[name] = new_articles

    _save_seen(seen)
    return all_news

def get_market_events() -> list:
    """국내외 주요 경제 일정 뉴스 수집 (48시간 이내)"""
    queries = [
        ("FOMC 금리 결정 발표 일정", 'en'),
        ("한국은행 금통위 기준금리 결정", 'ko'),
        ("미국 CPI 소비자물가 발표", 'ko'),
        ("미국 고용지표 실업률 비농업 발표", 'ko'),
        ("GDP 성장률 발표 경제지표", 'ko'),
        ("증시 주요 일정 이번주", 'ko'),
    ]
    events = []
    seen = set()
    for query, lang in queries:
        news = fetch_google_news(query, lang=lang, max_items=2, hours_limit=48)
        for n in news:
            nid = _news_id(n['title'])
            if nid not in seen:
                seen.add(nid)
                events.append(n)
    return events


# 경제 일정 → 증시 영향 키워드 매핑
_EVENT_IMPACT = {
    'FOMC':    ('🏦 FOMC 금리 결정', '금리 동결→호재 / 인상→악재'),
    '금통위':   ('🏦 한국은행 금통위',  '금리 동결→호재 / 인상→악재'),
    'CPI':     ('📊 CPI 물가지수',    '예상치 하회→호재 / 상회→악재'),
    '고용':     ('👷 고용지표',        '호조→증시 혼재 / 부진→악재'),
    '실업률':   ('👷 실업률 발표',     '하락→호재 / 상승→악재'),
    'GDP':     ('📈 GDP 성장률',      '예상치 상회→호재 / 하회→악재'),
    '비농업':   ('👷 비농업 고용',     '호조→증시 혼재 / 부진→악재'),
    '기준금리':  ('🏦 기준금리 결정',  '동결→호재 / 인상→악재'),
}

def classify_event_impact(title: str) -> str:
    """뉴스 제목에서 경제 이벤트 종류와 증시 영향 분류"""
    for keyword, (label, impact) in _EVENT_IMPACT.items():
        if keyword.lower() in title.lower():
            return f"{label} — {impact}"
    return ''


def get_surge_candidates(hours_limit: int = 12) -> list:
    """시장 큰 뉴스 기반 급등 예상 종목 후보 수집"""
    queries = [
        ("주식 급등 호재 오늘 뉴스", 'ko'),
        ("코스피 급등 종목 수급 뉴스", 'ko'),
        ("반도체 바이오 급등 이슈 오늘", 'ko'),
        ("stock surge catalyst earnings today", 'en'),
        ("semiconductor AI rally news today", 'en'),
    ]
    candidates = []
    seen = set()
    for query, lang in queries:
        news = fetch_google_news(query, lang=lang, max_items=3, hours_limit=hours_limit)
        for n in news:
            if n['sentiment']['score'] > 0:   # 긍정 뉴스만
                nid = _news_id(n['title'])
                if nid not in seen:
                    seen.add(nid)
                    candidates.append(n)
    return candidates[:6]


def get_sentiment_summary(news_list: list) -> dict:
    """뉴스 목록의 감성 요약"""
    if not news_list:
        return {'label': '중립', 'score': 0, 'pos': 0, 'neg': 0}
    pos = sum(1 for n in news_list if n['sentiment']['score'] > 0)
    neg = sum(1 for n in news_list if n['sentiment']['score'] < 0)
    total = len(news_list)
    net   = pos - neg
    if net > 0:
        label = '긍정적'
    elif net < 0:
        label = '부정적'
    else:
        label = '중립'
    return {'label': label, 'score': net, 'pos': pos, 'neg': neg, 'total': total}
