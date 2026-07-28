import streamlit as st
import pandas as pd
import requests
import re
from datetime import datetime
from supabase import create_client, Client

# --- 1. CONFIGURATION & SECRETS ---
st.set_page_config(page_title="Automated Family Ledger", page_icon="🏦", layout="wide")

# Fetch credentials securely from Streamlit Secrets
TELEGRAM_BOT_TOKEN = st.secrets["TELEGRAM_BOT_TOKEN"]
TELEGRAM_CHAT_ID = st.secrets["TELEGRAM_CHAT_ID"]
SUPABASE_URL = st.secrets["SUPABASE_URL"]
SUPABASE_KEY = st.secrets["SUPABASE_KEY"]

@st.cache_resource
def init_connection():
    return create_client(SUPABASE_URL, SUPABASE_KEY)

supabase: Client = init_connection()

SIBLINGS = ["Vivek", "Riddhi", "Siddhi"]
ALL_MEMBERS = ["Father", "Vivek", "Riddhi", "Siddhi"]

EXPENSE_CATEGORIES = [
    "Hostel / Rent", "Groceries / Food", "PGDM / College Fees", 
    "Transport", "Shopping", "Medical", "Other"
]

ALL_CATEGORIES = EXPENSE_CATEGORIES + [
    "From Father", "To Vivek", "To Riddhi", "To Siddhi", 
    "From Vivek", "From Riddhi", "From Siddhi"
]

# --- 2. CLOUD DATABASE LOGIC (Replaces CSV) ---
def load_raw_database():
    try:
        response = supabase.table("transactions").select("*").execute()
        if not response.data:
            return pd.DataFrame(columns=["id", "Date", "Type", "Member", "Category", "Amount (₹)", "Description", "Entered_By", "Edited_By"])
        
        df = pd.DataFrame(response.data)
        df = df.rename(columns={
            "txn_date": "Date",
            "txn_type": "Type",
            "member": "Member",
            "category": "Category",
            "amount": "Amount (₹)",
            "description": "Description",
            "entered_by": "Entered_By",
            "edited_by": "Edited_By"
        })
        return df
    except Exception as e:
        st.error(f"Database connection error: {e}")
        return pd.DataFrame(columns=["id", "Date", "Type", "Member", "Category", "Amount (₹)", "Description", "Entered_By", "Edited_By"])

def load_data():
    df = load_raw_database()
    if not df.empty:
        df["Date"] = pd.to_datetime(df["Date"], format='mixed')
        df["Month_Year"] = df["Date"].dt.strftime("%B %Y")
        df["Date"] = df["Date"].dt.date 
    return df

def save_transaction(txn_date, txn_type, member, category, amount, description, entered_by):
    new_txn = {
        "txn_date": str(txn_date), 
        "txn_type": txn_type,
        "member": member,
        "category": category,
        "amount": amount,
        "description": description,
        "entered_by": entered_by,
        "edited_by": ""
    }
    supabase.table("transactions").insert(new_txn).execute()

# --- 3. TELEGRAM MESSENGER & STATELESS SYNC ---
def send_telegram_alert(message):
    if TELEGRAM_BOT_TOKEN != "PASTE_YOUR_TOKEN_HERE":
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "Markdown"}
        try:
            requests.post(url, json=payload)
        except Exception as e:
            st.error(f"Failed to send Telegram alert: {e}")

