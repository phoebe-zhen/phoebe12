"""
특정 상품 판매수량 집계
- 스크랩 더 모먼트 노트
- 스크랩 더 모먼트 노트 전용 PVC 커버
기간: 2026-01-01 ~ 오늘
"""
import os
import base64
import time
from datetime import datetime, timezone, timedelta
from collections import defaultdict

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
    "스크랩더모먼트노트",
]
PVC_KEYWORDS = [
    "pvc",
    "PVC",
    "커버",
]

# 기간 설정
from_dt = datetime(2026, 1, 1, 0, 0, 0, tzinfo=KST)
to_dt   = datetime.now(KST).replace(hour=23, minute=59, second=59, microsecond=0)


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


def get_all_order_ids(token, from_dt, to_dt):
    headers = {"Authorization": f"Bearer {token}"}
    all_ids, page = [], 1
    print(f"  주문 ID 조회 중 (페이지 단위)...")
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
            print(f"  ⚠️  조회 실패 ({resp.status_code}): {resp.text[:300]}")
            break
        data  = resp.json().get("data", {})
        items = data.get("contents", data.get("productOrders", []))
        if not items:
            break
        ids = [item.get("productOrderId") for item in items if item.get("productOrderId")]
        all_ids.extend(ids)
        print(f"    페이지 {page}: {len(ids)}건 (누적 {len(all_ids)}건)")
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


def classify(name: str):
    """상품명으로 분류: 'note', 'pvc', None"""
    n = name.lower()
    is_note = "스크랩 더 모먼트 노트" in name or "스크랩더모먼트노트" in name
    if not is_note:
        return None
    is_pvc = "pvc" in n or "커버" in n
    return "pvc" if is_pvc else "note"


# ── 실행 ──────────────────────────────────────────────────────────────────────

print("🔑 네이버 API 인증 중...")
token = get_access_token()
print("✅ 인증 성공\n")

print(f"📅 기간: {from_dt.strftime('%Y-%m-%d')} ~ {to_dt.strftime('%Y-%m-%d')}\n")
print("📦 전체 주문 ID 조회 중...")
all_ids = get_all_order_ids(token, from_dt, to_dt)
print(f"  → 총 {len(all_ids)}건 (중복 제거 후)\n")

print("📋 주문 상세 조회 중...")
all_orders = get_order_details(token, all_ids)
print(f"  → {len(all_orders)}건 상세 조회 완료\n")

# ── 집계 ──────────────────────────────────────────────────────────────────────

note_qty = note_amt = 0
pvc_qty  = pvc_amt  = 0
note_names = defaultdict(int)
pvc_names  = defaultdict(int)

SKIP = ("CANCELED", "RETURNED", "EXCHANGED")

for wrap in all_orders:
    o = wrap.get("productOrder", wrap)
    if o.get("productOrderStatus") in SKIP:
        continue
    name = o.get("productName", "")
    cat  = classify(name)
    if cat is None:
        continue
    qty = int(o.get("quantity", 1))
    amt = int(o.get("totalPaymentAmount", 0))
    if cat == "note":
        note_qty += qty
        note_amt += amt
        note_names[name] += qty
    else:
        pvc_qty += qty
        pvc_amt += amt
        pvc_names[name] += qty

# ── 출력 ──────────────────────────────────────────────────────────────────────

print("━" * 55)
print(f"  루카랩 스마트스토어 — 스크랩 더 모먼트 상품 집계")
print(f"  기간: 2026-01-01 ~ {to_dt.strftime('%Y-%m-%d')}")
print("━" * 55)

print(f"\n📓 스크랩 더 모먼트 노트")
print(f"   판매수량: {note_qty}개")
print(f"   판매금액: ₩{note_amt:,}")
if note_names:
    print("   상품명 목록:")
    for nm, q in sorted(note_names.items(), key=lambda x: -x[1]):
        print(f"     - {nm}: {q}개")
else:
    print("   (해당 상품 없음)")

print(f"\n🗂️  스크랩 더 모먼트 노트 전용 PVC 커버")
print(f"   판매수량: {pvc_qty}개")
print(f"   판매금액: ₩{pvc_amt:,}")
if pvc_names:
    print("   상품명 목록:")
    for nm, q in sorted(pvc_names.items(), key=lambda x: -x[1]):
        print(f"     - {nm}: {q}개")
else:
    print("   (해당 상품 없음)")

print("\n" + "━" * 55)
