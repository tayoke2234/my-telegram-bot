# Telegram Temp Mail Bot (Definitive Final Version - All Features & Bug Fixes)
# Deployed on Render.com, kept alive by UptimeRobot

import logging
import asyncio
import sqlite3
from datetime import datetime, timedelta, date
import os
import random
import string
import re
import imaplib
import email
from email.header import decode_header
from flask import Flask
from threading import Thread

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, BotCommand, BotCommandScopeChat
from telegram.ext import Application, CommandHandler, ContextTypes, CallbackQueryHandler

# --- FLASK WEB SERVER ---
app = Flask(__name__)
@app.route('/')
def home():
    return "Bot is alive and running with definitive fixes and admin panel!"
def run_web_server():
    port = int(os.environ.get('PORT', 8080))
    app.run(host='0.0.0.0', port=port)
def start_web_server_in_thread():
    t = Thread(target=run_web_server)
    t.start()

# --- LOGGING SETUP ---
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

# --- CONFIGURATION ---
try:
    BOT_TOKEN = os.environ['BOT_TOKEN']
    YOUR_DOMAIN = os.environ['YOUR_DOMAIN']
    CATCH_ALL_EMAIL = os.environ['CATCH_ALL_EMAIL']
    CATCH_ALL_PASSWORD = os.environ['CATCH_ALL_PASSWORD']
    ADMIN_ID = int(os.environ.get('ADMIN_ID', 0))
    IMAP_SERVER = "imap.gmail.com"
    DAILY_LIMIT = 5
except KeyError as e:
    print(f"!!! FATAL ERROR: Environment variable {e} is not set on Render.com !!!")
    exit()

# --- DATABASE SETUP ---
DB_PATH = '/data/tempmail.db' if os.path.exists('/data') else 'tempmail.db'
db_lock = asyncio.Lock()

def get_db_conn():
    return sqlite3.connect(DB_PATH, timeout=10, check_same_thread=False, detect_types=sqlite3.PARSE_DECLTYPES | sqlite3.PARSE_COLNAMES)

def init_db():
    with get_db_conn() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS addresses (
                id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL, username TEXT NOT NULL,
                full_address TEXT NOT NULL, creation_date DATE NOT NULL,
                expires_at TIMESTAMP, UNIQUE(user_id, username)
            )
        ''')
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS emails (
                id INTEGER PRIMARY KEY, address_id INTEGER NOT NULL, message_id TEXT UNIQUE,
                from_address TEXT NOT NULL, subject TEXT NOT NULL, body TEXT,
                received_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (address_id) REFERENCES addresses (id) ON DELETE CASCADE
            )
        ''')
        conn.commit()

# --- RANDOM ADDRESS DATA ---
ADDRESS_DATA = {
    "Myanmar": {"cities": ["Yangon", "Mandalay", "Naypyidaw"], "streets": ["Anawrahta Road", "Maha Bandula Road", "Pyay Road"], "zips": ["11181", "05021", "15011"]},
    "USA": {"cities": ["New York", "Los Angeles", "Chicago"], "streets": ["Broadway", "Sunset Blvd", "Michigan Ave"], "zips": ["10001", "90001", "60601"]},
    "Mexico": {"cities": ["Mexico City", "Guadalajara", "Monterrey"], "streets": ["Paseo de la Reforma", "Avenida de los Insurgentes", "Calzada del Valle"], "zips": ["06500", "44100", "66220"]},
    "Spain": {"cities": ["Madrid", "Barcelona", "Seville"], "streets": ["Gran Vía", "La Rambla", "Calle Sierpes"], "zips": ["28013", "08002", "41004"]},
    "Japan": {"cities": ["Tokyo", "Osaka", "Kyoto"], "streets": ["Chuo Dori", "Midosuji", "Karasuma Dori"], "zips": ["100-0001", "542-0081", "600-8001"]},
    "Germany": {"cities": ["Berlin", "Munich", "Hamburg"], "streets": ["Kurfürstendamm", "Maximilianstraße", "Reeperbahn"], "zips": ["10719", "80539", "20359"]},
    "UK": {"cities": ["London", "Manchester", "Edinburgh"], "streets": ["Oxford Street", "Deansgate", "Princes Street"], "zips": ["W1B 3AG", "M3 4LQ", "EH2 2YJ"]},
    "France": {"cities": ["Paris", "Marseille", "Lyon"], "streets": ["Champs-Élysées", "La Canebière", "Rue de la République"], "zips": ["75008", "13001", "69002"]},
    "Canada": {"cities": ["Toronto", "Vancouver", "Montreal"], "streets": ["Yonge Street", "Granville Street", "Saint Catherine Street"], "zips": ["M5B 2H1", "V6Z 1S5", "H3B 2W6"]},
    "Australia": {"cities": ["Sydney", "Melbourne", "Brisbane"], "streets": ["George Street", "Collins Street", "Queen Street"], "zips": ["2000", "3000", "4000"]},
}
COUNTRIES = list(ADDRESS_DATA.keys())

