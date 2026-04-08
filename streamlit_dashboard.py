"""
루카랩 스마트스토어 실시간 대시보드
실행: streamlit run streamlit_dashboard.py
"""

import os
import base64
import time
from datetime import datetime, timezone, timedelta
from collections import defaultdict

import bcrypt
import requests
import streamlit as st
from dotenv import load_dotenv

load_dotenv()

def _get(key):
    """로컬은 .env, Streamlit Cloud는 st.secrets에서 읽기"""
    try:
        return str(st.secrets[key])
    except Exception:
        return os.getenv(key)

NAVER_CLIENT_ID     = _get("NAVER_CLIENT_ID")
NAVER_CLIENT_SECRET = _get("NAVER_CLIENT_SECRET")
NAVER_ACCOUNT_ID    = _get("NAVER_ACCOUNT_ID")
BASE_URL = "https://api.commerce.naver.com"
KST      = timezone(timedelta(hours=9))

# 주문 상태 한글 매핑
STATUS_KR = {
    "PAYMENT_WAITING":       "결제대기",
    "PAYED":                 "결제완료",
    "DELIVERING":            "배송중",
    "DELIVERED":             "배송완료",
    "PURCHASE_DECIDED":      "구매확정",
    "EXCHANGED":             "교환",
    "CANCELED":              "취소",
    "RETURNED":              "반품",
    "CANCELED_BY_NOPAYMENT": "미결제취소",
}

# 취소/반품 계열 상태 (매출 집계 제외)
EXCLUDED_STATUSES = {"CANCELED", "RETURNED", "EXCHANGED", "CANCELED_BY_NOPAYMENT"}

# 상태별 색상
STATUS_COLOR = {
    "결제완료":   "#4CAF50",
    "배송중":    "#2196F3",
    "배송완료":   "#9C27B0",
    "구매확정":   "#FF9800",
    "결제대기":   "#607D8B",
    "취소":     "#f44336",
    "반품":     "#E91E63",
    "교환":     "#FF5722",
    "미결제취소":  "#9E9E9E",
}


# ── API 함수 ──────────────────────────────────────────────────────────────────

def get_access_token() -> str:
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
            "account_id":         NAVER_ACCOUNT_ID,
        },
        timeout=10,
    )
    if not resp.ok:
        raise Exception(f"인증 실패 ({resp.status_code}): {resp.text}")
    return resp.json()["access_token"]


def get_order_ids(token: str, from_dt: datetime, to_dt: datetime) -> list:
    headers  = {"Authorization": f"Bearer {token}"}
    all_ids, page = [], 1
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
            st.warning(f"주문 목록 조회 실패 ({resp.status_code}): {resp.text[:200]}")
            break
        data  = resp.json().get("data", {})
        items = data.get("contents", data.get("productOrders", []))
        if not items:
            break
        all_ids.extend(item["productOrderId"] for item in items if item.get("productOrderId"))
        if len(items) < 300:
            break
        page += 1
    return list(set(all_ids))


def get_order_details(token: str, ids: list) -> list:
    if not ids:
        return []
    headers, result = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}, []
    for i in range(0, len(ids), 300):
        chunk = ids[i:i + 300]
        resp  = requests.post(
            f"{BASE_URL}/external/v1/pay-order/seller/product-orders/query",
            headers=headers, json={"productOrderIds": chunk}, timeout=15,
        )
        resp.raise_for_status()
        result.extend(resp.json().get("data", []))
    return result


# ── 집계 함수 ─────────────────────────────────────────────────────────────────

def aggregate_by_status(orders: list) -> dict:
    """주문 상태별 건수·금액 집계"""
    status_count  = defaultdict(int)
    status_amount = defaultdict(int)
    for wrap in orders:
        o      = wrap.get("productOrder", wrap)
        status = o.get("productOrderStatus", "UNKNOWN")
        qty    = int(o.get("quantity", 1))
        amt    = int(o.get("totalPaymentAmount", 0))
        status_count[status]  += qty
        status_amount[status] += amt
    return {"count": status_count, "amount": status_amount}


def calc_total_revenue(orders: list) -> int:
    """취소·반품 제외한 오늘 총 매출"""
    return sum(
        int(wrap.get("productOrder", wrap).get("totalPaymentAmount", 0))
        for wrap in orders
        if wrap.get("productOrder", wrap).get("productOrderStatus") not in EXCLUDED_STATUSES
    )


