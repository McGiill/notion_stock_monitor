import argparse
import json
import os
import time
import urllib.request
from datetime import datetime
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
from notion_client import Client
import FinanceDataReader as fdr
import yfinance as yf

load_dotenv()
NOTION_TOKEN = os.environ["NOTION_TOKEN"]
DATABASE_ID = os.environ["NOTION_DATABASE_ID"]
HISTORY_DATABASE_ID = os.environ["NOTION_HISTORY_DATABASE_ID"]
PRICE_DATABASE_ID = os.environ.get("NOTION_PRICE_DATABASE_ID")  # 선택 (없으면 가격기록 생략)
DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL")     # 선택 (없으면 알림 생략)

notion = Client(auth=NOTION_TOKEN)
KST = ZoneInfo("Asia/Seoul")

# 보유종목 DB 컬럼
COL_NAME = "종목명"
COL_CODE = "종목코드"
COL_QTY = "수량"
COL_AVG = "평단가"
COL_CCY = "통화"
COL_PRICE = "현재가"
COL_CHANGE = "전일대비"
COL_VALUE = "평가금액"
COL_WEIGHT = "비중"
COL_RETURN = "수익률"
COL_PNL = "손익"
COL_STATUS = "상태"
COL_UPDATED = "갱신시각"

# 자산기록 DB 컬럼
H_TITLE = "기록"
H_DATE = "일자"
H_TOTAL_VALUE = "총평가금액"
H_TOTAL_COST = "총매입금액"
H_TOTAL_PNL = "총손익"
H_TOTAL_RETURN = "총수익률"

# 가격기록 DB 컬럼 (종목별 가격 추이)
P_TITLE = "기록"
P_DATE = "일자"
P_STOCK = "종목"
P_PRICE = "현재가"
P_VALUE = "평가금액"

RETRY = 3  # 가격/환율 조회 재시도 횟수

def is_korean_stock(code):
    return code.isdigit() and len(code) == 6

def get_fx_usdkrw():
    """USD/KRW 환율(종가)"""
    for attempt in range(RETRY):
        try:
            df = fdr.DataReader("USD/KRW")
            if df is not None and not df.empty:
                return float(df["Close"].iloc[-1])
        except Exception as e:
            print(f"  ! 환율 조회 실패({attempt + 1} / {RETRY}): {e}")
        time.sleep(2 * (attempt+1))
    return None

def get_price(code):
    """(현재가, 전일종가) 반환. 재시도 포함"""
    code = code.strip()
    for attempt in range(RETRY):
        try:
            if is_korean_stock(code):
                df = fdr.DataReader(code)
            else:
                df = yf.Ticker(code).history(period="7d")
            closes = df["Close"].dropna() if df is not None else None
            if closes is not None and len(closes) >= 1:
                current = float(closes.iloc[-1])
                prev = float(closes.iloc[-2]) if len(closes) >= 2 else current
                return current, prev
        except Exception as e:
            print(f"  ! 가격 조회 실패 {code} ({attempt + 1} / {RETRY}): {e}")
        time.sleep(2*(attempt+1))
    return None, None

# ── 노션 헬퍼 ─────────────────────────────────────────────
def get_number(prop):
    return prop.get("number") if prop else None

def get_text(prop):
    if not prop:
        return ""
    rt = prop.get("rich_text") or prop.get("title") or []
    return "".join(t["plain_text"] for t in rt)

def fetch_rows(database_id, **kwargs):
    """databases.query 페이지네이션 처리."""
    results, cursor = [], None
    while True:
        if cursor:
            resp = notion.databases.query(database_id=database_id, start_cursor=cursor, **kwargs)
        else:
            resp = notion.databases.query(database_id=database_id, **kwargs)
        results.extend(resp["results"])
        if not resp.get("has_more"):
            break
        cursor = resp["next_cursor"]
    return results

def update_holding(page_id, ccy, price, change_pct, value, weight, ret, pnl, status, now_str):
    notion.pages.update(page_id=page_id, properties={
        COL_CCY: {"select": {"name": ccy}},
        COL_PRICE: {"number": round(price, 2)},
        COL_CHANGE: {"number": round(change_pct / 100, 4)},
        COL_VALUE: {"number": round(value)},
        COL_WEIGHT: {"number": round(weight / 100, 4)},
        COL_RETURN: {"number": round(ret / 100, 4)},
        COL_PNL: {"number": round(pnl)},
        COL_STATUS: {"select": {"name": status}},
        COL_UPDATED: {"rich_text": [{"text": {"content": now_str}}]},
    })

def upsert_history(today, total_value, total_cost, total_pnl, total_return):
    """오늘 날짜 행이 있으면 갱신, 없으면 새로 추가."""
    props = {
        H_DATE: {"date": {"start": today}},
        H_TOTAL_VALUE: {"number": round(total_value)},
        H_TOTAL_COST: {"number": round(total_cost)},
        H_TOTAL_PNL: {"number": round(total_pnl)},
        H_TOTAL_RETURN: {"number": round(total_return / 100, 4)},
    }
    existing = fetch_rows(
        HISTORY_DATABASE_ID,
        filter={"property": H_DATE, "date": {"equals": today}},
    )
    if existing:
        notion.pages.update(page_id=existing[0]["id"], properties=props)
    else:
        notion.pages.create(
            parent={"database_id": HISTORY_DATABASE_ID},
            properties={H_TITLE: {"title": [{"text": {"content": today}}]}, **props},
        )

