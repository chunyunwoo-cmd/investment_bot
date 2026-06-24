# -*- coding: utf-8 -*-
"""
playwright_agent.py
네이버 금융 / 야후 파이낸스에서 실시간 시세 + 뉴스 스크래핑
pykrx KRX 로그인 없이 국내 종목 현재가 획득 가능
"""
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
import re, time

NAVER_STOCK_URL = "https://finance.naver.com/item/main.naver?code={ticker}"
YAHOO_URL       = "https://finance.yahoo.com/quote/{ticker}/"
NAVER_NEWS_URL  = "https://finance.naver.com/news/news_search.naver?query={query}&sm=title_lqy&jos=0&period=0&hits=10"


def _launch():
    pw  = sync_playwright().start()
    br  = pw.chromium.launch(headless=True, args=["--no-sandbox", "--disable-gpu"])
    ctx = br.new_context(
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        locale="ko-KR",
        java_script_enabled=True,
    )
    return pw, br, ctx


def get_naver_price(ticker: str) -> dict:
    """네이버 금융에서 국내 종목 실시간 현재가 조회 (6자리 종목코드)"""
    pw = br = ctx = None
    try:
        pw, br, ctx = _launch()
        page = ctx.new_page()
        page.goto(NAVER_STOCK_URL.format(ticker=ticker), timeout=15000)
        page.wait_for_load_state("domcontentloaded", timeout=10000)

        # 현재가 (.no_today em span.blind)
        price = 0
        for sel in [".no_today .blind", ".today_present .blind", "#chart_area .today .blind"]:
            el = page.query_selector(sel)
            if el:
                txt = el.inner_text().strip().replace(",", "")
                if txt.isdigit():
                    price = int(txt)
                    break

        # 등락률 (.no_exday em.rate .blind)
        change_pct = 0.0
        rate_el = page.query_selector(".no_exday .rate .blind")
        if not rate_el:
            rate_el = page.query_selector(".no_exday em:last-child .blind")
        if rate_el:
            txt = rate_el.inner_text().strip().replace("%", "").replace(",", "")
            try:
                change_pct = float(txt)
            except ValueError:
                pass

        # 상승/하락 방향
        if page.query_selector(".no_exday .down, .no_exday .nv"):
            change_pct = -abs(change_pct)
        else:
            change_pct = abs(change_pct)

        # 거래량
        vol = 0
        trs = page.query_selector_all(".no_info tr")
        for tr in trs:
            th = tr.query_selector("th")
            td = tr.query_selector("td")
            if th and td and "거래량" in th.inner_text():
                vol_text = td.inner_text().strip().replace(",", "")
                try:
                    vol = int(vol_text)
                except ValueError:
                    pass
                break

        if price == 0:
            return {}

        prev_close = round(price / (1 + change_pct / 100)) if change_pct != 0 else price
        return {
            "ticker":     ticker,
            "price":      price,
            "prev_close": prev_close,
            "change_pct": round(change_pct, 2),
            "volume":     vol,
            "source":     "naver",
        }
    except PWTimeout:
        return {}
    except Exception as e:
        print(f"[playwright] 네이버 {ticker} 오류: {e}", flush=True)
        return {}
    finally:
        if ctx: ctx.close()
        if br:  br.close()
        if pw:  pw.stop()


def get_yahoo_price(ticker: str) -> dict:
    """야후 파이낸스에서 해외 종목 실시간 현재가 조회"""
    pw = br = ctx = None
    try:
        pw, br, ctx = _launch()
        page = ctx.new_page()
        page.goto(YAHOO_URL.format(ticker=ticker), timeout=20000)
        page.wait_for_load_state("domcontentloaded", timeout=15000)
        time.sleep(2)

        price = 0.0
        change_pct = 0.0

        # 현재가 — 여러 셀렉터 순서대로 시도
        for sel in [
            '[data-testid="qsp-price"]',
            'fin-streamer[data-field="regularMarketPrice"]',
            'span[data-reactid*="price"]',
            'div[data-field="regularMarketPrice"]',
        ]:
            el = page.query_selector(sel)
            if el:
                txt = el.inner_text().strip().replace(",", "")
                try:
                    price = float(txt)
                    break
                except ValueError:
                    pass

        # 등락률
        for sel in [
            '[data-testid="qsp-price-change-percent"]',
            'fin-streamer[data-field="regularMarketChangePercent"]',
        ]:
            el = page.query_selector(sel)
            if el:
                txt = el.inner_text().strip().replace("(", "").replace(")", "").replace("%", "").replace("+", "")
                try:
                    change_pct = float(txt)
                    break
                except ValueError:
                    pass

        # 못 찾으면 page.content()에서 정규식으로 추출
        if price == 0.0:
            content = page.content()
            m = re.search(r'"regularMarketPrice"[:\s,{"]*"raw"[:\s]*([0-9.]+)', content)
            if m:
                price = float(m.group(1))
            m2 = re.search(r'"regularMarketChangePercent"[:\s,{"]*"raw"[:\s]*([-0-9.]+)', content)
            if m2:
                change_pct = float(m2.group(1))

        if price == 0.0:
            return {}

        prev_close = round(price / (1 + change_pct / 100), 2) if change_pct != 0 else price
        return {
            "ticker":     ticker,
            "price":      round(price, 2),
            "prev_close": round(prev_close, 2),
            "change_pct": round(change_pct, 2),
            "source":     "yahoo_pw",
        }
    except PWTimeout:
        return {}
    except Exception as e:
        print(f"[playwright] 야후 {ticker} 오류: {e}", flush=True)
        return {}
    finally:
        if ctx: ctx.close()
        if br:  br.close()
        if pw:  pw.stop()


def get_naver_news(query: str, max_items: int = 5) -> list:
    """네이버 금융 뉴스 검색 스크래핑"""
    pw = br = ctx = None
    results = []
    try:
        pw, br, ctx = _launch()
        page = ctx.new_page()
        page.goto(NAVER_NEWS_URL.format(query=query), timeout=15000)
        page.wait_for_load_state("domcontentloaded", timeout=10000)

        items = page.query_selector_all(".articleSubject a")
        for el in items[:max_items]:
            title = el.inner_text().strip()
            href  = el.get_attribute("href") or ""
            if title:
                results.append({"title": title, "url": href})
        return results
    except Exception as e:
        print(f"[playwright] 네이버 뉴스 오류: {e}", flush=True)
        return []
    finally:
        if ctx: ctx.close()
        if br:  br.close()
        if pw:  pw.stop()


def get_realtime_koru_mu() -> dict:
    """KORU + MU 실시간 시세 동시 조회 (야후 파이낸스)"""
    results = {}
    for ticker in ["KORU", "MU", "SOXL"]:
        data = get_yahoo_price(ticker)
        if data:
            results[ticker] = data
    return results


if __name__ == "__main__":
    print("=== KORU / MU / SOXL 실시간 시세 ===")
    data = get_realtime_koru_mu()
    for tk, d in data.items():
        print(f"{tk}: ${d['price']:.2f}  ({d['change_pct']:+.2f}%)  [출처: {d['source']}]")

    print("\n=== 삼성전자 네이버 시세 ===")
    d = get_naver_price("005930")
    if d:
        print(f"삼성전자: {d['price']:,}원  ({d['change_pct']:+.2f}%)  거래량: {d['volume']:,}")

    print("\n=== 네이버 뉴스: 반도체 ===")
    news = get_naver_news("반도체 주가")
    for n in news:
        print(f"  • {n['title']}")