def process_telegram_inbox():
    if TELEGRAM_BOT_TOKEN == "PASTE_YOUR_TOKEN_HERE":
        st.sidebar.error("Telegram Token is missing!")
        return

    # Fetch processed IDs from Cloud memory (Zero local files!)
    try:
        res = supabase.table("processed_updates").select("update_id").execute()
        processed_ids = {str(row["update_id"]) for row in res.data}
    except Exception as e:
        st.sidebar.error(f"Could not reach database: {e}")
        return

    last_offset = max([int(pid) for pid in processed_ids]) if processed_ids else 0
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getUpdates?offset={last_offset + 1}"
    
    try:
        response = requests.get(url)
        data = response.json()
        
        if not data.get("ok") or not data.get("result"):
            st.sidebar.info("Inbox is empty. No new commands found.")
            return

        updates = data["result"]
        newly_processed_ids = []
        today_str = datetime.today().strftime("%Y-%m-%d")

        for update in updates:
            update_id = str(update["update_id"])
            if update_id in processed_ids:
                continue

            if "message" in update and "text" in update["message"]:
                text = update["message"]["text"].lower().strip()
                
                # --- PATTERN 1: Father giving funds ---
                match_father = re.search(r'father\s+(?:give|send|gave|sent)\s+(\d+)\s*(?:usdt|rs|rupees)?\s+(?:to|ro)\s+(vivek|riddhi|siddhi)', text)
                if match_father:
                    amount = int(match_father.group(1))
                    receiver = match_father.group(2).capitalize()
                    
                    save_transaction(today_str, "Fund Allocation", receiver, "From Father", amount, "Telegram Bot", "Father")
                    send_telegram_alert(f"🤖 *Bot Auto-Processed!*\nFather transferred ₹{amount:,} to {receiver}.")
                    newly_processed_ids.append(update_id)
                    continue

                # --- PATTERN 2: Sibling Transfer ---
                match_transfer = re.search(r'(?:(vivek|riddhi|siddhi)\s+)?(?:send|sent|transferred|give|gave)\s+(\d+)\s*(?:usdt|rs|rupees)?\s+(?:to|ro)\s+(vivek|riddhi|siddhi)', text)
                if match_transfer:
                    explicit_sender = match_transfer.group(1)
                    amount = int(match_transfer.group(2))
                    receiver = match_transfer.group(3).capitalize()
                    
                    sender = explicit_sender.capitalize() if explicit_sender else ""
                    if not sender:
                        if "from" in update["message"]:
                            u = update["message"]["from"]
                            profile_text = f"{u.get('first_name', '')} {u.get('last_name', '')} {u.get('username', '')}".lower()
                            for m in SIBLINGS:
                                if m.lower() in profile_text:
                                    sender = m
                                    break
                    if not sender:
                        sender = "Vivek"

                    if sender == receiver:
                        send_telegram_alert(f"🚨 *Transfer Error*\n{sender}, you cannot transfer funds to yourself!")
                        newly_processed_ids.append(update_id)
                        continue

                    if sender in SIBLINGS and sender != receiver:
                        save_transaction(today_str, "Transfer Out", sender, f"To {receiver}", amount, "Chat Transfer", sender)
                        save_transaction(today_str, "Transfer In", receiver, f"From {sender}", amount, "Chat Transfer", sender)
                        send_telegram_alert(f"🤖 *Bot Auto-Processed!*\n{sender} transferred ₹{amount:,} to {receiver}.")
                        newly_processed_ids.append(update_id)
                        continue

                # --- PATTERN 3: Sibling Expense ---
                match_expense = re.search(r'(?:(vivek|riddhi|siddhi)\s+)?(?:i\s+)?spent\s+(\d+)\s*(?:usdt|rs|rupees)?\s+(?:on\s+)?(.+)?', text)
                if match_expense:
                    explicit_spender = match_expense.group(1)
                    amount = int(match_expense.group(2))
                    raw_category = match_expense.group(3)
                    
                    spender = explicit_spender.capitalize() if explicit_spender else ""
                    if not spender:
                        if "from" in update["message"]:
                            u = update["message"]["from"]
                            profile_text = f"{u.get('first_name', '')} {u.get('last_name', '')} {u.get('username', '')}".lower()
                            for m in SIBLINGS:
                                if m.lower() in profile_text:
                                    spender = m
                                    break
                    if not spender:
                        spender = "Vivek"

                    if spender in SIBLINGS:
                        category = "Other"
                        if raw_category:
                            raw_lower = raw_category.strip().lower()
                            if "rent" in raw_lower or "hostel" in raw_lower: category = "Hostel / Rent"
                            elif "grocer" in raw_lower or "food" in raw_lower: category = "Groceries / Food"
                            elif "college" in raw_lower or "fee" in raw_lower or "pgdm" in raw_lower: category = "PGDM / College Fees"
                            elif "transport" in raw_lower or "travel" in raw_lower: category = "Transport"
                            elif "shop" in raw_lower: category = "Shopping"
                            elif "medic" in raw_lower: category = "Medical"
                            else: category = "Other"

                        save_transaction(today_str, "Expense", spender, category, amount, f"Chat: {raw_category}", spender)
                        send_telegram_alert(f"🤖 *Bot Auto-Processed!*\n{spender} logged an expense of ₹{amount:,} for {category}.")
                        newly_processed_ids.append(update_id)
                        continue

        # Batch insert memory to Cloud
        if newly_processed_ids:
            insert_data = [{"update_id": str(pid)} for pid in newly_processed_ids]
            supabase.table("processed_updates").insert(insert_data).execute()
            st.sidebar.success(f"Successfully processed {len(newly_processed_ids)} new command(s)!")
            st.rerun()
        else:
            st.sidebar.info("Checked inbox, but no valid commands matched format.")

    except Exception as e:
        st.sidebar.error(f"Sync error: {e}")