def upsert_price(today, name, price, value):
    """가격기록 DB에 (오늘 날짜 + 종목) 한 행. 같은 날 같은 종목이면 갱신."""
    props = {
        P_DATE: {"date": {"start": today}},
        P_STOCK: {"select": {"name": name}},
        P_PRICE: {"number": round(price)},
        P_VALUE: {"number": round(value)},
    }
    # 날짜로만 필터 (Select는 없는 옵션으로 필터하면 400 에러 → 종목 매칭은 파이썬에서)
    today_rows = fetch_rows(
        PRICE_DATABASE_ID,
        filter={"property": P_DATE, "date": {"equals": today}},
    )
    match = None
    for row in today_rows:
        sel = row["properties"].get(P_STOCK, {}).get("select")
        if sel and sel.get("name") == name:
            match = row
            break

    if match:
        notion.pages.update(page_id=match["id"], properties=props)
    else:
        notion.pages.create(
            parent={"database_id": PRICE_DATABASE_ID},
            properties={P_TITLE: {"title": [{"text": {"content": f"{today} {name}"}}]}, **props},
        )

def send_discord(message):
    """디스코드 웹훅으로 메시지 전송."""
    if not DISCORD_WEBHOOK_URL:
        print("디스코드 웹훅 미설정 — 알림 생략")
        return
    data = json.dumps({"content": message}).encode("utf-8")
    req = urllib.request.Request(
        DISCORD_WEBHOOK_URL, data=data,
        headers={"Content-Type": "application/json", "User-Agent": "notion-stock-monitor"},
    )
    try:
        urllib.request.urlopen(req, timeout=10)
        print("디스코드 알림 전송 완료.")
    except Exception as e:
        print(f"  ! 디스코드 전송 실패: {e}")

def main():
    now = datetime.now(KST)
    now_str = now.strftime("%Y-%m-%d %H:%M")
    today = now.strftime("%Y-%m-%d")

    fx = get_fx_usdkrw()
    if fx is None:
        print("환율 조회 실패 - 해외 주식 원화 환산 불가. 중단.")
        return
    print(f"USD/KRW = {fx:,.2f}\n")

    rows = fetch_rows(DATABASE_ID)

    # 1단계: 종목별 계산만 하고 결과를 모은다 (비중은 전체 합계가 나와야 구할 수 있음)
    holdings = []
    total_value = total_cost = 0.0

    for row in rows:
        p = row["properties"]
        name = get_text(p.get(COL_NAME))
        code = get_text(p.get(COL_CODE))
        qty = get_number(p.get(COL_QTY))
        avg = get_number(p.get(COL_AVG))

        if not code or qty is None or avg is None:
            print(f"[건너뜀] {name or '(이름없음)'}: 입력값 누락")
            continue

        price, prev = get_price(code)
        if price is None:
            print(f"[실패] {name} ({code}): 현재가 못 가져옴")
            continue

        kr = is_korean_stock(code)
        rate = 1.0 if kr else fx

        # 현재가·전일가를 원화로 환산, 평단가는 원화로 입력했으므로 그대로 사용
        price_krw = price * rate
        prev_krw = prev * rate

        value_krw = price_krw * qty
        cost_krw = avg * qty
        pnl_krw = value_krw - cost_krw
        ret = (price_krw - avg) / avg * 100 if avg else 0.0
        change_pct = (price_krw - prev_krw) / prev_krw * 100 if prev_krw else 0.0
        status = "📈 수익" if ret >= 0 else "📉 손실"

        holdings.append({
            "id": row["id"], "name": name, "code": code,
            "price_krw": price_krw, "change_pct": change_pct,
            "value_krw": value_krw, "ret": ret, "pnl_krw": pnl_krw, "status": status,
        })
        total_value += value_krw
        total_cost += cost_krw

    # 2단계: 전체 합계가 나왔으니 비중을 구해 노션에 기록
    for h in holdings:
        weight = (h["value_krw"] / total_value * 100) if total_value else 0.0
        update_holding(h["id"], "KRW", h["price_krw"], h["change_pct"],
                       h["value_krw"], weight, h["ret"], h["pnl_krw"], h["status"], now_str)
        if PRICE_DATABASE_ID:
            upsert_price(today, h["name"], h["price_krw"], h["value_krw"])
        print(f"[완료] {h['name']} ({h['code']}): {h['price_krw']:,.0f}원 | "
              f"{h['ret']:+.2f}% | 비중 {weight:.1f}% | 전일 {h['change_pct']:+.2f}%")
        time.sleep(0.4)

    total_pnl = total_value - total_cost
    total_return = (total_pnl / total_cost * 100) if total_cost else 0.0

    print(f"\n── 전체 ── 평가 {total_value:,.0f}원 | 손익 {total_pnl:+,.0f}원 | 수익률 {total_return:+.2f}%")

    if args.history:
        upsert_history(today, total_value, total_cost, total_pnl, total_return)
        print("자산기록 갱신 완료.")
    else:
        print("자산기록 생략 (월 1회 history 워크플로에서만 기록)")

    if args.notify and holdings:
        top = max(holdings, key=lambda h: h["change_pct"])      # 전일 대비 최고 상승
        bottom = min(holdings, key=lambda h: h["change_pct"])   # 전일 대비 최고 하락
        msg = (
            f"📊 **장마감 포트폴리오** ({now_str})\n"
            f"💱 환율(USD/KRW): {fx:,.2f}원\n"
            f"💰 총 평가금액: {total_value:,.0f}원 ({total_return:+.2f}%)\n"
            f"💵 총 손익: {total_pnl:+,.0f}원\n"
            f"📈 최고 상승: {top['name']} ({top['change_pct']:+.2f}%)\n"
            f"📉 최고 하락: {bottom['name']} ({bottom['change_pct']:+.2f}%)"
        )
        send_discord(msg)

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--history", action="store_true", help="자산기록 DB에 오늘 총자산 기록")
    parser.add_argument("--notify", action="store_true", help="디스코드로 장마감 요약 전송")
    args = parser.parse_args()
    main()