# --- BOT COMMANDS ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("👋 Bot မှကြိုဆိုပါတယ်။ Command များအတွက် 'Menu' ခလုတ် သို့မဟုတ် `/help` ကိုနှိပ်ပါ။")

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    help_text = """
    **ℹ️ အကူအညီ နှင့် အသုံးပြုပုံ**

    **Email ဖန်တီးခြင်း:**
    - `/new`: ကျပန်းနာမည်ဖြင့် သက်တမ်းမကုန်သော email ဖန်တီးရန်။
    - `/new <name>`: ကိုယ်ပိုင်နာမည်ဖြင့် သက်တမ်းမကုန်သော email ဖန်တီးရန်။

    **Email စီမံခန့်ခွဲခြင်း:**
    - `/myemails`: သင်၏ email များကို ကြည့်ရှု/စီမံရန်။

    **အခြား Feature များ:**
    - `/random`: နိုင်ငံအလိုက် လိပ်စာအတုများ ဖန်တီးရန်။
    """
    await update.message.reply_text(help_text, parse_mode='Markdown')

async def create_email_entry(user_id: int, username: str):
    full_address = f"{username}@{YOUR_DOMAIN}"
    async with db_lock:
        try:
            with get_db_conn() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT COUNT(*) FROM addresses WHERE user_id = ? AND creation_date = ?", (user_id, date.today()))
                if cursor.fetchone()[0] >= DAILY_LIMIT:
                    return None, f"⚠️ တစ်နေ့တာအတွက် သတ်မှတ်ထားတဲ့ အီးမေးလ် {DAILY_LIMIT} ခု ပြည့်သွားပါပြီ။"
                
                cursor.execute("INSERT INTO addresses (user_id, username, full_address, creation_date, expires_at) VALUES (?, ?, ?, ?, ?)",
                               (user_id, username, full_address, date.today(), None)) # No expiry
                conn.commit()
            return full_address, None
        except sqlite3.IntegrityError:
            return None, f"⚠️ `{username}` ဆိုတဲ့လိပ်စာက ရှိပြီးသားဖြစ်နေပါသည်။"
        except Exception as e:
            logger.error(f"Error in create_email_entry: {e}")
            return None, "❌ အမှားအယွင်းတစ်ခု ဖြစ်ပွားပါသည်။"

