import sys
import os
import base64
import time
from datetime import datetime, timedelta, timezone
from collections import defaultdict

sys.stdout.reconfigure(encoding='utf-8')

import bcrypt
import requests
from dotenv import load_dotenv

load_dotenv()

NAVER_CLIENT_ID     = os.getenv("NAVER_CLIENT_ID")
NAVER_CLIENT_SECRET = os.getenv("NAVER_CLIENT_SECRET")
BASE_URL = "https://api.commerce.naver.com"
KST      = timezone(timedelta(hours=9))

TARGET_KEYWORDS = [
    "스크랩 더 모먼트 노트",
    "스크랩 더 모먼트 실버",
    "기록책",
]

PERIODS = [
    ("25년 1~3월", datetime(2025, 1, 1, 0, 0, 0, tzinfo=KST), datetime(2025, 3, 31, 23, 59, 59, tzinfo=KST)),
    ("26년 1~3월", datetime(2026, 1, 1, 0, 0, 0, tzinfo=KST), datetime(2026, 3, 24, 23, 59, 59, tzinfo=KST)),
]


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


def get_order_ids(token, from_dt, to_dt, retries=3):
    headers = {"Authorization": f"Bearer {token}"}
    all_ids, page = [], 1
    while True:
        for attempt in range(retries):
            try:
                resp = requests.get(
                    f"{BASE_URL}/external/v1/pay-order/seller/product-orders",
                    headers=headers,
                    params={
                        "from":     from_dt.isoformat(timespec="milliseconds"),
                        "to":       to_dt.isoformat(timespec="milliseconds"),
                        "pageNum":  page,
                        "pageSize": 300,
                    }, timeout=30,
                )
                break
            except Exception as e:
                if attempt == retries - 1:
                    print(f"    재시도 실패: {e}", flush=True)
                    return list(set(all_ids))
                time.sleep(2)
        if not resp.ok:
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
    headers, result = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}, []
    for i in range(0, len(ids), 300):
        chunk = ids[i:i+300]
        resp  = requests.post(
            f"{BASE_URL}/external/v1/pay-order/seller/product-orders/query",
            headers=headers, json={"productOrderIds": chunk}, timeout=15,
        )
        resp.raise_for_status()
        result.extend(resp.json().get("data", []))
    return result


def collect_period(token, label, from_dt, to_dt):
    all_ids = []
    cursor  = from_dt
    while cursor <= to_dt:
        chunk_end = min(
            datetime(cursor.year, cursor.month, cursor.day, 23, 59, 59, tzinfo=KST),
            to_dt
        )
        ids = get_order_ids(token, cursor, chunk_end)
        print(f"  {cursor.strftime('%m/%d')} : {len(ids)}건", flush=True)
        all_ids.extend(ids)
        cursor = datetime(cursor.year, cursor.month, cursor.day, tzinfo=KST) + timedelta(days=1)
    return list(set(all_ids))


def analyze(orders):
    result = {kw: {"qty": 0, "amount": 0, "orders": 0, "names": set()} for kw in TARGET_KEYWORDS}
    for wrap in orders:
        o      = wrap.get("productOrder", wrap)
        status = o.get("productOrderStatus", "")
        if status in ("CANCELED", "RETURNED", "EXCHANGED"):
            continue
        name = o.get("productName", "")
        qty  = int(o.get("quantity", 1))
        amt  = int(o.get("totalPaymentAmount", 0))
        for kw in TARGET_KEYWORDS:
            if kw in name:
                result[kw]["qty"]    += qty
                result[kw]["amount"] += amt
                result[kw]["orders"] += 1
                result[kw]["names"].add(name)
                break
    return result


def main():
    print("네이버 API 인증 중...", flush=True)
    token = get_access_token()
    print("인증 성공\n", flush=True)

    period_results = {}

    for label, from_dt, to_dt in PERIODS:
        print(f"[{label}] 조회 시작 ({from_dt.strftime('%Y-%m-%d')} ~ {to_dt.strftime('%Y-%m-%d')})", flush=True)
        all_ids = collect_period(token, label, from_dt, to_dt)
        print(f"  총 {len(all_ids)}건 상세조회 중...", flush=True)
        orders  = get_order_details(token, all_ids)
        period_results[label] = analyze(orders)
        print(f"  완료\n", flush=True)

    print("\n" + "="*60)
    print("  루카랩 상품별 판매 현황 (25년 vs 26년 1~3월)")
    print("="*60)

    for kw in TARGET_KEYWORDS:
        print(f"\n[{kw}]")
        print(f"  {'기간':<12} {'판매량':>8} {'매출':>16} {'주문건':>8}")
        print(f"  {'-'*48}")
        for label, _, _ in PERIODS:
            d = period_results[label][kw]
            print(f"  {label:<12} {d['qty']:>8}개  {d['amount']:>14,}원  {d['orders']:>6}건")

        d25 = period_results["25년 1~3월"][kw]
        d26 = period_results["26년 1~3월"][kw]
        if d25["amount"] > 0:
            yoy = (d26["amount"] - d25["amount"]) / d25["amount"] * 100
            print(f"  {'YoY':<12} {'':>8}   {'':>14}  {yoy:>+.1f}%")

        all_names = set()
        for label, _, _ in PERIODS:
            all_names |= period_results[label][kw]["names"]
        if all_names:
            print(f"  실제 상품명: {', '.join(list(all_names)[:3])}")

    print("\n" + "="*60, flush=True)


if __name__ == "__main__":
    main()
