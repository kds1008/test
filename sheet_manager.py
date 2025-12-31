import gspread
from oauth2client.service_account import ServiceAccountCredentials
import streamlit as st
import pandas as pd
import datetime

# --- Configuration ---
SCOPE = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
SHEET_NAME = "StockFarmDB"

class SheetManager:
    def __init__(self):
        self.client = self._connect()
        self.sheet = self._get_sheet()

    def _connect(self):
        """Connects to Google Sheets using robust authentication (Secrets or Local File)."""
        # 1. Try Streamlit Secrets (Cloud Environment)
        try:
            # Check if secrets are available without crashing
            if "gcp_service_account" in st.secrets:
                creds_dict = st.secrets["gcp_service_account"]
                creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, SCOPE)
                return gspread.authorize(creds)
        except:
            pass # Secrets failed, fall back to local file

        # 2. Try Local File (Local Environment)
        try:
            creds = ServiceAccountCredentials.from_json_keyfile_name("service_account.json", SCOPE)
            return gspread.authorize(creds)
        except Exception as e:
            print(f"DEBUG: Connection Error: {e}") # Debugging
            try:
                st.error(f"구글 시트 연결 실패: {e}")
            except:
                pass
            return None

    def _get_sheet(self):
        if not self.client: return None
        try:
            return self.client.open(SHEET_NAME)
        except Exception as e:
            st.warning(f"'{SHEET_NAME}' 시트를 찾을 수 없습니다. ({e})")
            return None

    def _get_worksheet(self, name):
        if not self.sheet: return None
        try:
            return self.sheet.worksheet(name)
        except gspread.exceptions.WorksheetNotFound:
            # Create if missing
            return self.sheet.add_worksheet(title=name, rows="100", cols="20")

    def _ensure_headers(self, worksheet, headers):
        try:
            existing_headers = worksheet.row_values(1)
            if not existing_headers:
                worksheet.append_row(headers)
            elif existing_headers != headers:
                # If headers differ, maybe warn or update? For now, assume okay.
                pass
        except:
             pass

    # --- Farm Data Operations ---

    def load_farm(self, user_nickname):
        """Loads farm data (crops) for a specific user."""
        ws = self._get_worksheet("Crops")
        if not ws: return []
        
        self._ensure_headers(ws, ["User", "Ticker", "BuyPrice", "Quantity", "BuyDate"])
        
        all_records = ws.get_all_records()
        # Filter by User
        user_crops = [r for r in all_records if str(r['User']) == user_nickname]
        
        # Convert types to match app logic
        cleaned_crops = []
        for c in user_crops:
            cleaned_crops.append({
                "ticker": c['Ticker'],
                "buy_price": float(c['BuyPrice']),
                "quantity": int(c['Quantity']),
                "buy_date": c['BuyDate']
            })
        return cleaned_crops

    def save_crop(self, user_nickname, crop_data):
        """Adds a new crop."""
        ws = self._get_worksheet("Crops")
        row = [
            user_nickname,
            crop_data['ticker'],
            crop_data['buy_price'],
            crop_data['quantity'],
            crop_data['buy_date']
        ]
        ws.append_row(row)

    def remove_crop(self, user_nickname, crop_idx):
        """Removes a crop. Since we don't have unique IDs, we find by matching logic (simplified)."""
        # This is tricky without IDs. For MVP, we will fetch all, delete the matching one from LOCAL list, 
        # then CLEAR the sheet and RE-WRITE user's rows. (Inefficient but safe for MVP with small data)
        
        ws = self._get_worksheet("Crops")
        all_records = ws.get_all_records()
        
        # Filter OUT the user's records to keep others
        other_records = [r for r in all_records if str(r['User']) != user_nickname]
        
        # Get user's current records
        user_records = [r for r in all_records if str(r['User']) == user_nickname]
        
        if 0 <= crop_idx < len(user_records):
            user_records.pop(crop_idx) # Remove the targeted crop
            
        # Re-construct sheet data
        # Headers + Other Users + Updated User Records
        
        # We need to explicitly clear and rewrite? 
        # Safer: Just append `other_records` + `user_records` to a NEW list of rows
        
        new_rows = []
        # Add headers first? gspread update needs range.
        
        # Let's use a simpler approach:
        # Load all rows, modify in python, rewrite entire sheet.
        # Warning: Concurrency issue if multiple users write same time. 
        # For this demo, it's acceptable.
        
        final_data = other_records + user_records
        
        ws.clear()
        ws.append_row(["User", "Ticker", "BuyPrice", "Quantity", "BuyDate"])
        
        # Bulk Upload
        if final_data:
            # Convert dicts back to list of values
            rows_to_add = []
            for r in final_data:
                rows_to_add.append([r['User'], r['Ticker'], r['BuyPrice'], r['Quantity'], r['BuyDate']])
            ws.append_rows(rows_to_add)

    def update_crop_qty(self, user_nickname, crop_idx, new_qty):
        """Updates quantity (partial sell)."""
        # Similar logic to remove: Rewrite for now.
        ws = self._get_worksheet("Crops")
        all_records = ws.get_all_records()
        
        other_records = [r for r in all_records if str(r['User']) != user_nickname]
        user_records = [r for r in all_records if str(r['User']) == user_nickname]
        
        if 0 <= crop_idx < len(user_records):
            user_records[crop_idx]['Quantity'] = new_qty
            
        final_data = other_records + user_records
        
        ws.clear()
        ws.append_row(["User", "Ticker", "BuyPrice", "Quantity", "BuyDate"])
        
        if final_data:
            rows_to_add = []
            for r in final_data:
                rows_to_add.append([r['User'], r['Ticker'], r['BuyPrice'], r['Quantity'], r['BuyDate']])
            ws.append_rows(rows_to_add)

    # --- History Operations ---

    def load_history(self, user_nickname):
        ws = self._get_worksheet("History")
        if not ws: return []
        
        self._ensure_headers(ws, ["User", "Time", "Type", "Ticker", "Price", "Quantity", "Date", "ProfitRate", "ProfitAmt"])
        
        all_records = ws.get_all_records()
        user_logs = [r for r in all_records if str(r['User']) == user_nickname]
        
        # Convert keys to lowercase for app compatibility
        cleaned_logs = []
        for l in user_logs:
            cleaned_logs.append({
                "time": l['Time'],
                "type": l['Type'],
                "ticker": l['Ticker'],
                "price": float(l['Price']),
                "quantity": int(l['Quantity']),
                "date": l['Date'],
                "profit_rate": float(l['ProfitRate']) if l['ProfitRate'] != '' else None,
                "profit_amt": float(l['ProfitAmt']) if l['ProfitAmt'] != '' else None
            })
        return cleaned_logs

    def log_transaction(self, user_nickname, log_data):
        ws = self._get_worksheet("History")
        row = [
            user_nickname,
            log_data['time'],
            log_data['type'],
            log_data['ticker'],
            log_data['price'],
            log_data['quantity'],
            log_data['date'],
            log_data['profit_rate'] if log_data['profit_rate'] is not None else "",
            log_data['profit_amt'] if log_data['profit_amt'] is not None else ""
        ]
        ws.append_row(row)

    # --- Auth Operations ---
    
    def _hash_password(self, password):
        import hashlib
        return hashlib.sha256(password.encode()).hexdigest()

    def register_user(self, nickname, password):
        """Registers a new user. Returns True if successful, False if nickname exists."""
        ws = self._get_worksheet("Users")
        if not ws: return False
        
        self._ensure_headers(ws, ["Nickname", "PasswordHash"])
        
        # Check if exists
        try:
            cell = ws.find(nickname)
            if cell: return False # Already exists
        except gspread.exceptions.CellNotFound:
            pass # Good, doesn't exist

        ws.append_row([nickname, self._hash_password(password)])
        return True

    def login_user(self, nickname, password):
        """Verifies credentials. Returns True if valid."""
        ws = self._get_worksheet("Users")
        if not ws: return False
        
        self._ensure_headers(ws, ["Nickname", "PasswordHash"])
        
        try:
            cell = ws.find(nickname)
            if not cell: return False
            
            # Get hash from next column
            stored_hash = ws.cell(cell.row, cell.col + 1).value
            return stored_hash == self._hash_password(password)
        except:
            return False

    def get_all_users(self):
        """Returns a list of all nicknames."""
        ws = self._get_worksheet("Users")
        if not ws: return []
        
        try:
            records = ws.get_all_records()
            return [r['Nickname'] for r in records if r['Nickname']]
        except:
            return []
