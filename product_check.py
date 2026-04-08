import os
import base64
import time
from datetime import datetime, timedelta, timezone
from collections import defaultdict

import bcrypt
import requests
from dotenv import load_dotenv

load_dotenv()

NAVER_CLIENT_ID     = os.getenv("NAVER_CLIENT_ID")
NAVER_CLIENT_SECRET = os.getenv("NAVER_CLIENT_SECRET")
BASE_URL = "https://api.commerce.naver.com"
KST      = timezone(timedelta(hours=9))

TARGET_NAME    = "루카랩x링노트 캔뱃지 바인더 앨범"
SEARCH_FROM    = datetime(2026, 1, 1, 0, 0, 0, tzinfo=KST)
SEARCH_TO      = datetime(2026, 3, 19, 23, 59, 59, tzinfo=KST)


def get_access_token():
    timestamp = str(int(time.time() * 1000))
    secret    = NAVER_CLIENT_SECRET.strip()
    message   = f"{NAVER_CLIENT_ID}_{timestamp}"
    hashed    = bcrypt.hashpw(message.encode("utf-8"), secret.encode("utf-8"))
    signature = base64.b64encode(hashed).decode("utf-8")
    resp = requests.post(
        f"{BASE_URL}/external/v1/oauth2/token",
        data={
            "client_id":          NAVER_CLIENT_ID,
            "timestamp":          timestamp,
            "client_secret_sign": signature,
            "grant_type":         "client_credentials",
            "type":               "SELF",
            "account_id":         os.getenv("NAVER_ACCOUNT_ID"),
        }, timeout=10,
    )
    resp.raise_for_status()
    return resp.json()["access_token"]


def get_order_ids(token, from_dt, to_dt):
    headers  = {"Authorization": f"Bearer {token}"}
    all_ids  = []
    page     = 1
    while True:
        resp = requests.get(
            f"{BASE_URL}/external/v1/pay-order/seller/product-orders",
            headers=headers,
            params={
                "from":     from_dt.isoformat(timespec="milliseconds"),
                "to":       to_dt.isoformat(timespec="milliseconds"),
                "pageNum":  page,
                "pageSize": 300,
            }, timeout=15,
        )
        if not resp.ok:
            print(f"  ⚠️ 조회 실패: {resp.text[:200]}")
            break
        data  = resp.json().get("data", {})
        items = data.get("contents", data.get("productOrders", []))
        if not items:
            break
        all_ids.extend(item.get("productOrderId") for item in items if item.get("productOrderId"))
        if len(items) < 300:
            break
        page += 1
    return list(set(all_ids))


def get_order_details(token, ids):
    if not ids:
        return []
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    result  = []
    for i in range(0, len(ids), 300):
        chunk = ids[i:i+300]
        resp  = requests.post(
            f"{BASE_URL}/external/v1/pay-order/seller/product-orders/query",
            headers=headers, json={"productOrderIds": chunk}, timeout=15,
        )
        resp.raise_for_status()
        result.extend(resp.json().get("data", []))
    return result


def main():
    print("🔑 네이버 API 인증 중...")
    token = get_access_token()
    print("✅ 인증 성공")

    # 1일씩 나눠서 조회 (API 최대 24시간 제한)
    all_ids = []
    cursor  = SEARCH_FROM
    while cursor < SEARCH_TO:
        chunk_end = min(
            datetime(cursor.year, cursor.month, cursor.day, 23, 59, 59, tzinfo=KST),
            SEARCH_TO
        )
        print(f"📦 {cursor.strftime('%m/%d')} 조회 중...", end=" ")
        ids = get_order_ids(token, cursor, chunk_end)
        print(f"{len(ids)}건")
        all_ids.extend(ids)
        cursor = datetime(cursor.year, cursor.month, cursor.day, tzinfo=KST) + timedelta(days=1)

    all_ids = list(set(all_ids))
    print(f"\n📋 전체 주문 {len(all_ids)}건 상세조회 중...")
    orders = get_order_details(token, all_ids)

    # 제품 필터링
    total_qty    = 0
    total_amount = 0
    matched      = []

    for wrap in orders:
        o      = wrap.get("productOrder", wrap)
        status = o.get("productOrderStatus", "")
        if status in ("CANCELED", "RETURNED", "EXCHANGED"):
            continue
        name = o.get("productName", "")
        if TARGET_NAME in name:
            qty = int(o.get("quantity", 1))
            amt = int(o.get("totalPaymentAmount", 0))
            total_qty    += qty
            total_amount += amt
            matched.append({"name": name, "qty": qty, "amount": amt})

    print(f"\n{'━'*55}")
    print(f"  제품: {TARGET_NAME}")
    print(f"  기간: 2026-01-01 ~ 2026-03-19")
    print(f"{'━'*55}")
    print(f"  총 판매 수량: {total_qty}개")
    print(f"  총 판매 금액: ₩{total_amount:,}")
    print(f"{'━'*55}")


if __name__ == "__main__":
    main()