async def new_email(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not context.args:
        username = ''.join(random.choices(string.ascii_lowercase + string.digits, k=8))
    else:
        username = context.args[0].lower()
        if not username.isalnum():
            await update.message.reply_text("❌ Username ပုံစံမှားယွင်းနေပါသည်။ (a-z, 0-9 သာ)"); return
    
    full_address, error = await create_email_entry(user_id, username)
    if error: await update.message.reply_text(error, parse_mode='Markdown')
    else: await update.message.reply_text(f"✅ သက်တမ်းမကုန်သောလိပ်စာ:\n\n`{full_address}`", parse_mode='Markdown')

async def my_emails(update: Update, context: ContextTypes.DEFAULT_TYPE):
    async with db_lock:
        with get_db_conn() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT username, full_address FROM addresses WHERE user_id = ?", (update.effective_user.id,))
            addresses = cursor.fetchall()
    if not addresses: await update.message.reply_text("သင်ဖန်တီးထားတဲ့ အီးမေးလ်လိပ်စာ မရှိသေးပါ။"); return

    message_text = "📬 **သင်၏ Email များကို စီမံရန်:**\n\n"
    keyboard = []
    for username, full_address in addresses:
        message_text += f"- `{full_address}`\n"
        keyboard.append([
            InlineKeyboardButton(f"📥 Inbox: {username}", callback_data=f"inbox:{username}:0"),
            InlineKeyboardButton(f"🗑️ Delete", callback_data=f"user_delete_confirm:{username}")
        ])
    await update.message.reply_text(message_text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')

async def random_address_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await show_country_page(update.message.reply_text, 0)

async def show_country_page(reply_func, page: int):
    items_per_page = 5
    start_index = page * items_per_page
    end_index = start_index + items_per_page
    
    keyboard = []
    for country in COUNTRIES[start_index:end_index]:
        keyboard.append([InlineKeyboardButton(country, callback_data=f"gen_address:{country}")])
    
    pagination_buttons = []
    if page > 0:
        pagination_buttons.append(InlineKeyboardButton("◀️ Prev", callback_data=f"country_page:{page-1}"))
    if end_index < len(COUNTRIES):
        pagination_buttons.append(InlineKeyboardButton("Next ▶️", callback_data=f"country_page:{page+1}"))
    
    if pagination_buttons:
        keyboard.append(pagination_buttons)
        
    await reply_func("🌍 ကျေးဇူးပြု၍ နိုင်ငံတစ်ခုရွေးချယ်ပါ:", reply_markup=InlineKeyboardMarkup(keyboard))

# --- ADMIN PANEL ---
async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id != ADMIN_ID:
        await update.message.reply_text("⛔️ You are not authorized to use this command.")
        return

    async with db_lock:
        with get_db_conn() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(DISTINCT user_id) FROM addresses")
            user_count = cursor.fetchone()[0]
            cursor.execute("SELECT COUNT(*) FROM addresses")
            email_count = cursor.fetchone()[0]
    
    db_size_bytes = os.path.getsize(DB_PATH) if os.path.exists(DB_PATH) else 0
    db_size_mb = round(db_size_bytes / (1024 * 1024), 2)

    text = (
        f"👑 **Admin Control Panel** 👑\n\n"
        f"📊 **Bot Stats:**\n"
        f"  - 👥 Active Users: `{user_count}`\n"
        f"  - 📧 Total Emails Created: `{email_count}`\n"
        f"  - 💽 DB Storage Used: `{db_size_mb} MB`\n"
    )
    keyboard = [
        [InlineKeyboardButton("👥 User စာရင်းကြည့်ရန်", callback_data="admin_list_users:0")],
        [InlineKeyboardButton("🔄 Refresh Stats", callback_data="admin_panel")]
    ]
    
    if update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')
    else:
        await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')

async def show_all_users(query, page: int):
    if query.from_user.id != ADMIN_ID: return
    
    users_per_page = 5
    offset = page * users_per_page
    
    async with db_lock:
        with get_db_conn() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(DISTINCT user_id) FROM addresses")
            total_users = cursor.fetchone()[0]
            cursor.execute("""
                SELECT user_id, COUNT(id) as email_count 
                FROM addresses 
                GROUP BY user_id 
                ORDER BY user_id 
                LIMIT ? OFFSET ?
            """, (users_per_page, offset))
            users = cursor.fetchall()

    if not users:
        await query.edit_message_text("👥 User မရှိသေးပါ။", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("◀️ Admin Panel သို့ပြန်သွားရန်", callback_data="admin_panel")]]))
        return

    text = f"👥 **User စာရင်း** (Page {page + 1})\n\n"
    keyboard = []
    for user_id, email_count in users:
        text += f"- `ID: {user_id}` (Emails: {email_count})\n"
        keyboard.append([InlineKeyboardButton(f"👁️‍🗨️ User {user_id} ကိုကြည့်ရန်", callback_data=f"admin_view_user:{user_id}:0")])

    pagination_buttons = []
    if page > 0: pagination_buttons.append(InlineKeyboardButton("◀️ ရှေ့", callback_data=f"admin_list_users:{page-1}"))
    if (page + 1) * users_per_page < total_users: pagination_buttons.append(InlineKeyboardButton("နောက် ▶️", callback_data=f"admin_list_users:{page+1}"))
    if pagination_buttons: keyboard.append(pagination_buttons)
    
    keyboard.append([InlineKeyboardButton("◀️ Admin Panel သို့ပြန်သွားရန်", callback_data="admin_panel")])
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')

