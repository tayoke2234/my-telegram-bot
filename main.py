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
from telegram.ext import Application, CommandHandler, ContextTypes, CallbackQueryHandler, MessageHandler, filters

# --- FLASK WEB SERVER ---
app = Flask(__name__)
@app.route('/')
def home():
    return "Bot is alive and running with new email workflow and admin fixes!"
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
    DAILY_LIMIT = 10 # Increased limit slightly
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
                UNIQUE(user_id, username)
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

# --- BOT COMMANDS & HELPERS ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("👋 Bot မှကြိုဆိုပါတယ်။ Email အသစ်ဖန်တီးရန် `/new` ကိုသုံးပါ။")

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    help_text = """
    **ℹ️ အကူအညီ နှင့် အသုံးပြုပုံ**

    - `/new`: ကျပန်းနာမည်ဖြင့် email ဖန်တီးရန်။
    - `/new <name>`: ကိုယ်ပိုင်နာမည်ဖြင့် email ဖန်တီးရန်။
    - `/myemails`: သင်ဖန်တီးထားသော email လိပ်စာများကို ကြည့်ရန်။
    - `/admin`: (Admin only) Bot ကို ထိန်းချုပ်ရန်။
    """
    await update.message.reply_text(help_text, parse_mode='Markdown')

async def new_email(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    username = ''.join(random.choices(string.ascii_lowercase + string.digits, k=8))
    if context.args:
        arg_name = context.args[0].lower()
        if arg_name.isalnum():
            username = arg_name
        else:
            await update.message.reply_text("❌ Username ပုံစံမှားယွင်းနေပါသည်။ (a-z, 0-9 သာ)"); return
    
    full_address = f"{username}@{YOUR_DOMAIN}"
    async with db_lock:
        try:
            with get_db_conn() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT COUNT(*) FROM addresses WHERE user_id = ? AND creation_date = ?", (user_id, date.today()))
                if cursor.fetchone()[0] >= DAILY_LIMIT:
                    await update.message.reply_text(f"⚠️ တစ်နေ့တာအတွက် သတ်မှတ်ထားတဲ့ အီးမေးလ် {DAILY_LIMIT} ခု ပြည့်သွားပါပြီ။"); return
                
                cursor.execute("INSERT INTO addresses (user_id, username, full_address, creation_date) VALUES (?, ?, ?, ?)",
                               (user_id, username, full_address, date.today()))
                conn.commit()
                await update.message.reply_text(f"✅ အီးမေးလ်လိပ်စာအသစ် ရပါပြီ:\n\n`{full_address}`", parse_mode='Markdown')
        except sqlite3.IntegrityError:
            await update.message.reply_text(f"⚠️ `{username}` ဆိုတဲ့လိပ်စာက ရှိပြီးသားဖြစ်နေပါသည်။", parse_mode='Markdown')
        except Exception as e:
            logger.error(f"Error in new_email: {e}")
            await update.message.reply_text("❌ အမှားအယွင်းတစ်ခု ဖြစ်ပွားပါသည်။")

async def my_emails(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    async with db_lock:
        with get_db_conn() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT full_address FROM addresses WHERE user_id = ?", (user_id,))
            addresses = cursor.fetchall()

    if not addresses:
        await update.message.reply_text("သင်ဖန်တီးထားတဲ့ အီးမေးလ်လိပ်စာ မရှိသေးပါ။ `/new` ကိုသုံးပြီး အသစ်ဖန်တီးနိုင်ပါတယ်။"); return

    message_text = "📬 **သင်၏ Email လိပ်စာများ:**\n\n" + "\n".join([f"- `{addr[0]}`" for addr in addresses])
    await update.message.reply_text(message_text, parse_mode='Markdown')

# --- NEW WORKFLOW: Show Email Content on Demand ---
async def show_email_content(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    try:
        email_id = int(query.data.split(':')[1])
        async with db_lock:
            with get_db_conn() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT from_address, subject, body, received_at FROM emails WHERE id = ?", (email_id,))
                email_data = cursor.fetchone()

        if not email_data:
            await query.answer("❌ Email ကို ရှာမတွေ့ပါ။", show_alert=True)
            await query.edit_message_reply_markup(reply_markup=None) # Remove button
            return

        from_addr, subject, body, received_at_str = email_data
        received_at = datetime.fromisoformat(received_at_str).strftime('%Y-%m-%d %H:%M')
        body_text = body if body else "[Email body is empty]"
        if len(body_text) > 3800: body_text = body_text[:3800] + "\n\n[...]"
        
        # Send the full content as a NEW message
        message_text = f"**From:**\n`{from_addr}`\n\n**Subject:**\n`{subject}`\n\n**Received:**\n`{received_at}`\n----------------------------------------\n\n{body_text}"
        await context.bot.send_message(chat_id=query.from_user.id, text=message_text, parse_mode='Markdown')
        
        # Acknowledge the button press and remove the button
        await query.answer("✅ Email content sent.")
        await query.edit_message_text(f"✅ Opened email from: {from_addr}", reply_markup=None)

    except Exception as e:
        logger.error(f"Error in show_email_content for email_id {query.data}: {e}", exc_info=True)
        await query.answer("❌ Email ကိုဖွင့်ရာတွင် အမှားအယွင်းဖြစ်ပွားပါသည်။", show_alert=True)


# --- ADMIN PANEL (FIXED) ---
async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    try:
        async with db_lock:
            with get_db_conn() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT COUNT(DISTINCT user_id) FROM addresses")
                user_count = cursor.fetchone()[0]
                cursor.execute("SELECT COUNT(*) FROM addresses")
                email_count = cursor.fetchone()[0]
        db_size_mb = round(os.path.getsize(DB_PATH) / (1024 * 1024), 2) if os.path.exists(DB_PATH) else 0
        text = f"👑 **Admin Panel**\n- 👥 Users: `{user_count}`\n- 📧 Emails: `{email_count}`\n- 💽 DB: `{db_size_mb} MB`"
        keyboard = [[InlineKeyboardButton("👥 User စာရင်းကြည့်ရန်", callback_data="admin:users")]]
        
        if update.callback_query: await update.callback_query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')
        else: await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')
    except Exception as e:
        logger.error(f"Error in admin_panel: {e}")

async def show_admin_users(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    try:
        async with db_lock:
            with get_db_conn() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT user_id, COUNT(id) FROM addresses GROUP BY user_id")
                users = cursor.fetchall()
        
        if not users:
            text = "👥 Bot ကိုအသုံးပြုနေသူ မရှိသေးပါ။"
        else:
            text = "👥 **Active User List:**\n\n" + "\n".join([f"- ID: `{uid}` (Emails: {count})" for uid, count in users])
        
        keyboard = [[InlineKeyboardButton("◀️ Admin Panel သို့ပြန်သွားရန်", callback_data="admin:panel")]]
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')
    except Exception as e:
        logger.error(f"Error in show_admin_users: {e}", exc_info=True)
        await query.answer("❌ User list ကိုပြသရာတွင် အမှားအယွင်းဖြစ်ပွားပါသည်။", show_alert=True)

# --- BACKGROUND EMAIL FETCHING ---
async def fetch_and_process_emails(application: Application):
    try:
        with imaplib.IMAP4_SSL(IMAP_SERVER) as mail:
            mail.login(CATCH_ALL_EMAIL, CATCH_ALL_PASSWORD)
            mail.select("inbox")
            _, messages = mail.search(None, '(UNSEEN)') # Fetch only unread emails
            email_ids = messages[0].split()
            if not email_ids: return
            
            logger.info(f"Found {len(email_ids)} new unread emails. Processing...")
            await process_emails_in_db(application, mail, email_ids)
            # Mark emails as read after processing
            for mail_id in email_ids:
                mail.store(mail_id, '+FLAGS', '\\Seen')
    except Exception as e:
        logger.error(f"IMAP Error: {e}")

async def process_emails_in_db(application, mail, email_ids):
    async with db_lock:
        with get_db_conn() as conn:
            for email_id_bytes in reversed(email_ids):
                try:
                    cursor = conn.cursor()
                    _, msg_data = mail.fetch(email_id_bytes, "(RFC822)")
                    msg = email.message_from_bytes(msg_data[0][1])
                    message_id_header = msg.get("Message-ID")
                    if not message_id_header: continue

                    cursor.execute("SELECT id FROM emails WHERE message_id = ?", (message_id_header,))
                    if cursor.fetchone(): continue

                    to_address = (email.utils.parseaddr(msg.get("To"))[1] or email.utils.parseaddr(msg.get("Delivered-To"))[1])
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
                                    body = part.get_payload(decode=True).decode(part.get_content_charset() or 'utf-8', 'ignore'); break
                        else: body = msg.get_payload(decode=True).decode(msg.get_content_charset() or 'utf-8', 'ignore')
                        
                        cursor.execute("INSERT INTO emails (address_id, message_id, from_address, subject, body, received_at) VALUES (?, ?, ?, ?, ?, ?)", 
                                       (address_id, message_id_header, from_address, subject, body, datetime.now()))
                        new_db_email_id = cursor.lastrowid
                        conn.commit()
                        
                        # Send notification with button to view content
                        notification_text = f"📧 **{from_address}** ထံမှ စာအသစ်ရောက်ရှိပါသည်။"
                        keyboard = [[InlineKeyboardButton("📖 စာအပြည့်အစုံဖတ်ရန်", callback_data=f"read_email:{new_db_email_id}")]]
                        await application.bot.send_message(chat_id=user_id, text=notification_text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')

                except Exception as e:
                    logger.error(f"Error processing single email: {e}", exc_info=True)

async def background_tasks_loop(application: Application):
    logger.info("Background tasks loop started.")
    while True:
        await fetch_and_process_emails(application)
        await asyncio.sleep(30) # Check more frequently

async def post_init(application: Application):
    commands = [
        BotCommand("start", "Bot ကိုစတင်ရန်"),
        BotCommand("new", "Email အသစ်ဖန်တီးရန်"),
        BotCommand("myemails", "သင်၏ email များကိုကြည့်ရန်"),
        BotCommand("help", "အကူအညီကြည့်ရန်"),
    ]
    await application.bot.set_my_commands(commands)
    if ADMIN_ID != 0:
        admin_commands = commands + [BotCommand("admin", "👑 Admin Panel")]
        await application.bot.set_my_commands(admin_commands, scope=BotCommandScopeChat(chat_id=ADMIN_ID))
    asyncio.create_task(background_tasks_loop(application))

def main():
    init_db()
    start_web_server_in_thread()
    application = Application.builder().token(BOT_TOKEN).post_init(post_init).build()

    # Handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("new", new_email))
    application.add_handler(CommandHandler("myemails", my_emails))
    application.add_handler(CommandHandler("admin", admin_panel))
    
    # Callback Handlers
    application.add_handler(CallbackQueryHandler(show_email_content, pattern=r'^read_email:'))
    application.add_handler(CallbackQueryHandler(show_admin_users, pattern=r'^admin:users'))
    application.add_handler(CallbackQueryHandler(admin_panel, pattern=r'^admin:panel'))

    logger.info("Bot is starting to poll...")
    application.run_polling()

if __name__ == '__main__':
    main()