def get_top_products(orders: list, n: int = 5) -> list:
    """판매 수량 기준 TOP N 상품"""
    sales = defaultdict(lambda: {"qty": 0, "amount": 0})
    for wrap in orders:
        o = wrap.get("productOrder", wrap)
        if o.get("productOrderStatus") in EXCLUDED_STATUSES:
            continue
        name              = o.get("productName", "알 수 없음")
        sales[name]["qty"]    += int(o.get("quantity", 1))
        sales[name]["amount"] += int(o.get("totalPaymentAmount", 0))
    return sorted(sales.items(), key=lambda x: x[1]["qty"], reverse=True)[:n]


# ── 데이터 로딩 (캐시: 5분) ───────────────────────────────────────────────────

@st.cache_data(ttl=300)
def load_today_data():
    now   = datetime.now(KST)
    today = now.date()
    from_dt = datetime(today.year, today.month, today.day, 0, 0, 0, tzinfo=KST)
    to_dt   = datetime(today.year, today.month, today.day, 23, 59, 59, tzinfo=KST)

    token  = get_access_token()
    ids    = get_order_ids(token, from_dt, to_dt)
    orders = get_order_details(token, ids)
    return orders, now


# ── Streamlit UI ──────────────────────────────────────────────────────────────

st.set_page_config(
    page_title="루카랩 스마트스토어 대시보드",
    page_icon="📦",
    layout="wide",
)

st.title("📦 루카랩 스마트스토어 실시간 대시보드")

# 새로고침 버튼
col_title, col_btn = st.columns([5, 1])
with col_btn:
    if st.button("🔄 새로고침", use_container_width=True):
        st.cache_data.clear()
        st.rerun()

# 디버그: 시크릿 로딩 확인
with st.expander("🔧 디버그 정보 (확인 후 삭제 예정)"):
    st.write(f"CLIENT_ID 앞 5자: `{NAVER_CLIENT_ID[:5] if NAVER_CLIENT_ID else 'None'}`")
    st.write(f"CLIENT_SECRET 앞 5자: `{NAVER_CLIENT_SECRET[:5] if NAVER_CLIENT_SECRET else 'None'}`")
    st.write(f"ACCOUNT_ID: `{NAVER_ACCOUNT_ID}`")
    try:
        my_ip = requests.get("https://api.ipify.org", timeout=5).text
        st.write(f"서버 IP: `{my_ip}`")
    except Exception:
        st.write("서버 IP 확인 실패")

# 데이터 로딩
with st.spinner("네이버 커머스 API에서 데이터를 불러오는 중..."):
    try:
        orders, fetched_at = load_today_data()
    except Exception as e:
        st.error(f"데이터 로딩 실패: {e}")
        st.stop()

if orders:
    _sample = orders[0].get("productOrder", orders[0])
    st.caption(f"🔍 샘플 필드: {list(_sample.keys())}")
    st.caption(f"🔍 placeOrderDate: {_sample.get('placeOrderDate')} | status: {_sample.get('productOrderStatus')} | amount: {_sample.get('totalPaymentAmount')}")

today_str = fetched_at.strftime("%Y년 %m월 %d일 (%a)")
st.caption(f"기준일: {today_str}  |  마지막 조회: {fetched_at.strftime('%H:%M:%S')}  |  5분마다 자동 갱신")
st.divider()

# ── 상단 KPI 카드 ─────────────────────────────────────────────────────────────

total_revenue    = calc_total_revenue(orders)
total_orders     = len(orders)
excluded_count   = sum(
    1 for w in orders
    if w.get("productOrder", w).get("productOrderStatus") in EXCLUDED_STATUSES
)
valid_orders     = total_orders - excluded_count

c1, c2, c3 = st.columns(3)
c1.metric("💰 오늘 총 매출", f"₩{total_revenue:,}")
c2.metric("📋 전체 주문 건수", f"{total_orders}건")
c3.metric("✅ 유효 주문 건수", f"{valid_orders}건", f"-{excluded_count}건 취소/반품")

st.divider()

# ── 주문 상태별 현황 ──────────────────────────────────────────────────────────

st.subheader("📊 주문 상태별 현황")

agg     = aggregate_by_status(orders)
statuses = list(STATUS_KR.keys())

# 데이터가 있는 상태만 필터
present = [(s, STATUS_KR.get(s, s)) for s in statuses if agg["count"].get(s, 0) > 0]
# 데이터 없는 미지 상태 추가
for raw in agg["count"]:
    if raw not in STATUS_KR:
        present.append((raw, raw))

if not present:
    st.info("오늘 주문 데이터가 없습니다.")
