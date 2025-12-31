import streamlit as st
import yfinance as yf
import datetime
import pandas as pd
import time
from sheet_manager import SheetManager

# --- Config & Setup ---
st.set_page_config(page_title="주식 농장 (Stock Farm)", page_icon="🌿", layout="wide")

# --- Helper Functions ---
def get_current_price(ticker):
    try:
        return yf.Ticker(ticker).fast_info.last_price
    except:
        return 0.0

def get_status_emoji(profit_rate):
    if profit_rate < -20: return "☠️"
    elif profit_rate < 0: return "🍂"
    elif profit_rate < 10: return "🌱"
    else: return "🌳"

# --- Main App ---
def main():
    st.title("🌿 주식 농장 (Stock Farm)")
    
    # 1. Initialize Sheet Manager
    if "sheet_manager" not in st.session_state:
        st.session_state.sheet_manager = SheetManager()
        
    sm = st.session_state.sheet_manager
    if not sm.client:
        st.stop() # Stop if connection failed

    # 2. Login (Nickname)
    if "user_nickname" not in st.session_state:
        with st.form("login_form"):
            st.header("로그인 (닉네임 입력)")
            nickname = st.text_input("닉네임 (예: 홍길동)")
            submitted = st.form_submit_button("농장 입장하기")
            
            if submitted:
                if nickname:
                    st.session_state.user_nickname = nickname
                    st.success(f"환영합니다, {nickname}님!")
                    st.rerun()
                else:
                    st.error("닉네임을 입력해주세요.")
        return # Stop execution until logged in

    user = st.session_state.user_nickname
    st.sidebar.write(f"👤 **{user}**님의 농장")
    if st.sidebar.button("로그아웃"):
        del st.session_state.user_nickname
        st.rerun()

    # Load Data (from Sheet)
    crops = sm.load_farm(user)
    history = sm.load_history(user)
    
    # Sidebar Menu
    menu = st.sidebar.radio("메뉴", ["농장 (Farm)", "작물 심기 (Plant)", "수확 하기 (Harvest)", "장부 (History)"])
    
    if menu == "농장 (Farm)":
        show_farm(crops)
    elif menu == "작물 심기 (Plant)":
        show_plant(sm, user)
    elif menu == "수확 하기 (Harvest)":
        show_harvest(sm, user, crops)
    elif menu == "장부 (History)":
        show_history(history)

def show_farm(crops):
    st.header("🏡 농장 현황")
    
    if not crops:
        st.info("농장이 비어있습니다. '작물 심기' 메뉴에서 작물을 추가하세요!")
        return

    # Process Data for Display
    rows = []
    total_buy = 0
    total_val = 0
    
    progress_bar = st.progress(0)
    status_text = st.empty()
    
    for i, crop in enumerate(crops):
        status_text.text(f"Updating {crop['ticker']}...")
        current_price = get_current_price(crop["ticker"])
        progress_bar.progress((i + 1) / len(crops))
        
        profit_rate = ((current_price - crop["buy_price"]) / crop["buy_price"]) * 100 if crop["buy_price"] > 0 else 0
        profit_amt = (current_price - crop["buy_price"]) * crop["quantity"]
        
        # Daily Logic
        buy_dt = datetime.datetime.strptime(crop["buy_date"], "%Y-%m-%d")
        days = max(1, (datetime.datetime.now() - buy_dt).days)
        daily_rate = profit_rate / days
        
        total_buy += crop["buy_price"] * crop["quantity"]
        total_val += current_price * crop["quantity"]
        
        rows.append({
            "상태": get_status_emoji(profit_rate),
            "종목": crop["ticker"],
            "매수가": f"${crop['buy_price']:.2f}",
            "현재가": f"${current_price:.2f}",
            "수익률": f"{profit_rate:.2f}%",
            "일간": f"{daily_rate:.2f}%/일",
            "수익금": f"${profit_amt:.2f}",
            "수량": crop["quantity"],
            "매수일": crop["buy_date"]
        })
    
    status_text.empty()
    progress_bar.empty()
    
    # Summary Metrics
    if total_buy > 0:
        total_profit = total_val - total_buy
        total_profit_rate = (total_profit / total_buy) * 100
        
        col1, col2, col3 = st.columns(3)
        col1.metric("총 가치", f"${total_val:,.2f}")
        col2.metric("총 매수액", f"${total_buy:,.2f}")
        col3.metric("총 수익", f"${total_profit:,.2f}", f"{total_profit_rate:.2f}%")
    
    # DataFrame Display
    st.dataframe(pd.DataFrame(rows), use_container_width=True)

