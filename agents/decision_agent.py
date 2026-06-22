# -*- coding: utf-8 -*-
"""의사결정 에이전트 - Claude API + 규칙 기반 fallback"""
import os

try:
    import anthropic
    CLAUDE_AVAILABLE = True
except ImportError:
    CLAUDE_AVAILABLE = False


def get_decision(name: str, price_info: dict, signals: list, news: list,
                 api_key: str = '', score_info: dict = None) -> str:
    key = api_key or os.environ.get('ANTHROPIC_API_KEY', '')
    if key and CLAUDE_AVAILABLE:
        try:
            return _claude_decision(name, price_info, signals, news, score_info, key)
        except Exception as e:
            print(f"[Claude] {name} 오류: {e}", flush=True)
    return _rule_based_decision(name, price_info, signals, score_info)


def _claude_decision(name, price_info, signals, news, score_info, api_key):
    score    = score_info.get('score', 0) if score_info else 0
    adx      = score_info.get('adx', 0) if score_info else 0
    trend    = score_info.get('trend_strength', '') if score_info else ''
    details  = score_info.get('details', {}) if score_info else {}

    sig_lines  = '\n'.join(f"  [{s[0]}] {s[1]}" for s in signals) or '  없음'
    news_lines = '\n'.join(
        f"  ({n['sentiment']['label']}) {n['title'][:60]} [{n['age_hours']:.0f}h전]"
        for n in news[:4]
    ) if news else '  없음'

    detail_lines = '\n'.join(
        f"  {k}: {v}" for k, v in details.items()
    )

    prompt = f"""당신은 냉철한 퀀트 투자 분석가입니다. 수익을 위해 데이터만 보고 판단합니다.

종목: {name}
현재가: {price_info.get('price', '?')} | 등락: {price_info.get('change_pct', 0):+.2f}%
종합점수: {score:+d}/100 | ADX: {adx:.1f} ({trend})

지표 상세:
{detail_lines}

기술적 신호:
{sig_lines}

최신 뉴스:
{news_lines}

아래 형식으로 정확히 2줄만 작성. 군더더기 없이.
판단: [BUY/HOLD/SELL]
근거: 핵심 이유 1~2가지, 40자 이내"""

    client = anthropic.Anthropic(api_key=api_key)
    msg = client.messages.create(
        model='claude-haiku-4-5-20251001',
        max_tokens=100,
        temperature=0.3,
        messages=[{'role': 'user', 'content': prompt}]
    )
    return msg.content[0].text.strip()


def _rule_based_decision(name, price_info, signals, score_info=None) -> str:
    score  = score_info.get('score', 0) if score_info else 0
    trend  = score_info.get('trend_strength', '') if score_info else ''
    adx    = score_info.get('adx', 0) if score_info else 0
    buys   = [s for s in signals if s[0] == 'BUY']
    sells  = [s for s in signals if s[0] == 'SELL']

    # 횡보 구간 경고
    trend_note = f" (주의: {trend})" if adx < 20 else ""

    if score >= 50 or len(buys) >= 2:
        action  = 'BUY'
        reasons = ' + '.join(b[1][:20] for b in buys[:2]) or f"점수 {score:+d}"
    elif score <= -50 or len(sells) >= 2:
        action  = 'SELL'
        reasons = ' + '.join(s[1][:20] for s in sells[:2]) or f"점수 {score:+d}"
    elif buys:
        action  = 'HOLD'
        reasons = f"{buys[0][1][:25]} (추가 확인 필요)"
    elif sells:
        action  = 'HOLD'
        reasons = f"{sells[0][1][:25]} (추가 확인 필요)"
    else:
        action  = 'HOLD'
        reasons = f"신호 없음 (점수 {score:+d})"

    return f"판단: [{action}]{trend_note}\n근거: {reasons}"
