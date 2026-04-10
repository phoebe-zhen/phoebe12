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
import pandas as pd
import streamlit as st
from dotenv import load_dotenv

load_dotenv()

# ── 환경변수 ──────────────────────────────────────────────────────────────────

def _get(key):
    try:
        return str(st.secrets[key])
    except Exception:
        return os.getenv(key)

NAVER_CLIENT_ID     = _get("NAVER_CLIENT_ID")
NAVER_CLIENT_SECRET = _get("NAVER_CLIENT_SECRET")
NAVER_ACCOUNT_ID    = _get("NAVER_ACCOUNT_ID")
BASE_URL = "https://api.commerce.naver.com"
KST      = timezone(timedelta(hours=9))

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

EXCLUDED_STATUSES = {"CANCELED", "RETURNED", "EXCHANGED", "CANCELED_BY_NOPAYMENT"}

STATUS_COLOR = {
    "결제완료":  "#4CAF50",
    "배송중":   "#2196F3",
    "배송완료":  "#9C27B0",
    "구매확정":  "#FF9800",
    "결제대기":  "#607D8B",
    "취소":    "#f44336",
    "반품":    "#E91E63",
    "교환":    "#FF5722",
    "미결제취소": "#9E9E9E",
}

TARGET_PRODUCTS = ["기록책", "스크랩 더 모먼트 노트"]


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

def calc_total_revenue(orders: list) -> int:
    return sum(
        int(wrap.get("productOrder", wrap).get("totalPaymentAmount", 0))
        for wrap in orders
        if wrap.get("productOrder", wrap).get("productOrderStatus") not in EXCLUDED_STATUSES
    )

def calc_valid_count(orders: list) -> int:
    return sum(
        1 for w in orders
        if w.get("productOrder", w).get("productOrderStatus") not in EXCLUDED_STATUSES
    )

def calc_total_qty(orders: list) -> int:
    return sum(
        int(w.get("productOrder", w).get("quantity", 1))
        for w in orders
        if w.get("productOrder", w).get("productOrderStatus") not in EXCLUDED_STATUSES
    )

def calc_excluded_count(orders: list) -> int:
    return sum(
        1 for w in orders
        if w.get("productOrder", w).get("productOrderStatus") in EXCLUDED_STATUSES
    )

def aggregate_by_status(orders: list) -> dict:
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

def get_top_products(orders: list, n: int = 5) -> list:
    sales = defaultdict(lambda: {"qty": 0, "amount": 0})
    for wrap in orders:
        o = wrap.get("productOrder", wrap)
        if o.get("productOrderStatus") in EXCLUDED_STATUSES:
            continue
        name = o.get("productName", "알 수 없음")
        sales[name]["qty"]    += int(o.get("quantity", 1))
        sales[name]["amount"] += int(o.get("totalPaymentAmount", 0))
    return sorted(sales.items(), key=lambda x: x[1]["qty"], reverse=True)[:n]

def product_qty(order_list: list, keyword: str) -> int:
    return sum(
        int(w.get("productOrder", w).get("quantity", 1))
        for w in order_list
        if keyword in w.get("productOrder", w).get("productName", "")
        and w.get("productOrder", w).get("productOrderStatus") not in EXCLUDED_STATUSES
    )

def get_option_qty(order_list: list, keyword: str) -> dict:
    """상품명에 keyword가 포함된 주문의 옵션별 수량 집계"""
    result = defaultdict(int)
    for wrap in order_list:
        o = wrap.get("productOrder", wrap)
        if o.get("productOrderStatus") in EXCLUDED_STATUSES:
            continue
        if keyword not in o.get("productName", ""):
            continue
        raw = o.get("productOption", "") or ""
        option = raw.replace("옵션: ", "").strip() or "옵션없음"
        result[option] += int(o.get("quantity", 1))
    return dict(result)

def get_top_option(option_dict: dict) -> str:
    if not option_dict:
        return "-"
    return max(option_dict, key=option_dict.get)

def get_weekly_avg_qty(weekly_data: dict, keyword: str) -> float:
    total = sum(product_qty(v, keyword) for v in weekly_data.values())
    days  = len(weekly_data) or 1
    return round(total / days, 1)

def build_option_compare_df(today_opts: dict, yest_opts: dict) -> pd.DataFrame:
    """오늘 vs 전일 옵션 비교 테이블 생성"""
    all_options = sorted(set(today_opts) | set(yest_opts))
    rows = []
    max_today = max(today_opts.values(), default=0)
    for opt in all_options:
        t = today_opts.get(opt, 0)
        y = yest_opts.get(opt, 0)
        diff = t - y
        if t == max_today and t > 0:
            note = "오늘 최다"
        elif diff > 0:
            note = "증가"
        elif diff < 0:
            note = "감소"
        else:
            note = "유지"
        rows.append({"옵션": opt, "오늘": t, "전일": y, "증감": diff, "비고": note})
    return pd.DataFrame(rows)

