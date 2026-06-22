# -*- coding: utf-8 -*-
"""알림 에이전트 - 텔레그램 Bot"""
import requests

def send_telegram(message: str, bot_token: str, chat_id: str) -> bool:
    """텔레그램 메시지 전송"""
    if not bot_token or not chat_id:
        print(f"[알림 미전송 - 텔레그램 미설정]\n{message}\n")
        return False
    try:
        url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
        resp = requests.post(url, json={
            'chat_id': chat_id,
            'text': message,
            'parse_mode': 'Markdown',
        }, timeout=10)
        return resp.status_code == 200
    except Exception as e:
        print(f"[텔레그램] 전송 오류: {e}")
        return False

def format_brief(results: list, title: str = "📊 시장 브리핑") -> str:
    """전체 분석 결과를 알림 메시지로 포맷"""
    from datetime import datetime
    now = datetime.now().strftime('%m/%d %H:%M')
    lines = [f"*{title}* ({now})\n{'─'*25}"]

    for r in results:
        name      = r.get('name', '')
        price     = r.get('price', 'N/A')
        change    = r.get('change_pct', 0)
        decision  = r.get('decision', '')
        signals   = r.get('signals', [])

        emoji = '🔴' if change < -1 else ('🟢' if change > 1 else '⚪')
        sig_str = ' | '.join(f"{s[0]}" for s in signals[:2]) if signals else ''

        lines.append(f"{emoji} *{name}*  {price:,} ({change:+.2f}%)")
        if sig_str:
            lines.append(f"   📈 {sig_str}")
        if decision:
            lines.append(f"   💬 {decision}")
        lines.append("")

    return '\n'.join(lines)