else:
    # 상태 카드 그리드
    cols = st.columns(min(len(present), 4))
    for idx, (raw, kr) in enumerate(present):
        cnt = agg["count"].get(raw, 0)
        amt = agg["amount"].get(raw, 0)
        color = STATUS_COLOR.get(kr, "#757575")
        with cols[idx % 4]:
            st.markdown(
                f"""
                <div style="
                    background:{color}18;
                    border-left: 4px solid {color};
                    border-radius: 8px;
                    padding: 16px;
                    margin-bottom: 12px;
                ">
                    <div style="font-size:13px; color:{color}; font-weight:600;">{kr}</div>
                    <div style="font-size:28px; font-weight:700; color:#1a1a1a;">{cnt}<span style="font-size:14px;"> 건</span></div>
                    <div style="font-size:12px; color:#555;">₩{amt:,}</div>
                </div>
                """,
                unsafe_allow_html=True,
            )

    # 막대 차트 (수량 기준)
    st.markdown("#### 상태별 주문 수량")
    chart_data = {kr: agg["count"].get(raw, 0) for raw, kr in present}
    # streamlit 기본 bar_chart용 dict → list
    labels = list(chart_data.keys())
    values = list(chart_data.values())

    try:
        import pandas as pd
        df_chart = pd.DataFrame({"주문 건수": values}, index=labels)
        st.bar_chart(df_chart)
    except ImportError:
        # pandas 없으면 텍스트 테이블로 대체
        for k, v in chart_data.items():
            bar = "█" * min(v, 40)
            st.text(f"{k:8s} {bar} {v}건")

st.divider()

# ── TOP5 판매 상품 ────────────────────────────────────────────────────────────

st.subheader("🏆 오늘 판매 TOP 5")

top_products = get_top_products(orders)

if not top_products:
    st.info("판매 데이터가 없습니다.")
else:
    try:
        import pandas as pd
        rows = [
            {
                "순위": i + 1,
                "상품명": name,
                "수량(개)": info["qty"],
                "매출액": f"₩{info['amount']:,}",
            }
            for i, (name, info) in enumerate(top_products)
        ]
        df = pd.DataFrame(rows).set_index("순위")
        st.dataframe(df, use_container_width=True)
    except ImportError:
        for i, (name, info) in enumerate(top_products, 1):
            st.write(f"{i}. **{name}** — {info['qty']}개 / ₩{info['amount']:,}")

st.divider()

# ── 시간대별 매출 추이 ────────────────────────────────────────────────────────

st.subheader("⏱ 시간대별 매출 추이")

try:
    import pandas as pd
    hourly_rows = []
    for wrap in orders:
        o = wrap.get("productOrder", wrap)
        if o.get("productOrderStatus") in EXCLUDED_STATUSES:
            continue
        paid_at = o.get("placeOrderDate")
        if not paid_at:
            continue
        try:
            dt = datetime.fromisoformat(paid_at.replace("Z", "+00:00")).astimezone(KST)
            hourly_rows.append({"hour": dt.hour, "amount": int(o.get("totalPaymentAmount", 0))})
        except Exception:
            continue

    if hourly_rows:
        df_hourly = pd.DataFrame(hourly_rows)
        df_hourly = df_hourly.groupby("hour")["amount"].sum().reindex(range(24), fill_value=0)
        df_hourly.index = [f"{h:02d}시" for h in df_hourly.index]
        st.line_chart(df_hourly.rename("매출액 (원)"))
    else:
        # 첫 번째 주문의 키 목록 출력해서 날짜 필드명 확인
        if orders:
            sample = orders[0].get("productOrder", orders[0])
            st.warning(f"전체 필드 목록: {list(sample.keys())}")
        else:
            st.info("시간대별 데이터가 없습니다.")
except ImportError:
    st.warning("pandas가 필요합니다.")

st.divider()

# ── 전체 주문 목록 (접기) ──────────────────────────────────────────────────────

with st.expander("📋 전체 주문 목록 보기"):
    try:
        import pandas as pd
        rows = []
        for wrap in orders:
            o = wrap.get("productOrder", wrap)
            raw_status = o.get("productOrderStatus", "")
            rows.append({
                "주문번호":   o.get("productOrderId", ""),
                "상품명":    o.get("productName", ""),
                "수량":     int(o.get("quantity", 1)),
                "금액":     f"₩{int(o.get('totalPaymentAmount', 0)):,}",
                "상태":     STATUS_KR.get(raw_status, raw_status),
            })
        if rows:
            st.dataframe(pd.DataFrame(rows), use_container_width=True)
        else:
            st.info("주문 없음")
    except ImportError:
        st.warning("pandas가 없어 전체 목록을 표시할 수 없습니다. `pip install pandas`로 설치하세요.")

# ── 자동 새로고침 (5분) ────────────────────────────────────────────────────────

st.markdown(
    """
    <script>
    setTimeout(function() { window.location.reload(); }, 300000);
    </script>
    """,
    unsafe_allow_html=True,
)
