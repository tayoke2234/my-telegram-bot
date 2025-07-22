# Telegram Temp Mail Bot (Final Version - Corrected UNSEEN Bug)
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

# --- FLASK WEB SERVER (for UptimeRobot on Render) ---
app = Flask(__name__)
@app.route('/')
def home():
    return "Bot is alive and running with all fixes!"
def run_web_server():
    port = int(os.environ.get('PORT', 8080))
    app.run(host='0.0.0.0', port=port)
def start_web_server_in_thread():
    t = Thread(target=run_web_server)
    t.start()

# --- LOGGING SETUP ---
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# --- CONFIGURATION (from Render Environment Variables) ---
try:
    BOT_TOKEN = os.environ['BOT_TOKEN']
    YOUR_DOMAIN = os.environ['YOUR_DOMAIN']
    CATCH_ALL_EMAIL = os.environ['CATCH_ALL_EMAIL']
    CATCH_ALL_PASSWORD = os.environ['CATCH_ALL_PASSWORD']
    ADMIN_ID = int(os.environ.get('ADMIN_ID', 0))
    IMAP_SERVER = "imap.gmail.com"
    DAILY_LIMIT = 10
except KeyError as e:
    print(f"!!! FATAL ERROR: Environment variable {e} is not set on Render.com !!!")
    exit()