async def show_user_emails_for_admin(query, user_id_to_view: int, page: int):
    if query.from_user.id != ADMIN_ID: return

    emails_per_page = 5
    offset = page * emails_per_page
    
    async with db_lock:
        with get_db_conn() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM addresses WHERE user_id = ?", (user_id_to_view,))
            total_emails = cursor.fetchone()[0]
            cursor.execute("SELECT username, full_address FROM addresses WHERE user_id = ? LIMIT ? OFFSET ?", (user_id_to_view, emails_per_page, offset))
            addresses = cursor.fetchall()
            
    if not addresses:
        await query.edit_message_text(f"User `{user_id_to_view}` အတွက် email မရှိပါ။", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("◀️ User စာရင်းသို့ပြန်သွားရန်", callback_data="admin_list_users:0")]]))
        return
        
    text = f"📧 **User `{user_id_to_view}` ၏ Email များ** (Page {page+1})\n\n"
    keyboard = []
    for username, full_address in addresses:
        text += f"- `{full_address}`\n"
        keyboard.append([
            InlineKeyboardButton(f"🗑️ Delete", callback_data=f"admin_delete_confirm:{user_id_to_view}:{username}")
        ])
        
    pagination_buttons = []
    if page > 0: pagination_buttons.append(InlineKeyboardButton("◀️ ရှေ့", callback_data=f"admin_view_user:{user_id_to_view}:{page-1}"))
    if (page + 1) * emails_per_page < total_emails: pagination_buttons.append(InlineKeyboardButton("နောက် ▶️", callback_data=f"admin_view_user:{user_id_to_view}:{page+1}"))
    if pagination_buttons: keyboard.append(pagination_buttons)
        
    keyboard.append([InlineKeyboardButton("◀️ User စာရင်းသို့ပြန်သွားရန်", callback_data="admin_list_users:0")])
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')

async def confirm_admin_delete(query, user_id_to_delete: int, username: str):
    if query.from_user.id != ADMIN_ID: return
    keyboard = [[
        InlineKeyboardButton("✅ Yes, delete", callback_data=f"admin_delete_execute:{user_id_to_delete}:{username}"),
        InlineKeyboardButton("❌ Cancel", callback_data=f"admin_view_user:{user_id_to_delete}:0")
    ]]
    await query.edit_message_text(
        text=f"❓ User `{user_id_to_delete}` ၏ email `{username}@{YOUR_DOMAIN}` ကို အပြီးတိုင်ဖျက်ရန် သေချာပါသလား?", 
        reply_markup=InlineKeyboardMarkup(keyboard), 
        parse_mode='Markdown'
    )

async def execute_admin_delete(query, user_id_to_delete: int, username: str):
    if query.from_user.id != ADMIN_ID: return
    full_address = f"{username}@{YOUR_DOMAIN}"
    async with db_lock:
        with get_db_conn() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM addresses WHERE user_id = ? AND username = ?", (user_id_to_delete, username))
            rowcount = cursor.rowcount
            conn.commit()
    if rowcount > 0: 
        await query.edit_message_text(f"🗑️ `{full_address}` ကို အောင်မြင်စွာဖျက်ပြီးပါပြီ။", parse_mode='Markdown')
        await show_user_emails_for_admin(query, user_id_to_delete, 0)
    else: 
        await query.edit_message_text(f"⚠️ `{full_address}` ကို ရှာမတွေ့ပါ။", parse_mode='Markdown')

# --- INLINE BUTTON HANDLER ---
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data.split(':')
    action = data[0]
    user_id = query.from_user.id

    # User actions
    if action == "inbox": await show_inbox(query, username=data[1], page=int(data[2]))
    elif action == "read_email": await show_full_email(query, email_id=int(data[1])) # <<< NEW ACTION
    elif action == "user_delete_confirm": await confirm_user_delete(query, username=data[1])
    elif action == "user_delete_execute": await execute_user_delete(query, username=data[1])
    elif action == "country_page": await show_country_page(query.edit_message_text, page=int(data[1]))
    elif action == "gen_address": await generate_address(query, country=data[1])
    elif action == "cancel_delete": await query.edit_message_text("🗑️ ဖျက်ခြင်းကို ပယ်ဖျက်လိုက်ပါသည်။")
    
    # Admin actions - protected
    elif user_id == ADMIN_ID:
        if action == "admin_panel": await admin_panel(update, context)
        elif action == "admin_list_users": await show_all_users(query, page=int(data[1]))
        elif action == "admin_view_user": await show_user_emails_for_admin(query, user_id_to_view=int(data[1]), page=int(data[2]))
        elif action == "admin_delete_confirm": await confirm_admin_delete(query, user_id_to_delete=int(data[1]), username=data[2])
        elif action == "admin_delete_execute": await execute_admin_delete(query, user_id_to_delete=int(data[1]), username=data[2])
    else:
        await query.answer("⛔️ You are not authorized for this action.", show_alert=True)

