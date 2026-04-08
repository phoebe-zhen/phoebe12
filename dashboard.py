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
SLACK_WEBHOOK_URL   = os.getenv("SLACK_WEBHOOK_URL")
BASE_URL = "https://api.commerce.naver.com"
KST      = timezone(timedelta(hours=9))


# ── 인증 ──────────────────────────────────────────────────────────────────────

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


# ── 주문 조회 ─────────────────────────────────────────────────────────────────

def get_order_ids(token, from_dt: datetime, to_dt: datetime) -> list[str]:
    headers = {"Authorization": f"Bearer {token}"}
    all_ids = []
    page    = 1

    while True:
        params = {
            "from":     from_dt.isoformat(timespec="milliseconds"),
            "to":       to_dt.isoformat(timespec="milliseconds"),
            "pageNum":  page,
            "pageSize": 300,
        }
        resp = requests.get(
            f"{BASE_URL}/external/v1/pay-order/seller/product-orders",
            headers=headers, params=params, timeout=15,
        )
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


def get_order_details(token, product_order_ids: list[str]) -> list[dict]:
    if not product_order_ids:
        return []
    headers    = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    all_orders = []
    for i in range(0, len(product_order_ids), 300):
        chunk = product_order_ids[i:i + 300]
        resp  = requests.post(
            f"{BASE_URL}/external/v1/pay-order/seller/product-orders/query",
            headers=headers, json={"productOrderIds": chunk}, timeout=15,
        )
        resp.raise_for_status()
        all_orders.extend(resp.json().get("data", []))
    return all_orders


# ── 집계 ──────────────────────────────────────────────────────────────────────

def aggregate(orders: list[dict]) -> dict:
    total = 0
    sales = defaultdict(lambda: {"qty": 0, "amount": 0})

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

    ranking = sorted(
        [{"name": k, **v} for k, v in sales.items()],
        key=lambda x: x["qty"], reverse=True,
    )
    valid_count = sum(
        1 for wrap in orders
        if wrap.get("productOrder", wrap).get("productOrderStatus") not in ("CANCELED", "RETURNED", "EXCHANGED")
    )
    return {"total": total, "count": valid_count, "ranking": ranking}


# ── 슬랙 메시지 ───────────────────────────────────────────────────────────────

def fmt(amount: int) -> str:
    return f"₩{amount:,}"

def rank_text(data: dict) -> str:
    return "\n".join(
        f"{i+1}. {r['name']} — {r['qty']}개 ({fmt(r['amount'])})"
        for i, r in enumerate(data["ranking"][:5])
    ) or "데이터 없음"

def build_message(days: list[tuple], day_data: dict, total_data: dict, title: str) -> dict:
    blocks = [
        {"type": "header", "text": {"type": "plain_text", "text": f"📊 루카랩 스마트스토어 — {title}"}},
        {"type": "divider"},
    ]

    for label, _, _ in days:
        d = day_data[label]
        blocks.append({
            "type": "section",
            "fields": [
                {"type": "mrkdwn", "text": f"*{label}*\n매출: {fmt(d['total'])}  |  {d['count']}건"},
            ],
        })

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
    return {"blocks": blocks}


def send_to_slack(message: dict):
    resp = requests.post(SLACK_WEBHOOK_URL, json=message, timeout=10)
    resp.raise_for_status()


# ── 메인 ─────────────────────────────────────────────────────────────────────

def main():
    now     = datetime.now(KST)
    weekday = now.weekday()  # 월=0, 화=1, ..., 일=6

    print(f"[{now.strftime('%Y-%m-%d %H:%M:%S')}] 리포트 생성 시작")

    # 리포트 날짜 범위 결정
    if weekday == 0:  # 월요일 → 금+토+일
        fri = now.date() - timedelta(days=3)
        days = [
            (f"{(fri).strftime('%m/%d')}(금)", datetime(fri.year, fri.month, fri.day, 0, 0, 0, tzinfo=KST), datetime(fri.year, fri.month, fri.day, 23, 59, 59, tzinfo=KST)),
        ]
        sat = fri + timedelta(days=1)
        sun = fri + timedelta(days=2)
        days += [
            (f"{sat.strftime('%m/%d')}(토)", datetime(sat.year, sat.month, sat.day, 0, 0, 0, tzinfo=KST), datetime(sat.year, sat.month, sat.day, 23, 59, 59, tzinfo=KST)),
            (f"{sun.strftime('%m/%d')}(일)", datetime(sun.year, sun.month, sun.day, 0, 0, 0, tzinfo=KST), datetime(sun.year, sun.month, sun.day, 23, 59, 59, tzinfo=KST)),
        ]
        title = f"{days[0][0]} ~ {days[-1][0]} 주말 리포트"
    else:  # 화~금 → 전일
        yesterday = now.date() - timedelta(days=1)
        day_names = ["(월)", "(화)", "(수)", "(목)", "(금)", "(토)", "(일)"]
        label = f"{yesterday.strftime('%m/%d')}{day_names[yesterday.weekday()]}"
        days = [
            (label, datetime(yesterday.year, yesterday.month, yesterday.day, 0, 0, 0, tzinfo=KST),
                    datetime(yesterday.year, yesterday.month, yesterday.day, 23, 59, 59, tzinfo=KST)),
        ]
        title = f"{label} 전일 리포트"

    # 인증
    print("🔑 네이버 API 인증 중...")
    token = get_access_token()
    print("✅ 인증 성공")

    # 날짜별 주문 조회
    day_ids  = {}
    all_ids  = []
    for label, from_dt, to_dt in days:
        print(f"📦 {label} 조회 중...")
        ids = get_order_ids(token, from_dt, to_dt)
        print(f"   → {len(ids)}건")
        day_ids[label] = set(ids)
        all_ids.extend(ids)

    all_ids = list(set(all_ids))
    print(f"📋 상세조회 중... ({len(all_ids)}건)")
    all_orders = get_order_details(token, all_ids)

    # ID → 주문 매핑
    id_to_order = {}
    for wrap in all_orders:
        o   = wrap.get("productOrder", wrap)
        oid = o.get("productOrderId")
        if oid:
            id_to_order[oid] = wrap

    # 집계
    day_data = {}
    for label, _, _ in days:
        day_orders     = [id_to_order[i] for i in day_ids[label] if i in id_to_order]
        day_data[label] = aggregate(day_orders)

    total_data = aggregate(all_orders)

    # 데이터 미리보기
    print(f"\n{'━'*50}")
    print(f"  {title}")
    print(f"{'━'*50}")
    for label, _, _ in days:
        d = day_data[label]
        print(f"\n  [{label}]")
        print(f"  매출:   ₩{d['total']:,}  |  {d['count']}건")
        for i, r in enumerate(d["ranking"][:5], 1):
            print(f"  {i}. {r['name']} — {r['qty']}개 (₩{r['amount']:,})")
    if len(days) > 1:
        print(f"\n  [합계] ₩{total_data['total']:,}  |  {total_data['count']}건")
    print(f"{'━'*50}\n")

    # 슬랙 전송 확인
    confirm = input("슬랙으로 전송할까요? (y/n): ").strip().lower()
    if confirm == "y":
        print("💬 슬랙으로 전송 중...")
        message = build_message(days, day_data, total_data, title)
        send_to_slack(message)
        print("🎉 완료!")
    else:
        print("전송 취소됨.")


if __name__ == "__main__":
    main()