# ── 방문수 (오늘 보고서 API) ───────────────────────────────────────────────────

def get_channel_no(token: str) -> str | None:
    """고객 현황(채널) API로 channelNo 자동 조회"""
    resp = requests.get(
        f"{BASE_URL}/external/v1/bizdata-stats/channels",
        headers={"Authorization": f"Bearer {token}"},
        timeout=10,
    )
    if not resp.ok:
        return None
    data = resp.json()
    # 첫 번째 채널 번호 반환
    channels = data if isinstance(data, list) else data.get("channels", data.get("data", []))
    if channels:
        ch = channels[0]
        return str(ch.get("channelNo") or ch.get("id") or "")
    return None

@st.cache_data(ttl=300)
def get_current_visitors() -> int | None:
    try:
        token = get_access_token()
        channel_no = get_channel_no(token)
        if not channel_no:
            return None
        resp = requests.get(
            f"{BASE_URL}/external/v1/bizdata-stats/channels/{channel_no}/realtime/daily",
            headers={"Authorization": f"Bearer {token}"},
            timeout=10,
        )
        if not resp.ok:
            return None
        data = resp.json()
        return int(data.get("numInteraction", 0))
    except Exception:
        return None


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

@st.cache_data(ttl=300)
def load_yesterday_data():
    yesterday = (datetime.now(KST) - timedelta(days=1)).date()
    from_dt = datetime(yesterday.year, yesterday.month, yesterday.day, 0, 0, 0, tzinfo=KST)
    to_dt   = datetime(yesterday.year, yesterday.month, yesterday.day, 23, 59, 59, tzinfo=KST)
    token  = get_access_token()
    ids    = get_order_ids(token, from_dt, to_dt)
    return get_order_details(token, ids)

@st.cache_data(ttl=300)
def load_weekly_data():
    token = get_access_token()
    today = datetime.now(KST).date()
    result = {}
    for i in range(7):
        d = today - timedelta(days=i)
        from_dt = datetime(d.year, d.month, d.day, 0, 0, 0, tzinfo=KST)
        to_dt   = datetime(d.year, d.month, d.day, 23, 59, 59, tzinfo=KST)
        ids    = get_order_ids(token, from_dt, to_dt)
        result[d] = get_order_details(token, ids)
    return result


# ── UI 설정 ───────────────────────────────────────────────────────────────────

st.set_page_config(
    page_title="루카랩 스마트스토어 대시보드",
    page_icon="📦",
    layout="wide",
)

# 가벼운 CSS
st.markdown("""
<style>
.card {
    background: #f8f9fa;
    border: 1px solid #e0e0e0;
    border-radius: 10px;
    padding: 20px;
    height: 100%;
    min-height: 160px;
    box-sizing: border-box;
}
.card-title { font-size: 14px; color: #666; font-weight: 600; margin-bottom: 8px; }
.card-value { font-size: 28px; font-weight: 700; color: #1a1a1a; }
.card-sub   { font-size: 12px; color: #888; margin-top: 4px; }
.red   { color: #f44336; font-weight: 600; }
.green { color: #4CAF50; font-weight: 600; }
.tag-warn { background:#fff3e0; color:#e65100; border-radius:4px; padding:2px 8px; font-size:12px; }
.tag-ok   { background:#e8f5e9; color:#2e7d32; border-radius:4px; padding:2px 8px; font-size:12px; }
</style>
""", unsafe_allow_html=True)


# ── 1. 헤더 ──────────────────────────────────────────────────────────────────

col_h, col_btn = st.columns([5, 1])
with col_h:
    st.title("📦 루카랩 스마트스토어 실시간 대시보드")
with col_btn:
    st.markdown("<br>", unsafe_allow_html=True)
    if st.button("🔄 새로고침", use_container_width=True):
        st.cache_data.clear()
        st.rerun()

# 데이터 로딩
with st.spinner("데이터 불러오는 중..."):
    try:
        orders, fetched_at = load_today_data()
    except Exception as e:
        st.error(f"데이터 로딩 실패: {e}")
        st.stop()
    yesterday_orders = load_yesterday_data()
    weekly_data      = load_weekly_data()