async def confirm_user_delete(query: Update, username: str):
    keyboard = [[
        InlineKeyboardButton("✅ Yes, delete", callback_data=f"user_delete_execute:{username}"),
        InlineKeyboardButton("❌ Cancel", callback_data="cancel_delete")
    ]]
    await query.edit_message_text(text=f"❓ `{username}@{YOUR_DOMAIN}` ကို အပြီးတိုင်ဖျက်ရန် သေချာပါသလား?", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')

async def execute_user_delete(query: Update, username: str):
    user_id = query.from_user.id
    full_address = f"{username}@{YOUR_DOMAIN}"
    async with db_lock:
        with get_db_conn() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM addresses WHERE user_id = ? AND username = ?", (user_id, username))
            rowcount = cursor.rowcount
            conn.commit()
    if rowcount > 0: await query.edit_message_text(f"🗑️ `{full_address}` ကို အောင်မြင်စွာဖျက်ပြီးပါပြီ။", parse_mode='Markdown')
    else: await query.edit_message_text(f"⚠️ `{full_address}` ကို ရှာမတွေ့ပါ။", parse_mode='Markdown')

async def generate_address(query: Update, country: str):
    data = ADDRESS_DATA.get(country)
    if not data:
        await query.edit_message_text("Error: Country data not found.")
        return
    
    address = (
        f"**Random Address for {country}**\n\n"
        f"**Street:** {random.randint(100, 9999)} {random.choice(data['streets'])}\n"
        f"**City:** {random.choice(data['cities'])}\n"
        f"**Zip Code:** {random.choice(data['zips'])}"
    )
    await query.edit_message_text(address, parse_mode='Markdown')
    
# <<< MODIFIED FUNCTION: show_inbox >>>
async def show_inbox(query, username, page):
    user_id = query.from_user.id
    async with db_lock:
        with get_db_conn() as conn:
            cursor = conn.cursor()
            # Allow admin to see any inbox, but regular users only their own
            if user_id == ADMIN_ID:
                cursor.execute("SELECT id FROM addresses WHERE username = ?", (username,))
            else:
                cursor.execute("SELECT id FROM addresses WHERE user_id = ? AND username = ?", (user_id, username))
            
            address_row = cursor.fetchone()
            if not address_row: 
                await query.edit_message_text("❌ Error: Email address not found or you don't have permission to view it."); return
            
            address_id = address_row[0]
            emails_per_page = 5
            offset = page * emails_per_page
            # Fetch email ID, from, and subject for the list
            cursor.execute("SELECT id, from_address, subject FROM emails WHERE address_id = ? ORDER BY received_at DESC LIMIT ? OFFSET ?", (address_id, emails_per_page, offset))
            emails = cursor.fetchall()
            cursor.execute("SELECT COUNT(*) FROM emails WHERE address_id = ?", (address_id,))
            total_emails = cursor.fetchone()[0]

    full_address = f"{username}@{YOUR_DOMAIN}"
    if total_emails == 0: 
        await query.edit_message_text(f"📥 `{full_address}` ၏ inbox ထဲမှာ email မရှိသေးပါ။", parse_mode='Markdown'); return

    message_text = f"📥 **Inbox: {full_address}** (Page {page + 1})\n\nဖတ်ရှုရန် email တစ်ခုကို ရွေးချယ်ပါ:"
    keyboard = []
    # Create a button for each email
    for email_id, from_addr, subject in emails:
        # Truncate long subjects/senders for display on the button
        button_text = f"📧 {from_addr[:25]}... - {subject[:25]}..."
        keyboard.append([InlineKeyboardButton(button_text, callback_data=f"read_email:{email_id}")])
    
    pagination_buttons = []
    if page > 0: pagination_buttons.append(InlineKeyboardButton("◀️ ရှေ့", callback_data=f"inbox:{username}:{page-1}"))
    if (page + 1) * emails_per_page < total_emails: pagination_buttons.append(InlineKeyboardButton("နောက် ▶️", callback_data=f"inbox:{username}:{page+1}"))
    if pagination_buttons: keyboard.append(pagination_buttons)

    # Add a back button for easier navigation
    keyboard.append([InlineKeyboardButton("◀️ Email စာရင်းများသို့ ပြန်သွားရန်", callback_data="back_to_myemails")])

    try:
        await query.edit_message_text(text=message_text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')
    except Exception as e:
        if "Message is not modified" not in str(e): 
            logger.error(f"Error editing message for inbox list: {e}")
            await query.answer("❌ Inbox ကိုဖွင့်ရာတွင် အမှားအယွင်းဖြစ်ပွားပါသည်။", show_alert=True)

# <<< NEW FUNCTION: show_full_email >>>
async def show_full_email(query, email_id):
    async with db_lock:
        with get_db_conn() as conn:
            cursor = conn.cursor()
            # Fetch all details for the specific email
            cursor.execute("""
                SELECT e.from_address, e.subject, e.body, e.received_at, a.username
                FROM emails e
                JOIN addresses a ON e.address_id = a.id
                WHERE e.id = ?
            """, (email_id,))
            email_data = cursor.fetchone()

    if not email_data:
        await query.edit_message_text("❌ Error: Email not found.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("◀️ Inbox သို့ပြန်သွားရန်", callback_data="back_to_myemails")]]))
        return

    from_addr, subject, body, received_at_str, username = email_data
    received_at = datetime.fromisoformat(received_at_str).strftime('%Y-%m-%d %H:%M')

    # Truncate body to prevent hitting the character limit again
    max_len = 3800 # Leave buffer for headers
    if len(body) > max_len:
        body = body[:max_len] + "\n\n[...စာသားအရှည်ကိုဖြတ်တောက်ထားပါသည်...]"

    message_text = (
        f"**From:** `{from_addr}`\n"
        f"**Subject:** `{subject}`\n"
        f"**Received:** `{received_at}`\n"
        f"----------------------------------------\n\n"
        f"{body}"
    )
    
    # Go back to the first page of the correct inbox
    keyboard = [[InlineKeyboardButton("◀️ Inbox သို့ပြန်သွားရန်", callback_data=f"inbox:{username}:0")]]

    try:
        await query.edit_message_text(text=message_text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')
    except Exception as e:
        logger.error(f"Error showing full email: {e}")
        await query.answer("❌ Email ကိုပြသရာတွင် အမှားအယွင်းဖြစ်ပွားပါသည်။", show_alert=True)

# --- BACKGROUND TASKS & BOT SETUP ---
def auto_delete_expired_addresses():
    with get_db_conn() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM addresses WHERE expires_at IS NOT NULL AND expires_at < ?", (datetime.now(),))
        if cursor.rowcount > 0: logger.info(f"Auto-deleted {cursor.rowcount} expired addresses.")
        conn.commit()

def fetch_and_process_emails(application: Application):
    try:
        mail = imaplib.IMAP4_SSL(IMAP_SERVER); mail.login(CATCH_ALL_EMAIL, CATCH_ALL_PASSWORD); mail.select("inbox")
        yesterday = date.today() - timedelta(days=1)
        search_criteria = f'(SENTSINCE {yesterday.strftime("%d-%b-%Y")})'
        status, messages = mail.search(None, search_criteria)
        if status != 'OK': mail.logout(); return
        email_ids = messages[0].split()
        if not email_ids: mail.logout(); return
        
        logger.info(f"Found {len(email_ids)} emails since yesterday. Checking for new ones.")
        asyncio.run(process_emails_in_db(application, mail, email_ids))
        mail.logout()
    except Exception as e: logger.error(f"IMAP Error: {e}")

async def process_emails_in_db(application, mail, email_ids):
    async with db_lock:
        with get_db_conn() as conn:
            cursor = conn.cursor()
            for email_id in reversed(email_ids):
                try:
                    _, msg_data = mail.fetch(email_id, "(RFC822)"); msg = email.message_from_bytes(msg_data[0][1])
                    message_id_header = msg.get("Message-ID")
                    if not message_id_header: continue
                    cursor.execute("SELECT id FROM emails WHERE message_id = ?", (message_id_header,))
                    if cursor.fetchone(): continue

                    to_address = email.utils.parseaddr(msg.get("To"))[1] or email.utils.parseaddr(msg.get("Delivered-To"))[1]
                    if not to_address or YOUR_DOMAIN not in to_address: continue
                    username = to_address.split('@')[0].lower()
                    cursor.execute("SELECT id, user_id FROM addresses WHERE username = ?", (username,))
                    address_row = cursor.fetchone()
                    if address_row:
                        address_id, user_id = address_row
                        subject, _ = decode_header(msg["Subject"])[0]; from_address, _ = decode_header(msg.get("From"))[0]
                        subject = subject.decode('utf-8', 'ignore') if isinstance(subject, bytes) else subject
                        from_address = from_address.decode('utf-8', 'ignore') if isinstance(from_address, bytes) else from_address
                        body = ""
                        if msg.is_multipart():
                            for part in msg.walk():
                                if part.get_content_type() == "text/plain": body = part.get_payload(decode=True).decode(part.get_content_charset() or 'utf-8', 'ignore'); break
                        else: body = msg.get_payload(decode=True).decode(msg.get_content_charset() or 'utf-8', 'ignore')
                        
                        cursor.execute("INSERT INTO emails (address_id, message_id, from_address, subject, body, received_at) VALUES (?, ?, ?, ?, ?, ?)", 
                                       (address_id, message_id_header, from_address, subject, body, datetime.now())); 
                        conn.commit()
                        
                        logger.info(f"Processed new email for {to_address}")
                        notification = f"🔔 *Email အသစ်ရောက်ရှိ* `{to_address}`\n\n`/myemails` ကိုသုံးပြီး inbox ထဲဝင်ကြည့်နိုင်ပါပြီ။"
                        asyncio.run_coroutine_threadsafe(application.bot.send_message(chat_id=user_id, text=notification, parse_mode='Markdown'), application.loop)
                except Exception as e: logger.error(f"Error processing a single email: {e}")

async def background_tasks_loop(application: Application):
    logger.info("Background tasks loop started.")
    while True:
        await asyncio.to_thread(fetch_and_process_emails, application)
        await asyncio.to_thread(auto_delete_expired_addresses)
        await asyncio.sleep(60)

async def post_init(application: Application):
    user_commands = [
        BotCommand("start", "Bot ကိုစတင်ရန်"),
        BotCommand("new", "သက်တမ်းမကုန်သော email ဖန်တီးရန် (ဥပမာ: /new myname)"),
        BotCommand("myemails", "သင်၏ email များကို ကြည့်ရှု/စီမံရန်"),
        BotCommand("random", "နိုင်ငံအလိုက် လိပ်စာအတုများ ဖန်တီးရန်"),
        BotCommand("help", "အကူအညီကြည့်ရန်"),
    ]
    await application.bot.set_my_commands(user_commands)
    
    if ADMIN_ID != 0:
        admin_commands = user_commands + [
            BotCommand("admin", "👑 Admin Control Panel ကိုဖွင့်ရန်"),
        ]
        try:
            await application.bot.set_my_commands(admin_commands, scope=BotCommandScopeChat(chat_id=ADMIN_ID))
            logger.info(f"Custom commands set for ADMIN_ID {ADMIN_ID}")
        except Exception as e:
            logger.error(f"Failed to set admin commands: {e}")

    asyncio.create_task(background_tasks_loop(application))

# --- MAIN FUNCTION ---
def main():
    init_db()
    start_web_server_in_thread()
    logger.info("Web server running in a thread.")
    application = Application.builder().token(BOT_TOKEN).post_init(post_init).build()

    # Command Handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("new", new_email))
    application.add_handler(CommandHandler("myemails", my_emails))
    application.add_handler(CommandHandler("random", random_address_command))
    application.add_handler(CommandHandler("admin", admin_panel))
    
    # Callback Handler
    application.add_handler(CallbackQueryHandler(button_handler))

    logger.info("Bot is starting to poll...")
    application.run_polling()

if __name__ == '__main__':
    main()