def show_plant(sm, user):
    st.header("🌱 작물 심기 (매수)")
    
    # 1. Ticker Input OUTSIDE form
    ticker = st.text_input("종목 코드 (예: AAPL)", key="plant_ticker").upper()
    
    price_guess = 0.01 
    if ticker:
         st.caption(f"Fetching current price for {ticker}...")
         fetched_price = get_current_price(ticker)
         if fetched_price > 0:
             price_guess = fetched_price
         st.markdown(f"**현재 추정가: ${price_guess:.2f}**")
    
    with st.form("plant_form"):
        date_picked = st.date_input("매수 날짜", datetime.date.today())
        
        price = st.number_input("매수가 ($)", min_value=0.01, value=price_guess, format="%.2f", key=f"price_{ticker}")
        qty = st.number_input("수량", min_value=1, value=1)
        
        submitted = st.form_submit_button("심기 (확인)")
        
        if submitted:
            if not ticker:
                st.error("종목 코드를 입력하세요.")
            else:
                new_crop = {
                    "ticker": ticker,
                    "buy_price": price,
                    "quantity": qty,
                    "buy_date": date_picked.strftime("%Y-%m-%d")
                }
                
                # Save to Sheet
                sm.save_crop(user, new_crop)
                
                # Log Transaction
                current_time_str = datetime.datetime.now().strftime("%H:%M:%S")
                timestamp = f"{date_picked.strftime('%Y-%m-%d')} {current_time_str}"
                
                log = {
                    "time": timestamp,
                    "type": "매수",
                    "ticker": ticker,
                    "price": price,
                    "quantity": qty,
                    "date": date_picked.strftime("%Y-%m-%d"),
                    "profit_rate": None,
                    "profit_amt": None
                }
                sm.log_transaction(user, log)
                
                st.success(f"{ticker} {qty}주를 심었습니다!")
                st.cache_data.clear()

def show_harvest(sm, user, crops):
    st.header("🚜 수확 하기 (매도)")
    
    if not crops:
        st.warning("수확할 작물이 없습니다.")
        return

    # Select Crop
    crop_options = [f"{i}: {c['ticker']} (매수: ${c['buy_price']:.2f}, 수량: {c['quantity']})" for i, c in enumerate(crops)]
    selected_idx_str = st.selectbox("작물 선택", crop_options)
    
    if selected_idx_str:
        idx = int(selected_idx_str.split(":")[0])
        target_crop = crops[idx]
        
        with st.form("harvest_form"):
            st.info(f"선택됨: {target_crop['ticker']} (보유: {target_crop['quantity']}주)")
            
            qty_to_sell = st.number_input("수확(매도) 수량", min_value=1, max_value=target_crop["quantity"], value=target_crop["quantity"])
            
            current_price_guess = get_current_price(target_crop['ticker'])
            sell_price = st.number_input("매도 단가 ($)", min_value=0.01, value=current_price_guess, format="%.2f")
            
            sell_date = st.date_input("매도 날짜", datetime.date.today())

            submitted = st.form_submit_button("수확 하기 (확인)")
            
            if submitted:
                # Logic
                profit_rate = ((sell_price - target_crop["buy_price"]) / target_crop["buy_price"]) * 100
                profit_amt = (sell_price - target_crop["buy_price"]) * qty_to_sell
                
                # Update Sheet
                if qty_to_sell == target_crop["quantity"]:
                    # Full Sell
                    sm.remove_crop(user, idx) # Note: index based removal relies on list view stability
                    st.success(f"{target_crop['ticker']} 전체 수확 완료!")
                else:
                    # Partial Sell
                    new_qty = target_crop["quantity"] - qty_to_sell
                    sm.update_crop_qty(user, idx, new_qty)
                    st.success(f"{target_crop['ticker']} {qty_to_sell}주 부분 수확 완료!")

                # Log
                current_time_str = datetime.datetime.now().strftime("%H:%M:%S")
                timestamp = f"{sell_date.strftime('%Y-%m-%d')} {current_time_str}"
                
                log = {
                    "time": timestamp,
                    "type": "매도",
                    "ticker": target_crop['ticker'],
                    "price": sell_price,
                    "quantity": qty_to_sell,
                    "date": sell_date.strftime("%Y-%m-%d"),
                    "profit_rate": profit_rate,
                    "profit_amt": profit_amt
                }
                sm.log_transaction(user, log)
                
                st.rerun()

def show_history(history):
    st.header("📜 거래 장부 (History)")
    
    if not history:
        st.info("거래 내역이 없습니다.")
        return

    # Grouping Logic
    history_rev = history[::-1]
    df = pd.DataFrame(history_rev)
    
    # Calculate Totals
    total_buy = df[df['type'] == '매수'].apply(lambda x: x['price'] * x['quantity'], axis=1).sum()
    total_sell = df[df['type'] == '매도'].apply(lambda x: x['price'] * x['quantity'], axis=1).sum()
    total_profit = df['profit_amt'].sum()
    
    c1, c2, c3 = st.columns(3)
    c1.metric("총 매수액", f"${total_buy:,.2f}")
    c2.metric("총 매도액", f"${total_sell:,.2f}")
    c3.metric("확정 수익", f"${total_profit:,.2f}", delta_color="normal")

    if 'date' in df.columns:
        df['month'] = df['date'].apply(lambda x: x[:7]) # YYYY-MM
        months = df['month'].unique()
        
        for month in months:
            month_data = df[df['month'] == month]
            cnt = len(month_data)
            m_profit = month_data['profit_amt'].sum()
            m_profit_str = f"${m_profit:,.2f}"
            
            with st.expander(f"{month} (거래 {cnt}건, 수익: {m_profit_str})", expanded=True):
                month_data['total'] = month_data['price'] * month_data['quantity']
                
                display_df = month_data[['time', 'type', 'ticker', 'price', 'quantity', 'profit_rate', 'profit_amt', 'total']].copy()
                
                display_df.rename(columns={
                    'time': '일자', 'type': '구분', 'ticker': '종목', 'price': '단가',
                    'quantity': '수량', 'profit_rate': '수익률', 'profit_amt': '수익금', 'total': '총 거래액'
                }, inplace=True)
                
                st.dataframe(display_df, use_container_width=True, hide_index=True)
    else:
        st.dataframe(df)

if __name__ == "__main__":
    main()
