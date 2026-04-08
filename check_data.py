"""
루카랩 스마트스토어 데이터 확인 스크립트
- 인자 없이 실행: 오늘 데이터
- python check_data.py 2026-03-13 2026-03-15 : 날짜 범위 지정
"""
import os
import sys
import base64
import time
from datetime import datetime, timezone, timedelta, date
from collections import defaultdict

import bcrypt
import requests
from dotenv import load_dotenv

load_dotenv()

NAVER_CLIENT_ID     = os.getenv("NAVER_CLIENT_ID")
NAVER_CLIENT_SECRET = os.getenv("NAVER_CLIENT_SECRET")
SLACK_WEBHOOK_URL   = os.getenv("SLACK_WEBHOOK_URL")
BASE_URL = "https://api.commerce.naver.com"
KST      = timezone(timedelta(hours=9))

DAY_KR = ["(월)", "(화)", "(수)", "(목)", "(금)", "(토)", "(일)"]


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
        },
        timeout=10,
    )
    resp.raise_for_status()
    return resp.json()["access_token"]


def get_order_ids(token, from_dt: datetime, to_dt: datetime) -> list:
    headers = {"Authorization": f"Bearer {token}"}
    all_ids, page = [], 1
    while True:
        params = {"from": from_dt.isoformat(timespec="milliseconds"),
                  "to":   to_dt.isoformat(timespec="milliseconds"),
                  "pageNum": page, "pageSize": 300}
        resp = requests.get(f"{BASE_URL}/external/v1/pay-order/seller/product-orders",
                            headers=headers, params=params, timeout=15)
        if not resp.ok:
            print(f"  ⚠️  조회 실패 ({resp.status_code}): {resp.text[:200]}")
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


def get_order_details(token, ids: list) -> list:
    if not ids:
        return []
    headers, result = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}, []
    for i in range(0, len(ids), 300):
        chunk = ids[i:i+300]
        resp  = requests.post(f"{BASE_URL}/external/v1/pay-order/seller/product-orders/query",
                              headers=headers, json={"productOrderIds": chunk}, timeout=15)
        resp.raise_for_status()
        result.extend(resp.json().get("data", []))
    return result


def aggregate(orders: list) -> dict:
    total, sales = 0, defaultdict(lambda: {"qty": 0, "amount": 0})
    for wrap in orders:
        o      = wrap.get("productOrder", wrap)
        status = o.get("productOrderStatus", "")
        if status in ("CANCELED", "RETURNED", "EXCHANGED"):
            continue
        amt  = int(o.get("totalPaymentAmount", 0))
        qty  = int(o.get("quantity", 1))
        name = o.get("productName", "알 수 없음")
        total += amt
        sales[name]["qty"]    += qty
        sales[name]["amount"] += amt
    valid   = [w for w in orders if w.get("productOrder", w).get("productOrderStatus") not in ("CANCELED", "RETURNED", "EXCHANGED")]
    ranking = sorted([{"name": k, **v} for k, v in sales.items()], key=lambda x: x["qty"], reverse=True)
    return {"total": total, "count": len(valid), "ranking": ranking}


def fmt(amount):
    return f"₩{amount:,}"

def print_report(label, data):
    print(f"\n{'─'*50}")
    print(f"  {label}")
    print(f"{'─'*50}")
    print(f"  매출:   {fmt(data['total'])}")
    print(f"  주문수: {data['count']}건")
    print(f"  판매 순위 TOP5:")
    for i, r in enumerate(data["ranking"][:5], 1):
        print(f"    {i}. {r['name']}")
        print(f"       → {r['qty']}개 / {fmt(r['amount'])}")
    if not data["ranking"]:
        print("    (데이터 없음)")