# --- 4. USER LOGIN SIMULATION ---
st.sidebar.header("🔐 Active User Session")
st.sidebar.markdown("Select your profile before making entries or edits.")
active_user = st.sidebar.selectbox("Who is using the app?", ALL_MEMBERS)
st.sidebar.success(f"Currently logged in as: **{active_user}**")

st.sidebar.divider()
st.sidebar.header("🤖 Telegram Bot Sync")
st.sidebar.markdown("Click below to pull chat commands sent by family members:")
if st.sidebar.button("🔄 Sync Telegram Inbox"):
    process_telegram_inbox()

# --- 5. TOP NAVIGATION: MONTH SELECTOR ---
st.title("🏦 Automated Family Ledger")
df = load_data()

current_month_str = datetime.now().strftime("%B %Y")

if not df.empty:
    available_months = df["Month_Year"].unique().tolist()
    if current_month_str not in available_months:
        available_months.append(current_month_str)
else:
    available_months = [current_month_str]

default_index = available_months.index(current_month_str) if current_month_str in available_months else 0
selected_month = st.selectbox("📅 Select Month to View", available_months, index=default_index)

st.divider()

# --- 6. ALL-TIME AVAILABLE BALANCES ---
st.subheader("💰 Live Available Balances (All-Time)")

balances = {"Vivek": 0, "Riddhi": 0, "Siddhi": 0}
if not df.empty:
    for member in SIBLINGS:
        member_data = df[df["Member"] == member]
        funds_in = member_data[member_data["Type"].isin(["Fund Allocation", "Transfer In"])]["Amount (₹)"].sum()
        funds_out = member_data[member_data["Type"].isin(["Expense", "Transfer Out"])]["Amount (₹)"].sum()
        balances[member] = funds_in - funds_out

c1, c2, c3 = st.columns(3)
c1.metric(label="Vivek (Noida) - Balance", value=f"₹{balances['Vivek']:,.0f}")
c2.metric(label="Riddhi (Indore) - Balance", value=f"₹{balances['Riddhi']:,.0f}")
c3.metric(label="Siddhi (Indore) - Balance", value=f"₹{balances['Siddhi']:,.0f}")

st.divider()

# --- 7. MONTHLY CASH FLOW SUMMARY ---
st.subheader(f"📊 {selected_month} Cash Flow")
if not df.empty:
    monthly_df = df[df["Month_Year"] == selected_month]
    mc1, mc2, mc3 = st.columns(3)
    
    def show_monthly_metrics(col, name):
        if not monthly_df.empty:
            m_data = monthly_df[monthly_df["Member"] == name]
            m_in = m_data[m_data["Type"].isin(["Fund Allocation", "Transfer In"])]["Amount (₹)"].sum()
            m_out = m_data[m_data["Type"].isin(["Expense", "Transfer Out"])]["Amount (₹)"].sum()
        else:
            m_in, m_out = 0, 0
        col.markdown(f"**{name}**")
        col.write(f"🟢 Received: ₹{m_in:,.0f}")
        col.write(f"🔴 Spent: ₹{m_out:,.0f}")
        
    show_monthly_metrics(mc1, "Vivek")
    show_monthly_metrics(mc2, "Riddhi")
    show_monthly_metrics(mc3, "Siddhi")

st.divider()

# --- 8. DYNAMIC ROLE-BASED PORTAL ---
if active_user == "Father":
    tabs = st.tabs(["💸 Transfer Funds", "✏️ Edit / Delete Records"])
    tab_transfer = tabs[0]
    tab_expense = None  
    tab_edit = tabs[1]