st.caption(
    f"기준일: {fetched_at.strftime('%Y년 %m월 %d일 (%a)')}  |  "
    f"마지막 조회: {fetched_at.strftime('%H:%M:%S')}  |  5분마다 자동 갱신"
)

with st.expander("🔧 디버그 정보"):
    try:
        _ip = requests.get("https://api.ipify.org", timeout=5).text
        st.write(f"서버 IP: `{_ip}`")
    except Exception:
        st.write("IP 확인 실패")

st.divider()


# ── 2. 상단 KPI ───────────────────────────────────────────────────────────────

total_revenue    = calc_total_revenue(orders)
total_orders     = len(orders)
excluded_count   = calc_excluded_count(orders)
valid_orders     = total_orders - excluded_count
total_qty        = calc_total_qty(orders)
aov              = total_revenue // valid_orders if valid_orders > 0 else 0
yesterday_revenue = calc_total_revenue(yesterday_orders)
yest_qty         = calc_total_qty(yesterday_orders)

rev_diff = f"{(total_revenue - yesterday_revenue) / yesterday_revenue * 100:+.1f}%" if yesterday_revenue > 0 else "-"
qty_diff = f"{(total_qty - yest_qty) / yest_qty * 100:+.1f}%" if yest_qty > 0 else "-"
visitors = get_current_visitors()  # [방문수 placeholder]

k1, k2, k3, k4, k5 = st.columns(5)

with k1:
    rev_color = "red" if total_revenue < yesterday_revenue else "green"
    st.markdown(f"""
    <div class="card">
        <div class="card-title">💰 오늘 매출</div>
        <div class="card-value">₩{total_revenue:,}</div>
        <div class="card-sub">전일 대비 <span class="{rev_color}">{rev_diff}</span> · 어제 ₩{yesterday_revenue:,}</div>
    </div>
    """, unsafe_allow_html=True)

with k2:
    st.markdown(f"""
    <div class="card">
        <div class="card-title">📋 오늘 주문수</div>
        <div class="card-value">{valid_orders}건</div>
        <div class="card-sub">취소·반품 {excluded_count}건 포함 전체 {total_orders}건</div>
    </div>
    """, unsafe_allow_html=True)

with k3:
    qty_color = "red" if total_qty < yest_qty else "green"
    st.markdown(f"""
    <div class="card">
        <div class="card-title">📦 오늘 판매수량</div>
        <div class="card-value">{total_qty}개</div>
        <div class="card-sub">전일 대비 <span class="{qty_color}">{qty_diff}</span></div>
    </div>
    """, unsafe_allow_html=True)

with k4:
    st.markdown(f"""
    <div class="card">
        <div class="card-title">🧾 객단가</div>
        <div class="card-value">₩{aov:,}</div>
        <div class="card-sub">&nbsp;</div>
    </div>
    """, unsafe_allow_html=True)

with k5:
    visitor_str = f"{visitors}명" if visitors is not None else "방문수 데이터 없음"
    st.markdown(f"""
    <div class="card">
        <div class="card-title">👥 현재 방문수</div>
        <div class="card-value">{visitor_str}</div>
        <div class="card-sub">&nbsp;</div>
    </div>
    """, unsafe_allow_html=True)

st.markdown("<br>", unsafe_allow_html=True)
st.divider()


# ── 3. 핵심 상품 성과 ─────────────────────────────────────────────────────────

st.subheader("⭐ 핵심 상품 성과")

p_col1, p_col2 = st.columns(2)

for col, keyword in zip([p_col1, p_col2], TARGET_PRODUCTS):
    today_q  = product_qty(orders, keyword)
    yest_q   = product_qty(yesterday_orders, keyword)
    pct      = f"{(today_q - yest_q) / yest_q * 100:+.1f}%" if yest_q > 0 else "-"
    avg7     = get_weekly_avg_qty(weekly_data, keyword)
    today_opts = get_option_qty(orders, keyword)
    top_opt  = get_top_option(today_opts)

    # 상태 판단: 오늘이 7일 평균의 70% 미만이면 주의
    if avg7 > 0 and today_q < avg7 * 0.7:
        status_html = '<span class="tag-warn">주의</span>'
    else:
        status_html = '<span class="tag-ok">보통</span>'

    diff_color = "red" if today_q < yest_q else "green"

    with col:
        st.markdown(f"""
        <div class="card">
            <div class="card-title">📌 {keyword}</div>
            <div style="display:flex; align-items:center; gap:10px; margin-bottom:6px;">
                <div class="card-value">{today_q}개</div>
                {status_html}
            </div>
            <div class="card-sub">전일 {yest_q}개 &nbsp;|&nbsp; 증감 <span class="{diff_color}">{pct}</span></div>
            <div class="card-sub">최근 7일 평균 {avg7}개</div>
            <div class="card-sub">오늘 최다 옵션: <b>{top_opt}</b></div>
        </div>
        """, unsafe_allow_html=True)