def build_slack_blocks(days, day_data, total_data, title):
    def rank_text(data):
        return "\n".join(
            f"{i+1}. {r['name']} — {r['qty']}개 ({fmt(r['amount'])})"
            for i, r in enumerate(data["ranking"][:5])
        ) or "데이터 없음"

    blocks = [
        {"type": "header", "text": {"type": "plain_text", "text": f"📊 루카랩 스마트스토어 — {title}"}},
        {"type": "divider"},
    ]
    for label, _, _ in days:
        d = day_data[label]
        blocks.append({"type": "section", "fields": [
            {"type": "mrkdwn", "text": f"*{label}*\n매출: {fmt(d['total'])}  |  {d['count']}건"},
        ]})
    if len(days) > 1:
        blocks += [
            {"type": "divider"},
            {"type": "section", "fields": [
                {"type": "mrkdwn", "text": f"*합계 매출*\n{fmt(total_data['total'])}"},
                {"type": "mrkdwn", "text": f"*합계 주문*\n{total_data['count']}건"},
            ]},
        ]
    blocks += [
        {"type": "divider"},
        {"type": "section", "text": {"type": "mrkdwn", "text": f"*📦 판매 순위 TOP5*\n{rank_text(total_data)}"}},
    ]
    return blocks


# ── 날짜 범위 결정 ─────────────────────────────────────────────────────────────

if len(sys.argv) == 3:
    # 범위 지정: python check_data.py 2026-03-13 2026-03-15
    start_date = date.fromisoformat(sys.argv[1])
    end_date   = date.fromisoformat(sys.argv[2])
elif len(sys.argv) == 2:
    # 하루 지정: python check_data.py 2026-03-16
    start_date = date.fromisoformat(sys.argv[1])
    end_date   = start_date
else:
    # 기본: 오늘
    today      = datetime.now(KST).date()
    start_date = today
    end_date   = today

days = []
d = start_date
while d <= end_date:
    label = f"{d.strftime('%m/%d')}{DAY_KR[d.weekday()]}"
    days.append((
        label,
        datetime(d.year, d.month, d.day, 0, 0, 0, tzinfo=KST),
        datetime(d.year, d.month, d.day, 23, 59, 59, tzinfo=KST),
    ))
    d += timedelta(days=1)

if start_date == end_date:
    title = f"{days[0][0]} 리포트"
else:
    title = f"{days[0][0]} ~ {days[-1][0]} 리포트"

# ── 조회 ──────────────────────────────────────────────────────────────────────

print("🔑 네이버 API 인증 중...")
token = get_access_token()
print("✅ 인증 성공\n")

day_ids, all_ids = {}, []
for label, from_dt, to_dt in days:
    print(f"📦 {label} 조회 중...")
    ids = get_order_ids(token, from_dt, to_dt)
    print(f"   → {len(ids)}건")
    day_ids[label] = set(ids)
    all_ids.extend(ids)

all_ids    = list(set(all_ids))
all_orders = get_order_details(token, all_ids)

id_to_order = {}
for wrap in all_orders:
    o = wrap.get("productOrder", wrap)
    if o.get("productOrderId"):
        id_to_order[o["productOrderId"]] = wrap

# ── 출력 ──────────────────────────────────────────────────────────────────────

print(f"\n\n{'━'*50}")
print(f"  루카랩 스마트스토어 — {title}")
print(f"{'━'*50}")

day_data = {}
for label, _, _ in days:
    day_orders     = [id_to_order[i] for i in day_ids[label] if i in id_to_order]
    day_data[label] = aggregate(day_orders)
    print_report(label, day_data[label])

total_data = aggregate(all_orders)
if len(days) > 1:
    print_report(f"합계 ({days[0][0]} ~ {days[-1][0]})", total_data)

print(f"\n{'━'*50}\n")

# ── 슬랙 전송 여부 ─────────────────────────────────────────────────────────────

send = input("슬랙으로 전송할까요? (y/n): ").strip().lower()
if send == "y":
    blocks = build_slack_blocks(days, day_data, total_data, title)
    resp   = requests.post(SLACK_WEBHOOK_URL, json={"blocks": blocks}, timeout=10)
    resp.raise_for_status()
    print("✅ 슬랙 전송 완료!")
else:
    print("전송 취소됨.")