else:
    tabs = st.tabs(["💸 Transfer Funds", "📉 Log Expense", "✏️ Edit / Delete Records"])
    tab_transfer = tabs[0]
    tab_expense = tabs[1]
    tab_edit = tabs[2]

with tab_transfer:
    col1, col2, col3 = st.columns([1, 1, 1]) 
    with col1:
        transfer_date = st.date_input("Transaction Date", datetime.today(), key="t_date")
        
        if active_user == "Father":
            sender = "Father"
            st.markdown(f"**From (Sender):** Father")
            receiver = st.selectbox("To (Receiver)", SIBLINGS, key="receiver")
        else:
            sender = active_user
            st.markdown(f"**From (Sender):** {active_user}")
            available_receivers = [s for s in SIBLINGS if s != active_user]
            receiver = st.selectbox("To (Receiver)", available_receivers, key="receiver")
            
    with col2:
        fund_amount = st.number_input("Transfer Amount (₹)", min_value=0, step=500, key="fund_amount")
        fund_desc = st.text_input("Note", key="fund_desc")
    with col3:
        st.write("") 
        st.write("") 
        st.write("") 
        
        if st.button("Transfer Funds", type="primary"):
            if sender == receiver:
                st.error("🚨 Sender and Receiver cannot be the same person.")
            elif fund_amount > 0:
                
                # --- NEW STRICT BALANCE CHECK FOR TRANSFERS ---
                current_balance = balances.get(sender, 0)
                if sender != "Father" and (current_balance - fund_amount) < 0:
                    st.error(f"❌ Transfer declined! Your current balance is ₹{current_balance:,.0f}. Deducting ₹{fund_amount:,.0f} would drop your balance below zero.")
                else:
                    formatted_date = transfer_date.strftime("%Y-%m-%d")
                    if sender == "Father":
                        save_transaction(formatted_date, "Fund Allocation", receiver, "From Father", fund_amount, fund_desc, active_user)
                    else:
                        save_transaction(formatted_date, "Transfer Out", sender, f"To {receiver}", fund_amount, fund_desc, active_user)
                        save_transaction(formatted_date, "Transfer In", receiver, f"From {sender}", fund_amount, fund_desc, active_user)
                    
                    alert_msg = f"💸 *Fund Transfer Alert*\n*Amount:* ₹{fund_amount}\n*From:* {sender}\n*To:* {receiver}\n*Note:* {fund_desc}\n*Logged By:* {active_user}"
                    send_telegram_alert(alert_msg)
                    
                    st.success(f"Transferred ₹{fund_amount:,.0f} and notified group!")
                    st.rerun()

if tab_expense is not None:
    with tab_expense:
        col4, col5, col6 = st.columns([1, 1, 1])
        with col4:
            expense_date = st.date_input("Expense Date", datetime.today(), key="e_date")
            exp_spender = active_user
            st.markdown(f"**Spent By:** {active_user}")
            exp_category = st.selectbox("Category", EXPENSE_CATEGORIES, key="exp_category")
        with col5:
            exp_amount = st.number_input("Expense Amount (₹)", min_value=0, step=100, key="exp_amount")
            exp_desc = st.text_input("Description", key="exp_desc")
        with col6:
            st.write("")
            st.write("")
            st.write("")
            
            if st.button("Log Expense", type="primary"):
                if exp_amount > 0:
                    
                    # --- NEW STRICT BALANCE CHECK FOR EXPENSES ---
                    current_balance = balances.get(exp_spender, 0)
                    if (current_balance - exp_amount) < 0:
                        st.error(f"❌ Expense declined! Your current balance is ₹{current_balance:,.0f}. Deducting ₹{exp_amount:,.0f} would drop your balance below zero.")
                    else:
                        formatted_date = expense_date.strftime("%Y-%m-%d")
                        save_transaction(formatted_date, "Expense", exp_spender, exp_category, exp_amount, exp_desc, active_user)
                        
                        alert_msg = f"📉 *New Expense Alert*\n*Amount:* ₹{exp_amount}\n*Spent By:* {exp_spender}\n*Category:* {exp_category}\n*Details:* {exp_desc}\n*Logged By:* {active_user}"
                        send_telegram_alert(alert_msg)
                        
                        st.success(f"Logged ₹{exp_amount:,.0f} and notified group!")
                        st.rerun()

