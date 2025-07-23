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
                               (user_id, username, full_address, date.today(), None))
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
    await show_my_emails_handler(update.effective_user.id, update.message.reply_text)

async def show_my_emails_handler(user_id: int, reply_func, is_edit: bool = False):
    async with db_lock:
        with get_db_conn() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT username, full_address FROM addresses WHERE user_id = ?", (user_id,))
            addresses = cursor.fetchall()

    if not addresses:
        text = "သင်ဖန်တီးထားတဲ့ အီးမေးလ်လိပ်စာ မရှိသေးပါ။"
        if is_edit: await reply_func(text=text, reply_markup=None)
        else: await reply_func(text)
        return

    message_text = "📬 **သင်၏ Email များကို စီမံရန်:**\n\n"
    keyboard = []
    for username, full_address in addresses:
        message_text += f"- `{full_address}`\n"
        keyboard.append([
            InlineKeyboardButton(f"📥 Inbox: {username}", callback_data=f"inbox:{username}:0"),
            InlineKeyboardButton(f"🗑️ Delete", callback_data=f"user_delete_confirm:{username}")
        ])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    if is_edit:
        await reply_func(text=message_text, reply_markup=reply_markup, parse_mode='Markdown')
    else:
        await reply_func(message_text, reply_markup=reply_markup, parse_mode='Markdown')

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
    if page > 0: pagination_buttons.append(InlineKeyboardButton("◀️ Prev", callback_data=f"country_page:{page-1}"))
    if end_index < len(COUNTRIES): pagination_buttons.append(InlineKeyboardButton("Next ▶️", callback_data=f"country_page:{page+1}"))
    if pagination_buttons: keyboard.append(pagination_buttons)
        
    await reply_func("🌍 ကျေးဇူးပြု၍ နိုင်ငံတစ်ခုရွေးချယ်ပါ:", reply_markup=InlineKeyboardMarkup(keyboard))

# --- ADMIN PANEL ---
async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id != ADMIN_ID: return

    async with db_lock:
        with get_db_conn() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(DISTINCT user_id) FROM addresses")
            user_count = cursor.fetchone()[0]
            cursor.execute("SELECT COUNT(*) FROM addresses")
            email_count = cursor.fetchone()[0]
    
    db_size_mb = round(os.path.getsize(DB_PATH) / (1024 * 1024), 2) if os.path.exists(DB_PATH) else 0
    text = (
        f"👑 **Admin Control Panel** 👑\n\n"
        f"📊 **Bot Stats:**\n"
        f"  - 👥 Active Users: `{user_count}`\n"
        f"  - 📧 Total Emails Created: `{email_count}`\n"
        f"  - 💽 DB Storage Used: `{db_size_mb} MB`\n"
    )
    keyboard = [[InlineKeyboardButton("👥 User စာရင်းကြည့်ရန်", callback_data="admin_list_users:0")], [InlineKeyboardButton("🔄 Refresh Stats", callback_data="admin_panel")]]
    
    if update.callback_query: await update.callback_query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')
    else: await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')

# --- INLINE BUTTON HANDLER ---
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data.split(':')
    action = data[0]
    user_id = query.from_user.id

    if action == "inbox": await show_inbox(query, username=data[1], page=int(data[2]))
    elif action == "read_email": await show_full_email(query, email_id=int(data[1]))
    elif action == "back_to_myemails": await show_my_emails_handler(user_id, query.edit_message_text, is_edit=True)
    elif action == "user_delete_confirm": await confirm_user_delete(query, username=data[1])
    elif action == "user_delete_execute": await execute_user_delete(query, username=data[1])
    elif action == "country_page": await show_country_page(query.edit_message_text, page=int(data[1]))
    elif action == "gen_address": await generate_address(query, country=data[1])
    elif action == "cancel_delete": await show_my_emails_handler(user_id, query.edit_message_text, is_edit=True)
    elif user_id == ADMIN_ID:
        if action == "admin_panel": await admin_panel(update, context)
        # ... other admin actions
    else:
        await query.answer("⛔️ You are not authorized for this action.", show_alert=True)