st.markdown("<br>", unsafe_allow_html=True)
st.divider()


# ── 4. 대표 상품 옵션 분석 ────────────────────────────────────────────────────

st.subheader("📊 대표 상품 옵션 분석")

tab1, tab2 = st.tabs(TARGET_PRODUCTS)

for tab, keyword in zip([tab1, tab2], TARGET_PRODUCTS):
    with tab:
        today_opts = get_option_qty(orders, keyword)
        yest_opts  = get_option_qty(yesterday_orders, keyword)
        top_opt    = get_top_option(today_opts)
        today_total = sum(today_opts.values())
        yest_total  = sum(yest_opts.values())

        st.caption(
            f"오늘 총 {today_total}개 / 전일 {yest_total}개 / "
            f"가장 많이 팔린 옵션: {top_opt}"
        )

        df_cmp = build_option_compare_df(today_opts, yest_opts)

        if df_cmp.empty:
            st.info("해당 상품 데이터가 없습니다.")
        else:
            # 증감 색상 적용
            def color_diff(val):
                if val > 0:
                    return "color: #4CAF50; font-weight:600"
                elif val < 0:
                    return "color: #f44336; font-weight:600"
                return ""

            try:
                styled = df_cmp.style.map(color_diff, subset=["증감"])
            except AttributeError:
                styled = df_cmp.style.applymap(color_diff, subset=["증감"])
            st.dataframe(styled, use_container_width=True, hide_index=True)

st.divider()


# ── 5. 비교 요약 ──────────────────────────────────────────────────────────────

st.subheader("📋 비교 요약")

yest_valid   = calc_valid_count(yesterday_orders)
weekly_rev   = sum(calc_total_revenue(v) for v in weekly_data.values())
weekly_cnt   = sum(calc_valid_count(v) for v in weekly_data.values())
weekly_qty_s = sum(calc_total_qty(v) for v in weekly_data.values())
daily_avg    = weekly_rev // len(weekly_data) if weekly_data else 0

col_y, col_w = st.columns(2)

with col_y:
    st.markdown("""<div class="card">
        <div class="card-title">📅 전일 판매 요약</div>""", unsafe_allow_html=True)
    y1, y2, y3 = st.columns(3)
    y1.metric("매출", f"₩{yesterday_revenue:,}")
    y2.metric("주문수", f"{yest_valid}건")
    y3.metric("판매수량", f"{yest_qty}개")
    st.markdown("</div>", unsafe_allow_html=True)

with col_w:
    st.markdown("""<div class="card">
        <div class="card-title">📆 최근 7일 요약</div>""", unsafe_allow_html=True)
    w1, w2, w3, w4 = st.columns(4)
    w1.metric("주간 매출", f"₩{weekly_rev:,}")
    w2.metric("주간 주문수", f"{weekly_cnt}건")
    w3.metric("주간 판매수량", f"{weekly_qty_s}개")
    w4.metric("일평균 매출", f"₩{daily_avg:,}")
    st.markdown("</div>", unsafe_allow_html=True)

# 해석 문장
if daily_avg > 0:
    ratio = (total_revenue - daily_avg) / daily_avg * 100
    direction = "높습니다" if ratio >= 0 else "낮습니다"
    color = "green" if ratio >= 0 else "red"
    st.markdown(
        f"<span class='{color}'>오늘 매출은 최근 7일 일평균 대비 {abs(ratio):.1f}% {direction}.</span>",
        unsafe_allow_html=True,
    )

st.divider()


# ── 6. 매출 흐름 분석 ─────────────────────────────────────────────────────────

st.subheader("📈 매출 흐름 분석")

chart_l, chart_r = st.columns(2)

with chart_l:
    st.markdown("##### 최근 7일 매출 추이")
    daily_rows = [
        {"날짜": d.strftime("%m/%d"), "매출액": calc_total_revenue(v)}
        for d, v in sorted(weekly_data.items())
    ]
    st.line_chart(pd.DataFrame(daily_rows).set_index("날짜"))

with chart_r:
    st.markdown("##### 시간대별 매출 추이 (오늘)")
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
        df_h = pd.DataFrame(hourly_rows).groupby("hour")["amount"].sum().reindex(range(24), fill_value=0)
        df_h.index = [f"{h:02d}시" for h in df_h.index]
        st.line_chart(df_h.rename("매출액 (원)"))
    else:
        st.info("시간대별 데이터가 아직 충분하지 않습니다.")