with tab_edit:
    st.subheader("Database Editor with Audit Trail")
    st.info("The system automatically syncs changes to the cloud database.")
    
    raw_data = load_raw_database()
    
    if not raw_data.empty:
        raw_data["Date"] = pd.to_datetime(raw_data["Date"], format='mixed').dt.date
    
    edited_df = st.data_editor(
        raw_data, 
        num_rows="dynamic", 
        use_container_width=True, 
        key="db_editor",
        column_config={
            "id": None, # Visually hides the database Primary Key from the UI
            "Date": st.column_config.DateColumn("Date", format="YYYY-MM-DD"),
            "Type": st.column_config.SelectboxColumn("Type", options=["Expense", "Fund Allocation", "Transfer In", "Transfer Out"], required=True),
            "Member": st.column_config.SelectboxColumn("Member", options=SIBLINGS, required=True),
            "Category": st.column_config.SelectboxColumn("Category", options=ALL_CATEGORIES, required=True),
            "Entered_By": st.column_config.TextColumn("Entered By", disabled=True),
            "Edited_By": st.column_config.TextColumn("Edited By", disabled=True)
        }
    )
    
    if st.button("💾 Save Database"):
        changes_made = False
        
        # 1. Handle Deletions (if you delete a row in the UI, we delete it in the Cloud)
        deleted_rows = raw_data[~raw_data["id"].isin(edited_df["id"])]
        for _, row in deleted_rows.iterrows():
            supabase.table("transactions").delete().eq("id", int(row["id"])).execute()
            changes_made = True

        # 2. Handle Additions (if you add a manual row in the UI, we push it to the Cloud)
        added_rows = edited_df[edited_df["id"].isna()]
        for _, row in added_rows.iterrows():
            new_txn = {
                "txn_date": str(row["Date"]), 
                "txn_type": row["Type"],
                "member": row["Member"],
                "category": row["Category"],
                "amount": float(row["Amount (₹)"]),
                "description": str(row["Description"]) if pd.notna(row["Description"]) else "",
                "entered_by": active_user,
                "edited_by": ""
            }
            supabase.table("transactions").insert(new_txn).execute()
            changes_made = True

        # 3. Handle Updates (if you edit an existing cell, we patch it in the Cloud)
        for index in edited_df.dropna(subset=["id"]).index:
            orig_id = edited_df.loc[index, "id"]
            if orig_id in raw_data["id"].values:
                orig_index = raw_data[raw_data["id"] == orig_id].index[0]
                cols_to_check = ["Date", "Type", "Member", "Category", "Amount (₹)", "Description"]
                
                for col in cols_to_check:
                    if str(edited_df.loc[index, col]) != str(raw_data.loc[orig_index, col]):
                        
                        editor_note = f"⚠️ Edited by {active_user}" if str(edited_df.loc[index, "Entered_By"]) != active_user else f"✏️ Fixed by {active_user}"
                        
                        supabase.table("transactions").update({
                            "txn_date": str(edited_df.loc[index, "Date"]),
                            "txn_type": edited_df.loc[index, "Type"],
                            "member": edited_df.loc[index, "Member"],
                            "category": edited_df.loc[index, "Category"],
                            "amount": float(edited_df.loc[index, "Amount (₹)"]),
                            "description": str(edited_df.loc[index, "Description"]) if pd.notna(edited_df.loc[index, "Description"]) else "",
                            "edited_by": editor_note
                        }).eq("id", int(orig_id)).execute()
                        
                        changes_made = True
                        break 
        
        if changes_made:
            edit_msg = f"⚠️ *Database Edit Alert*\n{active_user} just modified a record in the cloud ledger."
            send_telegram_alert(edit_msg)
            st.success("Cloud Database successfully updated!")
            st.rerun()
        else:
            st.info("No changes detected.")

# --- 9. MONTHLY TRANSACTION HISTORY ---
st.divider()
st.subheader(f"Ledger for {selected_month}")
if not df.empty and not monthly_df.empty:
    display_df = monthly_df.drop(columns=["Month_Year", "id"]).sort_values(by="Date", ascending=False)
    st.dataframe(display_df, use_container_width=True, hide_index=True)
else:
    st.info(f"No transactions found for {selected_month}.")