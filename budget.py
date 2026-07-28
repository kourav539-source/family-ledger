import streamlit as st
from supabase import create_client, Client
import requests
import datetime

# --- PAGE CONFIGURATION ---
st.set_page_config(
    page_title="Family Ledger & Expense Tracker",
    page_icon="💰",
    layout="centered"
)

# --- SECRETS & CLIENT SETUP ---
try:
    SUPABASE_URL = st.secrets["SUPABASE_URL"]
    SUPABASE_KEY = st.secrets["SUPABASE_KEY"]
    TELEGRAM_BOT_TOKEN = st.secrets.get("TELEGRAM_BOT_TOKEN", "")
    TELEGRAM_CHAT_ID = st.secrets.get("TELEGRAM_CHAT_ID", "")
except Exception as e:
    st.error("⚠️ Missing Streamlit Secrets! Please configure your Supabase and Telegram keys in Advanced Settings.")
    st.stop()

@st.cache_resource
def init_supabase() -> Client:
    return create_client(SUPABASE_URL, SUPABASE_KEY)

supabase = init_supabase()

# --- HELPER FUNCTIONS ---
def send_telegram_alert(message):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "Markdown"
    }
    try:
        requests.post(url, json=payload, timeout=5)
    except Exception:
        pass

def fetch_ledger_data():
    try:
        response = supabase.table("ledger").select("*").execute()
        return response.data
    except Exception:
        return []

def calculate_balance(data):
    total_income = 0.0
    total_expense = 0.0
    for row in data:
        amount = float(row.get("amount", 0))
        trans_type = row.get("type", "").lower()
        if trans_type == "income":
            total_income += amount
        else:
            total_expense += amount
    return total_income, total_expense, total_income - total_expense

# --- UI DESIGN ---
st.title("👨‍👩‍👧 Family Ledger & Expense Tracker")
st.markdown("Track family expenses securely with live cloud sync and strict balance protection.")

# Load existing transactions
data = fetch_ledger_data()
total_income, total_expense, current_balance = calculate_balance(data)

# Display Summary Metrics
col1, col2, col3 = st.columns(3)
col1.metric("Total Income", f"₹{total_income:,.2f}")
col2.metric("Total Expenses", f"₹{total_expense:,.2f}")
col3.metric("Current Balance", f"₹{current_balance:,.2f}", delta=f"₹{current_balance:,.2f}")

st.divider()

# --- TRANSACTION FORM ---
st.subheader("➕ Add New Transaction")

with st.form("transaction_form", clear_on_submit=True):
    trans_type = st.selectbox("Transaction Type", ["Expense", "Income"])
    amount = st.number_input("Amount (₹)", min_value=1.0, step=1.0)
    category = st.text_input("Category (e.g., Groceries, Utilities, Salary)")
    description = st.text_area("Description / Notes")
    
    submitted = st.form_submit_button("Submit Entry")
    
    if submitted:
        # Check if expense drops balance below zero
        if trans_type.lower() == "expense" and (current_balance - amount) < 0:
            st.error(f"❌ Transaction declined! Your current balance is ₹{current_balance:,.2f}. The amount you entered is higher than your remaining balance, or it would bring your account to zero/negative.")
        else:
            payload = {
                "type": trans_type.lower(),
                "amount": amount,
                "category": category,
                "description": description,
                "date": str(datetime.date.today())
            }
            try:
                supabase.table("ledger").insert(payload).execute()
                st.success("✅ Transaction added successfully and saved to cloud!")
                
                # Send telegram notification
                alert_msg = f"*{trans_type} Added!*\nAmount: ₹{amount:,.2f}\nCategory: {category}\nNotes: {description}"
                send_telegram_alert(alert_msg)
                
                st.rerun()
            except Exception as err:
                st.error(f"Database Error: {err}")

# --- LEDGER HISTORY TABLE ---
st.divider()
st.subheader("📊 Recent Transactions Ledger")

if data:
    st.dataframe(data, use_container_width=True)
else:
    st.info("No ledger entries found yet. Add your first transaction above!")