st.divider()


# ── 7. 전체 상품 분석 ─────────────────────────────────────────────────────────

st.subheader("🏆 전체 상품 분석")

top_products  = get_top_products(orders)
yest_products = dict(get_top_products(yesterday_orders, n=100))

# 오늘 TOP5
st.markdown("##### 오늘 판매 TOP 5")
if top_products:
    rows_top = []
    for i, (name, info) in enumerate(top_products):
        yest_info = yest_products.get(name, {})
        yest_q_p  = yest_info.get("qty", 0) if yest_info else 0
        diff_q    = info["qty"] - yest_q_p
        diff_str_p = f"{diff_q:+d}" if yest_q_p > 0 else "-"
        rows_top.append({
            "순위": i + 1,
            "상품명": name,
            "판매수량": info["qty"],
            "매출액": f"₩{info['amount']:,}",
            "전일 대비": diff_str_p,
        })
    st.dataframe(pd.DataFrame(rows_top).set_index("순위"), use_container_width=True)
else:
    st.info("판매 데이터가 없습니다.")

# 급상승 / 급감
today_sales = {name: info["qty"] for name, info in get_top_products(orders, n=50)}
rise_items, drop_items = [], []
for name, t_qty in today_sales.items():
    y_info = yest_products.get(name, {})
    y_qty  = y_info.get("qty", 0) if y_info else 0
    if y_qty > 0:
        chg = t_qty - y_qty
        if chg > 0:
            rise_items.append((name, chg))
        elif chg < 0:
            drop_items.append((name, chg))

rise_items.sort(key=lambda x: -x[1])
drop_items.sort(key=lambda x: x[1])

c_rise, c_drop = st.columns(2)
with c_rise:
    st.markdown("##### 📈 오늘 급상승 상품")
    if rise_items:
        for name, chg in rise_items[:3]:
            st.markdown(f"<span class='green'>+{chg}개</span> {name}", unsafe_allow_html=True)
    else:
        st.caption("해당 없음")

with c_drop:
    st.markdown("##### 📉 오늘 급감 상품")
    if drop_items:
        for name, chg in drop_items[:3]:
            st.markdown(f"<span class='red'>{chg}개</span> {name}", unsafe_allow_html=True)
    else:
        st.caption("해당 없음")

st.divider()


# ── 8. 주문 상태 및 이슈 ──────────────────────────────────────────────────────

st.subheader("📋 주문 상태 및 이슈")

agg     = aggregate_by_status(orders)
present = [(s, STATUS_KR.get(s, s)) for s in STATUS_KR if agg["count"].get(s, 0) > 0]
for raw in agg["count"]:
    if raw not in STATUS_KR:
        present.append((raw, raw))

if not present:
    st.info("오늘 주문 데이터가 없습니다.")
else:
    cols = st.columns(min(len(present), 4))
    for idx, (raw, kr) in enumerate(present):
        cnt   = agg["count"].get(raw, 0)
        amt   = agg["amount"].get(raw, 0)
        color = STATUS_COLOR.get(kr, "#757575")
        with cols[idx % 4]:
            st.markdown(f"""
            <div style="background:{color}18; border-left:4px solid {color};
                        border-radius:8px; padding:16px; margin-bottom:12px;">
                <div style="font-size:13px; color:{color}; font-weight:600;">{kr}</div>
                <div style="font-size:28px; font-weight:700; color:#1a1a1a;">{cnt}<span style="font-size:14px;"> 건</span></div>
                <div style="font-size:12px; color:#555;">₩{amt:,}</div>
            </div>
            """, unsafe_allow_html=True)

with st.expander("📋 전체 주문 목록 보기"):
    rows_all = []
    for wrap in orders:
        o = wrap.get("productOrder", wrap)
        raw_status = o.get("productOrderStatus", "")
        rows_all.append({
            "주문번호": o.get("productOrderId", ""),
            "상품명":   o.get("productName", ""),
            "수량":    int(o.get("quantity", 1)),
            "금액":    f"₩{int(o.get('totalPaymentAmount', 0)):,}",
            "상태":    STATUS_KR.get(raw_status, raw_status),
        })
    if rows_all:
        st.dataframe(pd.DataFrame(rows_all), use_container_width=True)
    else:
        st.info("주문 없음")

# ── 자동 새로고침 (5분) ────────────────────────────────────────────────────────

st.markdown("""
<script>
setTimeout(function() { window.location.reload(); }, 300000);
</script>
""", unsafe_allow_html=True)