async def confirm_user_delete(query: Update, username: str):
    keyboard = [[
        InlineKeyboardButton("✅ Yes, delete", callback_data=f"user_delete_execute:{username}"),
        InlineKeyboardButton("❌ Cancel", callback_data="back_to_myemails")
    ]]
    await query.edit_message_text(text=f"❓ `{username}@{YOUR_DOMAIN}` ကို အပြီးတိုင်ဖျက်ရန် သေချာပါသလား?", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')

async def execute_user_delete(query: Update, username: str):
    user_id = query.from_user.id
    async with db_lock:
        with get_db_conn() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM addresses WHERE user_id = ? AND username = ?", (user_id, username))
            conn.commit()
    await query.edit_message_text(f"🗑️ `{username}@{YOUR_DOMAIN}` ကို အောင်မြင်စွာဖျက်ပြီးပါပြီ။", parse_mode='Markdown')
    await asyncio.sleep(2)
    await show_my_emails_handler(user_id, query.edit_message_text, is_edit=True)

async def generate_address(query: Update, country: str):
    data = ADDRESS_DATA.get(country)
    if not data: return
    address = (
        f"**Random Address for {country}**\n\n"
        f"**Street:** {random.randint(100, 9999)} {random.choice(data['streets'])}\n"
        f"**City:** {random.choice(data['cities'])}\n"
        f"**Zip Code:** {random.choice(data['zips'])}"
    )
    await query.edit_message_text(address, parse_mode='Markdown')
    
async def show_inbox(query, username, page):
    user_id = query.from_user.id
    async with db_lock:
        with get_db_conn() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT id FROM addresses WHERE username = ? AND (user_id = ? OR ? = ?)", (username, user_id, user_id, ADMIN_ID))
            address_row = cursor.fetchone()
            if not address_row: return
            
            address_id = address_row[0]
            emails_per_page = 5
            offset = page * emails_per_page
            cursor.execute("SELECT id, from_address, subject FROM emails WHERE address_id = ? ORDER BY received_at DESC LIMIT ? OFFSET ?", (address_id, emails_per_page, offset))
            emails = cursor.fetchall()
            cursor.execute("SELECT COUNT(*) FROM emails WHERE address_id = ?", (address_id,))
            total_emails = cursor.fetchone()[0]

    full_address = f"{username}@{YOUR_DOMAIN}"
    if total_emails == 0: 
        await query.edit_message_text(f"📥 `{full_address}` ၏ inbox ထဲမှာ email မရှိသေးပါ။", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("◀️ Email စာရင်းများသို့ ပြန်သွားရန်", callback_data="back_to_myemails")]]), parse_mode='Markdown'); return

    message_text = f"📥 **Inbox: {full_address}** (Page {page + 1})\n\nဖတ်ရှုရန် email တစ်ခုကို ရွေးချယ်ပါ:"
    keyboard = []
    for email_id, from_addr, subject in emails:
        button_text = f"📧 {from_addr[:25]} - {subject[:30]}"
        if len(from_addr) > 25 or len(subject) > 30: button_text += "..."
        keyboard.append([InlineKeyboardButton(button_text, callback_data=f"read_email:{email_id}")])
    
    pagination_buttons = []
    if page > 0: pagination_buttons.append(InlineKeyboardButton("◀️ ရှေ့", callback_data=f"inbox:{username}:{page-1}"))
    if (page + 1) * emails_per_page < total_emails: pagination_buttons.append(InlineKeyboardButton("နောက် ▶️", callback_data=f"inbox:{username}:{page+1}"))
    if pagination_buttons: keyboard.append(pagination_buttons)
    keyboard.append([InlineKeyboardButton("◀️ Email စာရင်းများသို့ ပြန်သွားရန်", callback_data="back_to_myemails")])

    await query.edit_message_text(text=message_text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')

# <<< MODIFIED AND MOST ROBUST VERSION >>>
async def show_full_email(query, email_id):
    try:
        async with db_lock:
            with get_db_conn() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT e.from_address, e.subject, e.body, e.received_at, a.username
                    FROM emails e JOIN addresses a ON e.address_id = a.id
                    WHERE e.id = ?
                """, (email_id,))
                email_data = cursor.fetchone()

        if not email_data:
            await query.edit_message_text("❌ Error: Email not found.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("◀️ Email စာရင်းများသို့ ပြန်သွားရန်", callback_data="back_to_myemails")]]))
            return

        from_addr, subject, body, received_at_str, username = email_data
        received_at = datetime.fromisoformat(received_at_str).strftime('%Y-%m-%d %H:%M')

        max_len = 3800
        body_text = body if body else "[Email body is empty]"
        if len(body_text) > max_len:
            body_text = body_text[:max_len] + "\n\n[...စာသားအရှည်ကိုဖြတ်တောက်ထားပါသည်...]"

        message_text = (
            f"**From:**\n`{from_addr}`\n\n"
            f"**Subject:**\n`{subject}`\n\n"
            f"**Received:**\n`{received_at}`\n"
            f"----------------------------------------\n\n"
            f"{body_text}"
        )
        
        keyboard = [[InlineKeyboardButton("◀️ Inbox သို့ပြန်သွားရန်", callback_data=f"inbox:{username}:0")]]
        await query.edit_message_text(text=message_text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')

    except Exception as e:
        logger.error(f"CRITICAL ERROR in show_full_email for email_id {email_id}: {e}", exc_info=True)
        await query.answer("❌ Email ကိုပြသရာတွင် အမှားအယွင်းဖြစ်ပွားပါသည်။", show_alert=True)

# --- BACKGROUND TASKS & BOT SETUP ---
def fetch_and_process_emails(application: Application):
    try:
        with imaplib.IMAP4_SSL(IMAP_SERVER) as mail:
            mail.login(CATCH_ALL_EMAIL, CATCH_ALL_PASSWORD)
            mail.select("inbox")
            yesterday = date.today() - timedelta(days=1)
            _, messages = mail.search(None, f'(SENTSINCE {yesterday.strftime("%d-%b-%Y")})')
            email_ids = messages[0].split()
            if not email_ids: return
            
            logger.info(f"Found {len(email_ids)} emails. Checking...")
            asyncio.run(process_emails_in_db(application, mail, email_ids))
    except Exception as e:
        logger.error(f"IMAP Error: {e}")

async def process_emails_in_db(application, mail, email_ids):
    async with db_lock:
        with get_db_conn() as conn:
            for email_id in reversed(email_ids):
                try:
                    cursor = conn.cursor()
                    _, msg_data = mail.fetch(email_id, "(RFC822)")
                    msg = email.message_from_bytes(msg_data[0][1])
                    message_id_header = msg.get("Message-ID")
                    if not message_id_header: continue

                    cursor.execute("SELECT id FROM emails WHERE message_id = ?", (message_id_header,))
                    if cursor.fetchone(): continue

                    to_address = (email.utils.parseaddr(msg.get("To"))[1] or 
                                  email.utils.parseaddr(msg.get("Delivered-To"))[1])
                    if not to_address or YOUR_DOMAIN not in to_address: continue
                    
                    username = to_address.split('@')[0].lower()
                    cursor.execute("SELECT id, user_id FROM addresses WHERE username = ?", (username,))
                    address_row = cursor.fetchone()
                    if address_row:
                        address_id, user_id = address_row
                        subject_header = decode_header(msg["Subject"])[0]
                        from_header = decode_header(msg.get("From"))[0]
                        
                        subject = subject_header[0].decode(subject_header[1] or 'utf-8', 'ignore') if isinstance(subject_header[0], bytes) else subject_header[0]
                        from_address = from_header[0].decode(from_header[1] or 'utf-8', 'ignore') if isinstance(from_header[0], bytes) else from_header[0]
                        
                        body = ""
                        if msg.is_multipart():
                            for part in msg.walk():
                                if part.get_content_type() == "text/plain":
                                    body = part.get_payload(decode=True).decode(part.get_content_charset() or 'utf-8', 'ignore')
                                    break
                        else:
                            body = msg.get_payload(decode=True).decode(msg.get_content_charset() or 'utf-8', 'ignore')
                        
                        cursor.execute("INSERT INTO emails (address_id, message_id, from_address, subject, body, received_at) VALUES (?, ?, ?, ?, ?, ?)", 
                                       (address_id, message_id_header, from_address, subject, body, datetime.now()))
                        conn.commit()
                        
                        notification = f"🔔 *Email အသစ်ရောက်ရှိ* `{to_address}`\n\n`/myemails` ကိုသုံးပြီး inbox ထဲဝင်ကြည့်နိုင်ပါပြီ။"
                        asyncio.run_coroutine_threadsafe(application.bot.send_message(chat_id=user_id, text=notification, parse_mode='Markdown'), application.loop)
                except Exception as e:
                    logger.error(f"Error processing single email: {e}")

async def background_tasks_loop(application: Application):
    logger.info("Background tasks loop started.")
    while True:
        fetch_and_process_emails(application)
        await asyncio.sleep(60)

async def post_init(application: Application):
    user_commands = [
        BotCommand("start", "Bot ကိုစတင်ရန်"),
        BotCommand("new", "Email အသစ်ဖန်တီးရန်"),
        BotCommand("myemails", "သင်၏ email များကိုကြည့်ရန်"),
        BotCommand("random", "လိပ်စာအတုဖန်တီးရန်"),
        BotCommand("help", "အကူအညီကြည့်ရန်"),
    ]
    await application.bot.set_my_commands(user_commands)
    if ADMIN_ID != 0:
        admin_commands = user_commands + [BotCommand("admin", "👑 Admin Panel")]
        await application.bot.set_my_commands(admin_commands, scope=BotCommandScopeChat(chat_id=ADMIN_ID))
    asyncio.create_task(background_tasks_loop(application))

# --- MAIN FUNCTION ---
def main():
    init_db()
    start_web_server_in_thread()
    application = Application.builder().token(BOT_TOKEN).post_init(post_init).build()

    # Command Handlers
    handlers = [
        CommandHandler("start", start), CommandHandler("help", help_command),
        CommandHandler("new", new_email), CommandHandler("myemails", my_emails),
        CommandHandler("random", random_address_command), CommandHandler("admin", admin_panel),
        CallbackQueryHandler(button_handler)
    ]
    application.add_handlers(handlers)

    logger.info("Bot is starting to poll...")
    application.run_polling()

if __name__ == '__main__':
    main()
