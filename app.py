import sqlite3
from datetime import datetime, date
from typing import List, Optional, Tuple

import streamlit as st

# (집에서만) 자동 현재가용: stooq (회사망 차단이면 실패할 수 있음)
import requests
import csv
import io

DB_PATH = "portfolio.db"


# =============================
# Price Fetch (optional)
# =============================
def fetch_price_stooq(stooq_symbol: str) -> Tuple[float, str, str]:
    """
    stooq_symbol example: 'AAPL.US'
    Returns: (close_price, asof_iso, source)
    """
    url = f"https://stooq.com/q/l/?s={stooq_symbol}&f=sd2t2ohlcv&h&e=csv"
    r = requests.get(url, timeout=5)
    r.raise_for_status()

    f = io.StringIO(r.text)
    reader = csv.DictReader(f)
    row = next(reader, None)
    if not row or not row.get("Close"):
        raise ValueError(f"Stooq returned no data for {stooq_symbol}")

    price = float(row["Close"])
    asof = datetime.now().replace(microsecond=0).isoformat(sep=" ")
    return price, asof, "stooq"


def fetch_current_price_for_ticker(ticker: str) -> Tuple[float, str, str]:
    """
    간단 규칙:
    - '.’ 없으면 US로 가정해 STQOO 심볼로 변환(AAPL -> AAPL.US)
    - 국내 6자리 자동조회는 현재 미구현(수동 입력 사용 권장)
    """
    t = ticker.strip().upper()
    if t.isdigit() and len(t) == 6:
        raise ValueError("국내(6자리) 자동조회는 현재 미구현입니다. 수동 현재가를 사용하십시오.")

    if "." not in t:
        t = f"{t}.US"

    return fetch_price_stooq(t)


# =============================
# DB
# =============================
def get_conn():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init_db(conn: sqlite3.Connection):
    cur = conn.cursor()

    # 종목(밭)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS securities (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ticker TEXT NOT NULL UNIQUE,
        name TEXT
    );
    """)

    # 개체(작물): 1주 단위
    cur.execute("""
    CREATE TABLE IF NOT EXISTS lots (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        security_id INTEGER NOT NULL,
        buy_datetime TEXT NOT NULL,
        buy_price REAL NOT NULL,
        status TEXT NOT NULL CHECK(status IN ('OPEN','CLOSED')) DEFAULT 'OPEN',
        sell_datetime TEXT,
        sell_price REAL,
        FOREIGN KEY (security_id) REFERENCES securities(id)
    );
    """)

    # 현재가 캐시(종목 1개당 1개)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS prices (
        security_id INTEGER PRIMARY KEY,
        asof_datetime TEXT NOT NULL,
        price REAL NOT NULL,
        FOREIGN KEY (security_id) REFERENCES securities(id)
    );
    """)

    # 원장(감사용/추적용): 수수료 없이 BUY/SELL만
    cur.execute("""
    CREATE TABLE IF NOT EXISTS transactions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        security_id INTEGER NOT NULL,
        type TEXT NOT NULL CHECK(type IN ('BUY','SELL')),
        datetime TEXT NOT NULL,
        quantity INTEGER NOT NULL,
        price REAL NOT NULL,
        note TEXT,
        FOREIGN KEY (security_id) REFERENCES securities(id)
    );
    """)

    conn.commit()


# =============================
# Helpers
# =============================
def iso_now():
    return datetime.now().replace(microsecond=0).isoformat(sep=" ")

def iso_today() -> str:
    return date.today().isoformat()  # 'YYYY-MM-DD'

def parse_dt(s: str) -> datetime:
    s = (s or "").strip()

    # 1) 날짜만
    try:
        return datetime.strptime(s, "%Y-%m-%d")
    except ValueError:
        pass

    # 2) 날짜 + 분
    try:
        return datetime.strptime(s, "%Y-%m-%d %H:%M")
    except ValueError:
        pass

    # 3) 날짜 + 초
    return datetime.strptime(s, "%Y-%m-%d %H:%M:%S")