# --- DATABASE SETUP ---
DB_PATH = '/data/tempmail.db' if os.path.exists('/data') else 'tempmail.db'

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
                id INTEGER PRIMARY KEY, address_id INTEGER NOT NULL,
                message_id TEXT UNIQUE, -- To prevent duplicate processing
                from_address TEXT NOT NULL, subject TEXT NOT NULL, body TEXT,
                received_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (address_id) REFERENCES addresses (id) ON DELETE CASCADE
            )
        ''')
        conn.commit()

# --- HELPER FUNCTIONS ---
def parse_duration(time_str: str) -> timedelta | None:
    match = re.match(r'(\d+)([mhd])', time_str.lower())
    if not match: return None
    value, unit = int(match.groups()[0]), match.groups()[1]
    if unit == 'm': return timedelta(minutes=value)
    if unit == 'h': return timedelta(hours=value)
    if unit == 'd': return timedelta(days=value)
    return None

def format_remaining_time(expires_at: datetime) -> str:
    if datetime.now() >= expires_at: return "(expired)"
    remaining = expires_at - datetime.now()
    d, h, m = remaining.days, remaining.seconds // 3600, (remaining.seconds // 60) % 60
    if d > 0: return f"({d}d {h}h left)"
    if h > 0: return f"({h}h {m}m left)"
    if m > 0: return f"({m}m left)"
    return "(<1m left)"

# --- BOT COMMANDS ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("👋 Bot မှကြိုဆိုပါတယ်။ Command များအတွက် 'Menu' ခလုတ်ကိုနှိပ်ပါ။")

async def create_email_entry(user_id: int, username: str, expires_at: datetime | None):
    full_address = f"{username}@{YOUR_DOMAIN}"
    try:
        with get_db_conn() as conn:
            cursor = conn.cursor()
            cursor.execute("INSERT INTO addresses (user_id, username, full_address, creation_date, expires_at) VALUES (?, ?, ?, ?, ?)",
                           (user_id, username, full_address, date.today(), expires_at))
            conn.commit()
        return full_address, None
    except sqlite3.IntegrityError:
        return None, f"⚠️ `{username}` ဆိုတဲ့လိပ်စာက ရှိပြီးသားဖြစ်နေပါသည်။"
    except Exception as e:
        logger.error(f"Error in create_email_entry: {e}")
        return None, "❌ အမှားအယွင်းတစ်ခု ဖြစ်ပွားပါသည်။"

async def new_random_email(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    username = ''.join(random.choices(string.ascii_lowercase + string.digits, k=8))
    full_address, error = await create_email_entry(user_id, username, None)
    if error:
        await update.message.reply_text(error, parse_mode='Markdown')
    else:
        await update.message.reply_text(f"✅ ကျပန်းလိပ်စာအသစ် (သက်တမ်းမကုန်):\n\n`{full_address}`", parse_mode='Markdown')

async def new_timed_email(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not context.args or len(context.args) not in [1, 2]:
        await update.message.reply_text("ℹ️ အသုံးပြုပုံ: `/newtimed <name> [time]`\nဥပမာ: `/newtimed test 1h`")
        return

    username = context.args[0].lower()
    if not username.isalnum():
        await update.message.reply_text("❌ Username ပုံစံမှားယွင်းနေပါသည်။ (a-z, 0-9 သာ)"); return

    expires_at, time_str = None, None
    if len(context.args) == 2:
        time_str = context.args[1]
        duration = parse_duration(time_str)
        if duration:
            expires_at = datetime.now() + duration
        else:
            await update.message.reply_text("❌ အချိန်သတ်မှတ်ပုံစံမှားယွင်းနေပါသည်။ (ဥပမာ: 30m, 2h, 1d)"); return
    
    full_address, error = await create_email_entry(user_id, username, expires_at)
    if error:
        await update.message.reply_text(error, parse_mode='Markdown')
    else:
        expiry_info = f"\n\n🕒 ဤလိပ်စာသည် {time_str} ကြာလျှင် အလိုအလျောက်ပျက်သွားပါမည်။" if expires_at else ""
        await update.message.reply_text(f"✅ အောင်မြင်စွာဖန်တီးပြီးပါပြီ:\n\n`{full_address}`{expiry_info}", parse_mode='Markdown')

async def my_emails(update: Update, context: ContextTypes.DEFAULT_TYPE):
    with get_db_conn() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT username, full_address, expires_at FROM addresses WHERE user_id = ?", (update.effective_user.id,))
        addresses = cursor.fetchall()
    if not addresses:
        await update.message.reply_text("သင်ဖန်တီးထားတဲ့ အီးမေးလ်လိပ်စာ မရှိသေးပါ။"); return

    keyboard = []
    for username, full_address, expires_at_str in addresses:
        button_text = full_address
        if expires_at_str: button_text += f" {format_remaining_time(datetime.fromisoformat(expires_at_str))}"
        keyboard.append([InlineKeyboardButton(button_text, callback_data=f"inbox:{username}:0")])
    await update.message.reply_text('📬 သင်၏ အီးမေးလ်လိပ်စာများ (Inbox ကြည့်ရန်နှိပ်ပါ):', reply_markup=InlineKeyboardMarkup(keyboard))

# --- INLINE BUTTON HANDLER & OTHER FUNCTIONS ---
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data.split(':')
    action = data[0]
    if action == "inbox": await show_inbox(query, username=data[1], page=int(data[2]))
    elif action == "read_full": await show_full_email(query, email_id=int(data[1]))

async def show_inbox(query, username, page):
    user_id = query.from_user.id
    with get_db_conn() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT id FROM addresses WHERE user_id = ? AND username = ?", (user_id, username))
        address_row = cursor.fetchone()
        if not address_row: await query.edit_message_text("❌ Error: Email address not found."); return
        
        address_id, emails_per_page = address_row[0], 5
        offset = page * emails_per_page
        cursor.execute("SELECT id, from_address, subject, received_at FROM emails WHERE address_id = ? ORDER BY received_at DESC LIMIT ? OFFSET ?", (address_id, emails_per_page, offset))
        emails = cursor.fetchall()
        cursor.execute("SELECT COUNT(*) FROM emails WHERE address_id = ?", (address_id,))
        total_emails = cursor.fetchone()[0]

    if total_emails == 0: await query.edit_message_text(f"📥 `{username}@{YOUR_DOMAIN}` ၏ inbox ထဲမှာ email မရှိသေးပါ။", parse_mode='Markdown'); return

    message_text = f"📥 **{username}@{YOUR_DOMAIN}** (Inbox: {page + 1})\n\n"
    keyboard = []
    for email_id, from_addr, subject, received_at in emails:
        message_text += f"**From:** {from_addr}\n**Sub:** {subject}\n_{datetime.fromisoformat(received_at).strftime('%Y-%m-%d %H:%M')}_\n\n"
        keyboard.append([InlineKeyboardButton(f"📧 အပြည့်အစုံဖတ်ရန် ({subject[:10]}...)", callback_data=f"read_full:{email_id}")])

    pagination_buttons = []
    if page > 0: pagination_buttons.append(InlineKeyboardButton("◀️ ရှေ့", callback_data=f"inbox:{username}:{page-1}"))
    if (page + 1) * emails_per_page < total_emails: pagination_buttons.append(InlineKeyboardButton("နောက် ▶️", callback_data=f"inbox:{username}:{page+1}"))
    if pagination_buttons: keyboard.append(pagination_buttons)

    try:
        await query.edit_message_text(text=message_text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')
    except Exception as e:
        if "Message is not modified" not in str(e): logger.error(f"Error editing message for inbox: {e}")

async def show_full_email(query, email_id):
    with get_db_conn() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT from_address, subject, body, received_at FROM emails WHERE id = ?", (email_id,))
        email_data = cursor.fetchone()
    if not email_data: await query.message.reply_text("❌ Email ကိုရှာမတွေ့ပါ။"); return
    from_addr, subject, body, received_at = email_data
    full_email_text = f"--- Email Details ---\n**From:** {from_addr}\n**Subject:** {subject}\n**Received:** {received_at}\n------------------\n{body}"
    await query.message.reply_text(full_email_text, parse_mode='Markdown')

# --- ADMIN COMMANDS ---
async def admin_command_wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE, admin_function):
    if update.effective_user.id != ADMIN_ID: await update.message.reply_text("❌ Admin command ဖြစ်ပါသည်။"); return
    await admin_function(update, context)

async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    with get_db_conn() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM addresses"); total_addrs = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(DISTINCT user_id) FROM addresses"); total_users = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM emails"); total_emails = cursor.fetchone()[0]
    stats_text = f"📊 **Bot Statistics**\n- စုစုပေါင်း အသုံးပြုသူ: {total_users}\n- စုစုပေါင်း လိပ်စာ: {total_addrs}\n- စုစုပေါင်း လက်ခံရရှိသော အီးမေးလ်: {total_emails}"
    await update.message.reply_text(stats_text)

async def storage_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        db_size = os.path.getsize(DB_PATH)
        size_mb = db_size / (1024 * 1024)
        await update.message.reply_text(f"💽 Database storage: {size_mb:.2f} MB")
    except FileNotFoundError: await update.message.reply_text("❌ Database file ကိုရှာမတွေ့ပါ။")

async def adelete_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args or len(context.args) != 1: await update.message.reply_text("ℹ️ အသုံးပြုပုံ: `/adelete <full_email_address>`"); return
    email_to_delete = context.args[0]
    with get_db_conn() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM addresses WHERE full_address = ?", (email_to_delete,))
        rowcount = cursor.rowcount
        conn.commit()
    if rowcount > 0: await update.message.reply_text(f"🗑️ `{email_to_delete}` ကို အောင်မြင်စွာဖျက်ပြီးပါပြီ။", parse_mode='Markdown')
    else: await update.message.reply_text(f"⚠️ `{email_to_delete}` ကို ရှာမတွေ့ပါ။", parse_mode='Markdown')

async def finduser_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args or len(context.args) != 1: await update.message.reply_text("ℹ️ အသုံးပြုပုံ: `/finduser <full_email_address>`"); return
    email_to_find = context.args[0]
    with get_db_conn() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT user_id FROM addresses WHERE full_address = ?", (email_to_find,))
        result = cursor.fetchone()
    if result: await update.message.reply_text(f"👤 `{email_to_find}` ကို ဖန်တီးသူ၏ User ID မှာ:\n`{result[0]}`", parse_mode='Markdown')
    else: await update.message.reply_text(f"⚠️ `{email_to_find}` ကို ရှာမတွေ့ပါ။", parse_mode='Markdown')

async def listusers_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    with get_db_conn() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT DISTINCT user_id FROM addresses")
        users = cursor.fetchall()
    if not users: await update.message.reply_text("ℹ️ Bot ကိုအသုံးပြုနေသော User မရှိသေးပါ။"); return
    user_list = "\n".join([f"- `{user[0]}`" for user in users])
    await update.message.reply_text(f"👥 **Bot အသုံးပြုသူများ စာရင်း:**\n{user_list}", parse_mode='Markdown')

async def deleteuser_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args or not context.args[0].isdigit():
        await update.message.reply_text("ℹ️ အသုံးပြုပုံ: `/deleteuser <user_id>`"); return
    user_id_to_delete = int(context.args[0])
    with get_db_conn() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM addresses WHERE user_id = ?", (user_id_to_delete,))
        rowcount = cursor.rowcount
        conn.commit()
    if rowcount > 0:
        await update.message.reply_text(f"🗑️ User ID `{user_id_to_delete}` နှင့် သက်ဆိုင်သော data အားလုံးကို အောင်မြင်စွာဖျက်ပြီးပါပြီ။", parse_mode='Markdown')
    else: await update.message.reply_text(f"⚠️ User ID `{user_id_to_delete}` ကို ရှာမတွေ့ပါ။", parse_mode='Markdown')

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
        # Fixed: Search for ALL emails, not just UNSEEN
        status, messages = mail.search(None, "ALL")
        if status != 'OK':
            logger.error("IMAP search failed.")
            mail.logout()
            return
        
        email_ids = messages[0].split()
        if not email_ids: mail.logout(); return
        
        logger.info(f"Found {len(email_ids)} total emails. Checking for new ones.")
        with get_db_conn() as conn:
            cursor = conn.cursor()
            for email_id in reversed(email_ids[-20:]): # Check latest 20 emails for performance
                try:
                    _, msg_data = mail.fetch(email_id, "(RFC822)")
                    msg = email.message_from_bytes(msg_data[0][1])
                    
                    message_id_header = msg.get("Message-ID")
                    if not message_id_header: continue

                    # Check if this email has already been processed
                    cursor.execute("SELECT id FROM emails WHERE message_id = ?", (message_id_header,))
                    if cursor.fetchone():
                        continue # Skip already processed email

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
        mail.logout()
    except Exception as e: logger.error(f"IMAP Error: {e}")

async def background_tasks_loop(application: Application):
    logger.info("Background tasks loop started.")
    while True:
        await asyncio.to_thread(fetch_and_process_emails, application)
        await asyncio.to_thread(auto_delete_expired_addresses)
        await asyncio.sleep(60)

async def post_init(application: Application):
    user_commands = [
        BotCommand("start", "Bot ကိုစတင်ရန်"),
        BotCommand("new", "ကျပန်း email အသစ် (သက်တမ်းမကုန်) ဖန်တီးရန်"),
        BotCommand("newtimed", "အချိန်ကန့်သတ်ဖြင့် email ဖန်တီးရန် (ဥပမာ: /newtimed test 1h)"),
        BotCommand("myemails", "သင်၏ email များကိုကြည့်ရှုရန်"),
    ]
    await application.bot.set_my_commands(user_commands)
    
    if ADMIN_ID != 0:
        admin_commands = user_commands + [
            BotCommand("stats", "📊 Bot စာရင်းအင်းများ ကြည့်ရန်"),
            BotCommand("storage", "💽 Database အရွယ်အစား ကြည့်ရန်"),
            BotCommand("listusers", "👥 User အားလုံးကို ကြည့်ရန်"),
            BotCommand("deleteuser", "🚫 User တစ်ဦးကိုဖျက်ရန် (ဥပမာ: /deleteuser ID)"),
            BotCommand("adelete", "🗑️ User email ကိုဖျက်ရန် (ဥပမာ: /adelete name@domain.com)"),
            BotCommand("finduser", "👤 Email ဖြင့် User ID ရှာရန်"),
        ]
        await application.bot.set_my_commands(admin_commands, scope=BotCommandScopeChat(chat_id=ADMIN_ID))
    
    asyncio.create_task(background_tasks_loop(application))

# --- MAIN FUNCTION ---
def main():
    init_db()
    start_web_server_in_thread()
    logger.info("Web server running in a thread.")
    application = Application.builder().token(BOT_TOKEN).post_init(post_init).build()

    # Command Handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("new", new_random_email))
    application.add_handler(CommandHandler("newtimed", new_timed_email))
    application.add_handler(CommandHandler("myemails", my_emails))
    # Admin Handlers
    application.add_handler(CommandHandler("stats", lambda u, c: admin_command_wrapper(u, c, stats_command)))
    application.add_handler(CommandHandler("storage", lambda u, c: admin_command_wrapper(u, c, storage_command)))
    application.add_handler(CommandHandler("adelete", lambda u, c: admin_command_wrapper(u, c, adelete_command)))
    application.add_handler(CommandHandler("finduser", lambda u, c: admin_command_wrapper(u, c, finduser_command)))
    application.add_handler(CommandHandler("listusers", lambda u, c: admin_command_wrapper(u, c, listusers_command)))
    application.add_handler(CommandHandler("deleteuser", lambda u, c: admin_command_wrapper(u, c, deleteuser_command)))
    
    application.add_handler(CallbackQueryHandler(button_handler))

    logger.info("Bot is starting to poll...")
    application.run_polling()

if __name__ == '__main__':
    main()