def days_since(buy_dt: datetime, today: Optional[date] = None) -> int:
    if today is None:
        today = date.today()
    return (today - buy_dt.date()).days


def pct(a: float, b: float) -> float:
    if b == 0:
        return 0.0
    return (a / b - 1.0) * 100.0

def farm_stage(return_pct: Optional[float]) -> tuple[str, str]:
    """
    return_pct: 수익률(%). None이면 현재가 미입력.
    Returns: (stage_name, emoji)
    """
    if return_pct is None:
        return ("가격 미입력", "❔")

    if return_pct <= -10.0:
        return ("썩은 식물", "🪰")   # 또는 🥀
    elif return_pct < 0.0:
        return ("시든 식물", "🥀")
    elif return_pct < 10.0:
        return ("새싹 식물", "🌱")
    else:
        return ("만개한 꽃", "🌸")

# =============================
# CRUD
# =============================
def upsert_security(conn, ticker: str, name: str = "") -> int:
    ticker = ticker.strip().upper()
    name = (name or "").strip()
    cur = conn.cursor()
    cur.execute("INSERT OR IGNORE INTO securities(ticker, name) VALUES (?, ?)", (ticker, name))
    if name:
        cur.execute("UPDATE securities SET name=? WHERE ticker=?", (name, ticker))
    conn.commit()
    cur.execute("SELECT id FROM securities WHERE ticker=?", (ticker,))
    row = cur.fetchone()
    return int(row["id"])


def list_securities(conn):
    cur = conn.cursor()
    cur.execute("SELECT id, ticker, name FROM securities ORDER BY ticker ASC")
    return cur.fetchall()


def set_price(conn, security_id: int, price: float, asof: Optional[str] = None):
    if asof is None:
        asof = iso_now()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO prices(security_id, asof_datetime, price)
        VALUES (?, ?, ?)
        ON CONFLICT(security_id) DO UPDATE SET
            asof_datetime=excluded.asof_datetime,
            price=excluded.price
    """, (security_id, asof, float(price)))
    conn.commit()


def get_price(conn, security_id: int) -> Optional[Tuple[str, float]]:
    cur = conn.cursor()
    cur.execute("SELECT asof_datetime, price FROM prices WHERE security_id=?", (security_id,))
    row = cur.fetchone()
    if not row:
        return None
    return (row["asof_datetime"], float(row["price"]))


def add_buy(conn, security_id: int, buy_dt: str, buy_price: float, qty: int, note: str = ""):
    buy_dt = buy_dt.strip()
    qty = int(qty)
    if qty <= 0:
        raise ValueError("매수 수량은 1 이상이어야 합니다.")

    cur = conn.cursor()
    cur.execute("""
        INSERT INTO transactions(security_id, type, datetime, quantity, price, note)
        VALUES (?, 'BUY', ?, ?, ?, ?)
    """, (security_id, buy_dt, qty, float(buy_price), note))

    cur.executemany("""
        INSERT INTO lots(security_id, buy_datetime, buy_price, status)
        VALUES (?, ?, ?, 'OPEN')
    """, [(security_id, buy_dt, float(buy_price)) for _ in range(qty)])

    conn.commit()


def get_open_lots(conn, security_id: int):
    cur = conn.cursor()
    cur.execute("""
        SELECT id, buy_datetime, buy_price
        FROM lots
        WHERE security_id=? AND status='OPEN'
        ORDER BY buy_datetime ASC, id ASC
    """, (security_id,))
    return cur.fetchall()


def get_open_lot_batches(conn, security_id: int):
    cur = conn.cursor()
    cur.execute("""
        SELECT
            buy_datetime,
            buy_price,
            COUNT(*) AS qty,
            MIN(id) AS first_lot_id,
            MAX(id) AS last_lot_id
        FROM lots
        WHERE security_id=? AND status='OPEN'
        GROUP BY buy_datetime, buy_price
        ORDER BY buy_datetime ASC, buy_price ASC
    """, (security_id,))
    return cur.fetchall()

def get_open_lots_in_batch(conn, security_id: int, buy_datetime: str, buy_price: float):
    """
    특정 덩어리(같은 buy_datetime + buy_price)에 속한 OPEN lot들을 id 오름차순으로 반환
    """
    cur = conn.cursor()
    cur.execute("""
        SELECT id, buy_datetime, buy_price
        FROM lots
        WHERE security_id=? AND status='OPEN'
          AND buy_datetime=? AND buy_price=?
        ORDER BY id ASC
    """, (security_id, buy_datetime, float(buy_price)))
    return cur.fetchall()

def get_closed_lots(conn, security_id: int, limit: int = 200):
    cur = conn.cursor()
    cur.execute("""
        SELECT id, buy_datetime, buy_price, sell_datetime, sell_price
        FROM lots
        WHERE security_id=? AND status='CLOSED'
        ORDER BY sell_datetime DESC, id DESC
        LIMIT ?
    """, (security_id, limit))
    return cur.fetchall()


def pick_lots_auto(open_lots, rule: str, now_price: Optional[float]) -> List[int]:
    if rule == "FIFO":
        sorted_lots = list(open_lots)
    elif rule == "LIFO":
        sorted_lots = list(reversed(open_lots))
    elif rule == "LONGEST_HELD":
        sorted_lots = list(open_lots)
    elif rule in ("HIGHEST_GAIN", "LOWEST_GAIN"):
        if now_price is None:
            sorted_lots = list(open_lots)
        else:
            lots_with_gain = [(now_price - float(r["buy_price"]), r) for r in open_lots]
            lots_with_gain.sort(key=lambda x: x[0], reverse=(rule == "HIGHEST_GAIN"))
            sorted_lots = [r for _, r in lots_with_gain]
    else:
        sorted_lots = list(open_lots)

    return [int(r["id"]) for r in sorted_lots]


def sell_lots(conn, security_id: int, lot_ids: List[int], sell_dt: str, sell_price: float, note: str = ""):
    if not lot_ids:
        raise ValueError("매도할 개체가 선택되지 않았습니다.")

    sell_dt = sell_dt.strip()
    cur = conn.cursor()

    cur.execute("""
        INSERT INTO transactions(security_id, type, datetime, quantity, price, note)
        VALUES (?, 'SELL', ?, ?, ?, ?)
    """, (security_id, sell_dt, len(lot_ids), float(sell_price), note))

    cur.executemany("""
        UPDATE lots
        SET status='CLOSED', sell_datetime=?, sell_price=?
        WHERE id=? AND security_id=? AND status='OPEN'
    """, [(sell_dt, float(sell_price), int(lid), int(security_id)) for lid in lot_ids])

    conn.commit()


def list_transactions(conn, security_id: Optional[int] = None, limit: int = 300):
    cur = conn.cursor()
    if security_id is None:
        cur.execute("""
            SELECT t.id, s.ticker, s.name, t.type, t.datetime, t.quantity, t.price, t.note
            FROM transactions t
            JOIN securities s ON s.id=t.security_id
            ORDER BY t.datetime DESC, t.id DESC
            LIMIT ?
        """, (limit,))
    else:
        cur.execute("""
            SELECT t.id, s.ticker, s.name, t.type, t.datetime, t.quantity, t.price, t.note
            FROM transactions t
            JOIN securities s ON s.id=t.security_id
            WHERE t.security_id=?
            ORDER BY t.datetime DESC, t.id DESC
            LIMIT ?
        """, (security_id, limit))
    return cur.fetchall()

def portfolio_total_pnl(conn) -> tuple[float, float, float, int]:
    """
    Returns: (realized_pnl, unrealized_pnl, total_pnl, missing_price_count)
    - unrealized_pnl은 현재가 있는 종목만 포함
    - missing_price_count: 현재가가 없어 평가손익에서 제외된 종목 개수
    """
    cur = conn.cursor()

    # 1) 실현손익: CLOSED lots 합계
    cur.execute("""
        SELECT COALESCE(SUM(sell_price - buy_price), 0.0) AS realized
        FROM lots
        WHERE status='CLOSED' AND sell_price IS NOT NULL
    """)
    realized = float(cur.fetchone()["realized"])

    # 2) 평가손익: OPEN lots × 종목별 현재가(있는 종목만)
    cur.execute("""
        SELECT
            COALESCE(SUM(p.price - l.buy_price), 0.0) AS unrealized
        FROM lots l
        JOIN prices p ON p.security_id = l.security_id
        WHERE l.status='OPEN'
    """)
    unrealized = float(cur.fetchone()["unrealized"])

    # 3) 현재가 없는 종목 개수(OPEN 보유 중인데 prices에 없음)
    cur.execute("""
        SELECT COUNT(*) AS cnt
        FROM (
            SELECT l.security_id
            FROM lots l
            LEFT JOIN prices p ON p.security_id = l.security_id
            WHERE l.status='OPEN'
            GROUP BY l.security_id
            HAVING MAX(CASE WHEN p.security_id IS NULL THEN 1 ELSE 0 END) = 1
        )
    """)
    missing = int(cur.fetchone()["cnt"])

    total = realized + unrealized
    return realized, unrealized, total, missing

# =============================
# UI
# =============================
st.set_page_config(page_title="주식 농장", layout="wide")
st.title("주식 농장")

conn = get_conn()
init_db(conn)

st.sidebar.header("종목(밭)")
securities = list_securities(conn)
ticker_to_id = {r["ticker"]: int(r["id"]) for r in securities}
ticker_values = [r["ticker"] for r in securities]

with st.sidebar.expander("종목 추가", expanded=False):
    new_ticker = st.text_input("티커", placeholder="예: 005930, AAPL", key="sec_new_ticker").strip()
    new_name = st.text_input("이름(선택)", placeholder="예: 삼성전자", key="sec_new_name").strip()
    if st.button("종목 추가/갱신", key="sec_upsert_btn"):
        if not new_ticker:
            st.warning("티커를 입력하십시오.")
        else:
            sid = upsert_security(conn, new_ticker, new_name)
            st.success(f"등록 완료: {new_ticker.upper()} (id={sid})")
            st.rerun()

if not ticker_values:
    st.info("좌측에서 종목을 먼저 추가하십시오.")
    st.stop()

selected_ticker = st.sidebar.selectbox("종목 선택", ticker_values, key="sec_select")
selected_security_id = ticker_to_id[selected_ticker]

st.sidebar.header("포트폴리오 요약")

realized, unrealized, total, missing = portfolio_total_pnl(conn)

st.sidebar.metric("총 수익(실현+평가)", f"{total:,.4f}")
st.sidebar.metric("실현손익", f"{realized:,.4f}")
st.sidebar.metric("평가손익", f"{unrealized:,.4f}")

if missing > 0:
    st.sidebar.caption(f"현재가 미입력 종목 {missing}개는 평가손익에서 제외됨")

# 탭
tab1, tab2, tab3, tab4 = st.tabs(["대시보드", "매수(심기)", "매도(수확)", "원장/히스토리"])


# --- Dashboard
with tab1:
    st.subheader(f"대시보드: {selected_ticker}")

    # 현재가: 수동 입력(기본)
    st.markdown("### 현재가")
    cur_price_info = get_price(conn, selected_security_id)
    default_price = cur_price_info[1] if cur_price_info else 0.0

    price_key = f"price_manual_{selected_security_id}"
    save_key  = f"price_save_{selected_security_id}"
    auto_key  = f"price_auto_{selected_security_id}"

    cA, cB, cC = st.columns([1.2, 1, 1])
    with cA:
        price_input = st.number_input(
            "현재가(수동)",
            min_value=0.0,
            value=float(default_price),
            step=0.01,
            key=price_key,
        )
    with cB:
        if st.button("현재가 저장", key=save_key):
            set_price(conn, selected_security_id, float(price_input))
            st.success("저장 완료")
            st.rerun()
    with cC:
        if cur_price_info:
            st.caption(f"기준시각: {cur_price_info[0]}")

    # (선택) 자동 조회 버튼: 집에서만
    with st.expander("현재가 자동 가져오기(집 네트워크용)", expanded=False):
        st.caption("회사망에서 차단될 수 있습니다. 실패 시 수동 입력을 사용하십시오.")
        if st.button("자동 조회 실행(선택 종목)", key=auto_key):
            try:
                price, asof, source = fetch_current_price_for_ticker(selected_ticker)
                set_price(conn, selected_security_id, float(price), asof)
                st.success(f"자동 조회 성공: {price} (asof={asof}, source={source})")
                st.rerun()
            except Exception as e:
                st.error(f"자동 조회 실패: {e}")

    open_lots = get_open_lots(conn, selected_security_id)
    price_info = get_price(conn, selected_security_id)
    now_price = price_info[1] if price_info else None

    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.metric("보유 개체 수(주)", len(open_lots))
    with col2:
        st.metric("현재가", "미입력" if now_price is None else f"{now_price:,.4f}")
    with col3:
        total_cost = sum(float(r["buy_price"]) for r in open_lots)
        st.metric("총 매수금액(원가 합)", f"{total_cost:,.4f}")
    with col4:
        if now_price is None:
            st.metric("평가손익", "현재가 필요")
        else:
            total_value = now_price * len(open_lots)
            st.metric("평가손익", f"{(total_value - total_cost):,.4f}")

    # -----------------------------
    # 농장 상태(이 종목)
    # -----------------------------
    st.markdown("### 농장 상태(이 종목)")
    if now_price is None or len(open_lots) == 0:
        st.info("현재가가 없거나 보유 개체가 없습니다.")
    else:
        avg_buy = sum(float(r["buy_price"]) for r in open_lots) / len(open_lots)
        rr_avg = pct(now_price, avg_buy)
        stage_avg, icon_avg = farm_stage(rr_avg)

        c1, c2, c3 = st.columns([1, 1, 2])
        with c1:
            st.metric("수익률(평균단가 기준)", f"{rr_avg:,.2f}%")
        with c2:
            st.metric("상태", f"{icon_avg} {stage_avg}")
        with c3:
            st.caption("기준: -10% 이하=썩은 / -10~0=시든 / 0~10=새싹 / 10% 이상=만개")

    # -----------------------------
    # 덩어리(묶음) 요약 + 개별 보기
    # -----------------------------
    batches = get_open_lot_batches(conn, selected_security_id)

    if batches:
        st.markdown("### 보유 요약(같이 산 덩어리 기준)")
        rows = []

        for b in batches:
            bd = parse_dt(b["buy_datetime"])
            bp = float(b["buy_price"])
            qty = int(b["qty"])
            d = days_since(bd)

            if now_price is None:
                pnl = None
                rr = None
                stage, icon = farm_stage(None)
            else:
                pnl = (now_price - bp) * qty
                rr = pct(now_price, bp)
                stage, icon = farm_stage(rr)

            rows.append({
                "농장 상태": f"{icon} {stage}",
                "매수일": b["buy_datetime"][:10],
                "매수가": bp,
                "수량": qty,
                "D+": d,
                "평가손익": pnl,
                "수익률(%)": rr,
                "lot 범위": f'{int(b["first_lot_id"])}–{int(b["last_lot_id"])}',
            })

        st.dataframe(rows, use_container_width=True, hide_index=True)

        with st.expander("1주 개체(작물) 상세 보기", expanded=False):
            today = date.today()
            detail = []

            for r in open_lots:
                bd = parse_dt(r["buy_datetime"])
                bp = float(r["buy_price"])
                d = days_since(bd, today=today)

                if now_price is None:
                    rr = None
                    pp = None
                    stage, icon = farm_stage(None)
                else:
                    rr = pct(now_price, bp)
                    pp = now_price - bp
                    stage, icon = farm_stage(rr)

                detail.append({
                    "lot_id": int(r["id"]),
                    "농장 상태": f"{icon} {stage}",
                    "매수일": r["buy_datetime"][:10],
                    "D+": d,
                    "매수가": bp,
                    "평가손익/주": pp,
                    "수익률(%)": rr,
                })

            st.dataframe(detail, use_container_width=True, hide_index=True)

    else:
        st.info("보유 중인 개체가 없습니다. '매수(심기)'에서 추가하십시오.")
                
# --- Buy
with tab2:
    st.subheader("매수(심기)")
    c1, c2, c3, c4 = st.columns([2, 2, 1, 2])

    with c1:
        buy_date = st.date_input("매수일자", value=date.today(), key=f"buy_date_{selected_security_id}")
        buy_dt = buy_date.isoformat()  # DB에는 'YYYY-MM-DD'로 저장
    with c2:
        buy_price = st.number_input("매수가(체결단가)", min_value=0.0, value=0.0, step=0.01, key="buy_price_input")
    with c3:
        buy_qty = st.number_input("수량(주)", min_value=1, value=1, step=1, key="buy_qty_input")
    with c4:
        buy_note = st.text_input("메모(선택)", value="", key="buy_note_input")

    if st.button("매수 기록 및 개체 생성", key="buy_submit_btn"):
        try:
            _ = parse_dt(buy_dt)
            add_buy(conn, selected_security_id, buy_dt, buy_price, int(buy_qty), buy_note)
            st.success(f"매수 완료: {selected_ticker} {int(buy_qty)}주 (1주 개체 {int(buy_qty)}개 생성)")
            st.rerun()
        except Exception as e:
            st.error(f"오류: {e}")


# --- Sell
with tab3:
    st.subheader("매도(수확)")
    open_lots = get_open_lots(conn, selected_security_id)
    price_info = get_price(conn, selected_security_id)
    now_price = price_info[1] if price_info else None

    if not open_lots:
        st.info("매도할 보유 개체가 없습니다.")
        st.stop()

    # -----------------------------
    # 종목별 key 분리
    # -----------------------------
    sid = selected_security_id
    sell_dt_key = f"sell_datetime_input_{sid}"
    sell_price_key = f"sell_price_input_{sid}"              # 위젯 키(직접 수정 금지)
    sell_price_default_key = f"sell_price_default_{sid}"    # 기본값/동기화용 키(이것만 수정)
    sell_qty_key = f"sell_qty_input_{sid}"
    sell_mode_key = f"sell_mode_{sid}"
    sell_rule_key = f"sell_rule_{sid}"
    sell_note_key = f"sell_note_input_{sid}"
    sell_manual_ids_key = f"sell_manual_ids_{sid}"
    sell_batch_select_key = f"sell_batch_select_{sid}"
    sell_sync_key = f"sell_price_sync_{sid}"
    sell_submit_key = f"sell_submit_{sid}"

    # -----------------------------
    # 매도가 기본값 초기화(한 번만)
    # - 위젯 키(sell_price_key)는 건드리지 않음
    # -----------------------------
    if sell_price_default_key not in st.session_state:
        st.session_state[sell_price_default_key] = float(now_price or 0.0)

    left, right = st.columns([1, 2])

    # 덩어리 모드에서 right가 참조할 변수
    batch_lots = []

    with left:
        sell_date = st.date_input("매도일자", value=date.today(), key=f"sell_date_{selected_security_id}")
        sell_dt = sell_date.isoformat()
        if now_price is None:
            st.warning("현재가가 입력되어 있지 않습니다. 매도가를 직접 입력하십시오.")
        else:
            st.caption(f"현재가: {now_price:,.4f}")

        # 매도가(위젯)
        sell_price = st.number_input(
            "매도가(체결단가)",
            min_value=0.0,
            value=float(st.session_state[sell_price_default_key]),
            step=0.01,
            key=sell_price_key,
        )

        # 현재가로 맞춤 버튼: default_key만 바꾸고 rerun
        if st.button("매도가를 현재가로 맞춤", key=sell_sync_key, disabled=(now_price is None)):
            st.session_state[sell_price_default_key] = float(now_price or 0.0)
            st.rerun()

        sell_qty = st.number_input(
            "매도 수량(주)",
            min_value=1,
            max_value=len(open_lots),
            value=1,
            step=1,
            key=sell_qty_key,
        )

        mode = st.radio(
            "선택 방식",
            ["직접 고르기", "규칙으로 고르기", "덩어리에서 고르기"],
            horizontal=False,
            key=sell_mode_key,
        )

        rule_map = {
            "FIFO(먼저 산 것부터)": "FIFO",
            "LIFO(나중에 산 것부터)": "LIFO",
            "보유기간 긴 것부터": "LONGEST_HELD",
            "수익 큰 것부터(현재가 기반)": "HIGHEST_GAIN",
            "손실 큰 것부터(현재가 기반)": "LOWEST_GAIN",
        }

        rule = None
        if mode == "규칙으로 고르기":
            rule_label = st.selectbox("규칙", list(rule_map.keys()), key=sell_rule_key)
            rule = rule_map[rule_label]

        # 덩어리 선택 UI
        if mode == "덩어리에서 고르기":
            batches = get_open_lot_batches(conn, sid)
            if not batches:
                st.warning("선택할 덩어리가 없습니다.")
                batch_lots = []
            else:
                batch_labels = []
                batch_keys = []
                for b in batches:
                    label = f'{b["buy_datetime"]} | {float(b["buy_price"]):,.4f} | {int(b["qty"])}주'
                    batch_labels.append(label)
                    batch_keys.append((b["buy_datetime"], float(b["buy_price"]), int(b["qty"])))

                idx = st.selectbox(
                    "덩어리 선택(매수 묶음)",
                    range(len(batch_labels)),
                    format_func=lambda i: batch_labels[i],
                    key=sell_batch_select_key,
                )

                buy_dt, buy_price, _ = batch_keys[idx]
                batch_lots = get_open_lots_in_batch(conn, sid, buy_dt, buy_price)
                st.caption(f"선택 덩어리의 현재 보유 수량: {len(batch_lots)}주")

                if int(sell_qty) > len(batch_lots):
                    st.warning(
                        f"매도 수량({int(sell_qty)})이 덩어리 보유수량({len(batch_lots)})보다 큽니다. 수량을 줄이십시오."
                    )

        sell_note = st.text_input("메모(선택)", value="", key=sell_note_key)

    with right:
        # 보유 lot 테이블(매도 선택 참고용)
        today = date.today()
        lots_rows = []
        for r in open_lots:
            bd = parse_dt(r["buy_datetime"])
            bp = float(r["buy_price"])
            d = days_since(bd, today=today)
            est_pnl = float(sell_price) - bp
            est_ret = pct(float(sell_price), bp)
            lots_rows.append({
                "번호": int(r["id"]),
                "구매일자": r["buy_datetime"],
                "경과일": d,
                "구매가": bp,
                "평가손익": est_pnl,
                "수익율%": est_ret,
            })

        st.caption("보유 개체(작물) — 매도 선택 대상")
        st.dataframe(lots_rows, use_container_width=True, hide_index=True)

        # -----------------------------
        # selected_ids 결정
        # -----------------------------
        if mode == "규칙으로 고르기":
            sorted_ids = pick_lots_auto(open_lots, rule, now_price=now_price)
            selected_ids = sorted_ids[: int(sell_qty)]
            st.info(f"자동 선택됨: {len(selected_ids)}개")
            st.write(selected_ids)

        elif mode == "덩어리에서 고르기":
            if not batch_lots:
                selected_ids = []
                st.warning("덩어리를 먼저 선택하십시오.")
            elif int(sell_qty) > len(batch_lots):
                selected_ids = []
                st.error("매도 수량이 덩어리 보유수량보다 큽니다. 수량을 줄이십시오.")
            else:
                selected_ids = [int(r["id"]) for r in batch_lots[: int(sell_qty)]]
                st.info(f"덩어리에서 선택됨: {len(selected_ids)}개")
                st.write(selected_ids)

        else:
            all_ids = [int(r["id"]) for r in open_lots]
            selected_ids = st.multiselect(
                "매도할 lot_id를 선택 (선택 개수 = 매도 수량과 일치해야 함)",
                options=all_ids,
                default=all_ids[: int(sell_qty)],
                key=sell_manual_ids_key,
            )

        # -----------------------------
        # 미리보기(예상 실현손익)
        # -----------------------------
        id_to_bp = {int(r["id"]): float(r["buy_price"]) for r in open_lots}

        if selected_ids:
            total_buy = sum(id_to_bp[i] for i in selected_ids)
            total_sell = float(sell_price) * len(selected_ids)
            total_pnl = total_sell - total_buy
            st.metric("예상 실현손익(합계)", f"{total_pnl:,.4f}")
            if total_buy != 0:
                st.metric("예상 실현수익률(합계 기준)", f"{pct(total_sell, total_buy):,.4f}%")

        # -----------------------------
        # 매도 확정
        # -----------------------------
        if st.button("매도 확정(선택된 개체 수확)", key=sell_submit_key):
            try:
                _ = parse_dt(sell_dt)

                if len(selected_ids) != int(sell_qty):
                    raise ValueError(
                        f"선택된 개체 수({len(selected_ids)})와 매도 수량({int(sell_qty)})이 일치해야 합니다."
                    )

                # 덩어리 모드에서 sell_qty가 덩어리 보유수량 초과면 방어
                if mode == "덩어리에서 고르기" and int(sell_qty) > len(batch_lots):
                    raise ValueError("덩어리 보유수량보다 큰 수량을 매도할 수 없습니다.")

                sell_lots(conn, sid, selected_ids, sell_dt, float(sell_price), sell_note)

                # 매도 후 다음 진입 시 기본값을 현재가로 다시 잡고 싶으면(선택):
                # st.session_state.pop(sell_price_default_key, None)

                st.success(f"매도 완료: {selected_ticker} {len(selected_ids)}주")
                st.rerun()

            except Exception as e:
                st.error(f"오류: {e}")
# --- Ledger / History
with tab4:
    st.subheader("원장/히스토리")

    c1, c2 = st.columns([1, 1])
    with c1:
        show_scope = st.radio("표시 범위", ["선택 종목만", "전체 종목"], horizontal=True, key="ledger_scope")
    with c2:
        limit = st.number_input("최대 표시 행", min_value=50, max_value=1000, value=300, step=50, key="ledger_limit")

    txs = list_transactions(conn, None if show_scope == "전체 종목" else selected_security_id, int(limit))
    tx_rows = [{
        "id": int(r["id"]),
        "ticker": r["ticker"],
        "name": r["name"],
        "type": r["type"],
        "datetime": r["datetime"],
        "quantity": int(r["quantity"]),
        "price": float(r["price"]),
        "note": r["note"],
    } for r in txs]

    st.dataframe(tx_rows, use_container_width=True, hide_index=True)

    st.caption("최근 CLOSED 개체(수확 완료) 일부")
    closed = get_closed_lots(conn, selected_security_id, limit=200)
    closed_rows = []
    for r in closed:
        bp = float(r["buy_price"])
        sp = float(r["sell_price"]) if r["sell_price"] is not None else None
        bd = parse_dt(r["buy_datetime"])
        sd = parse_dt(r["sell_datetime"]) if r["sell_datetime"] else None
        hold_days = (sd.date() - bd.date()).days if sd else None
        pnl = (sp - bp) if (sp is not None) else None
        rr = pct(sp, bp) if (sp is not None) else None
        closed_rows.append({
            "lot_id": int(r["id"]),
            "buy_datetime": r["buy_datetime"],
            "sell_datetime": r["sell_datetime"],
            "hold_days": hold_days,
            "buy_price": bp,
            "sell_price": sp,
            "realized_pnl_per_share": pnl,
            "realized_return_%": rr,
        })
    st.dataframe(closed_rows, use_container_width=True, hide_index=True)

st.caption("MVP: 수수료/세금/기업행사(분할·배당 등)는 제외. 현재가는 기본적으로 수동 입력 기반.")
