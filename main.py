# Telegram Temp Mail Bot (Dual Domain Fix Version)
# Deployed on Render.com, kept alive by UptimeRobot

import logging
import asyncio
import sqlite3
from datetime import datetime
import os
import random
import string
import re
import imaplib
import email
from email.header import decode_header
from flask import Flask
from markupsafe import escape  # <<< FIX: Import escape from markupsafe
from threading import Thread

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, BotCommand, BotCommandScopeChat
from telegram.ext import Application, CommandHandler, ContextTypes, CallbackQueryHandler
from telegram.constants import ParseMode
from telegram.error import BadRequest

# --- FLASK WEB SERVER ---
app = Flask(__name__)

def start_web_server_in_thread():
    """Starts the Flask web server in a separate thread."""
    def run_web_server():
        port = int(os.environ.get('PORT', 8080))
        app.run(host='0.0.0.0', port=port)
    t = Thread(target=run_web_server)
    t.daemon = True
    t.start()

@app.route('/')
def home():
    return "Bot is alive and running with external inbox!"

# --- LOGGING SETUP ---
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

# --- CONFIGURATION ---
try:
    BOT_TOKEN = os.environ['BOT_TOKEN']
    # NEW: Separate domains for email generation and web hosting
    EMAIL_DOMAIN = os.environ['EMAIL_DOMAIN'] # Your custom domain for emails, e.g., "iam1.qzz.io"
    APP_HOST_DOMAIN = os.environ['APP_HOST_DOMAIN'] # Your Render app domain, e.g., "my-bot.onrender.com"
    
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
db_lock = asyncio.Lock()

def get_db_conn():
    return sqlite3.connect(DB_PATH, timeout=10, check_same_thread=False, detect_types=sqlite3.PARSE_DECLTYPES | sqlite3.PARSE_COLNAMES)

def init_db():
    with get_db_conn() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS addresses (
                id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL,
                full_address TEXT NOT NULL UNIQUE, creation_date DATE NOT NULL
            )
        ''')
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS emails (
                id INTEGER PRIMARY KEY, address_id INTEGER NOT NULL, message_id TEXT UNIQUE,
                from_address TEXT NOT NULL, subject TEXT NOT NULL, body TEXT,
                received_at timestamp,
                FOREIGN KEY (address_id) REFERENCES addresses (id) ON DELETE CASCADE
            )
        ''')
        conn.commit()

# --- FLASK ROUTE TO DISPLAY EMAILS ---
@app.route('/view_email/<int:email_id>')
def view_email(email_id):
    """Renders a simple HTML page to display the email content."""
    try:
        with get_db_conn() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT from_address, subject, body, received_at FROM emails WHERE id = ?", (email_id,))
            email_data = cursor.fetchone()

        if not email_data:
            return "<h1>Email not found</h1><p>The email you are looking for does not exist or has been deleted.</p>", 404

        from_addr, subject, body, received_at_obj = email_data
        received_at_str = received_at_obj.strftime('%Y-%m-%d %H:%M:%S') if isinstance(received_at_obj, datetime) else "N/A"
        safe_from = escape(from_addr)
        safe_subject = escape(subject)
        # The body is already escaped by the escape_markdown function for Telegram, 
        # but we should escape it for HTML display as well.
        safe_body = escape(body).replace('\n', '<br>')

        html = f"""
        <!DOCTYPE html><html lang="en"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>{safe_subject}</title><style>body{{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;line-height:1.6;margin:0;padding:20px;background-color:#f4f4f9;color:#333;}}.container{{max-width:800px;margin:auto;background:#fff;padding:25px;border-radius:8px;box-shadow:0 2px 10px rgba(0,0,0,0.1);}}h1{{font-size:1.8em;color:#111;margin-top:0;}}.meta-info{{font-size:0.9em;color:#555;border-bottom:1px solid #eee;padding-bottom:15px;margin-bottom:20px;}}.meta-info p{{margin:5px 0;}}.email-body{{font-size:1em;white-space:pre-wrap;word-wrap:break-word;}}b{{color:#000;}}</style></head>
        <body><div class="container"><h1>{safe_subject}</h1><div class="meta-info"><p><b>From:</b> {safe_from}</p><p><b>Received:</b> {received_at_str}</p></div>
        <div class="email-body">{safe_body if safe_body else "<p><i>[This email has no content]</i></p>"}</div></div></body></html>
        """
        return html
    except Exception as e:
        logger.error(f"Error rendering email view for ID {email_id}: {e}")
        return "<h1>Server Error</h1><p>An error occurred while trying to display the email.</p>", 500

# --- MARKDOWN SANITIZER ---
def escape_markdown(text: str) -> str:
    """Escapes characters for Telegram's MarkdownV2 parse mode."""
    if not isinstance(text, str): return ""
    # Chars to escape for MarkdownV2
    escape_chars = r'\_*[]()~`>#+-=|{}.!'
    return re.sub(f'([{re.escape(escape_chars)}])', r'\\\1', text)

# --- BOT COMMANDS & HELPERS ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("👋 Bot မှကြိုဆိုပါတယ်။ Email အသစ်ဖန်တီးရန် `/new` ကိုသုံးပါ။")

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    help_text = "*ℹ️ အကူအညီ နှင့် အသုံးပြုပုံ*\n\n- `/new`: ကျပန်းနာမည်ဖြင့် email ဖန်တီးရန်။\n- `/new <name>`: ကိုယ်ပိုင်နာမည်ဖြင့် email ဖန်တီးရန်။\n- `/myemails`: သင်ဖန်တီးထားသော email လိပ်စာများကို ကြည့်ရန်။\n- `/admin`: (Admin only) Bot ကို ထိန်းချုပ်ရန်။"
    await update.message.reply_text(help_text, parse_mode=ParseMode.MARKDOWN_V2)

async def new_email(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    username = ''.join(random.choices(string.ascii_lowercase + string.digits, k=8))
    if context.args:
        arg_name = context.args[0].lower()
        if len(arg_name) > 20:
            await update.message.reply_text("❌ Username သည် အက္ခရာ 20 ထက်မပိုရပါ။"); return
        if arg_name.isalnum():
            username = arg_name
        else:
            await update.message.reply_text("❌ Username ပုံစံမှားယွင်းနေပါသည်။ (a-z, 0-9 သာ)"); return
    
    # Use EMAIL_DOMAIN for email generation
    full_address = f"{username}@{EMAIL_DOMAIN}"
    async with db_lock:
        try:
            with get_db_conn() as conn:
                cursor = conn.cursor()
                # Check daily limit
                cursor.execute("SELECT COUNT(*) FROM addresses WHERE user_id = ? AND creation_date = ?", (user_id, datetime.now().date()))
                if cursor.fetchone()[0] >= DAILY_LIMIT:
                    await update.message.reply_text(f"⚠️ တစ်နေ့တာအတွက် သတ်မှတ်ထားတဲ့ အီးမေးလ် {DAILY_LIMIT} ခု ပြည့်သွားပါပြီ။"); return
                
                # Insert new address
                cursor.execute("INSERT INTO addresses (user_id, full_address, creation_date) VALUES (?, ?, ?)",
                               (user_id, full_address, datetime.now().date()))
                conn.commit()
                await update.message.reply_text(f"✅ အီးမေးလ်လိပ်စာအသစ် ရပါပြီ:\n\n`{full_address}`", parse_mode=ParseMode.MARKDOWN_V2)
        except sqlite3.IntegrityError:
            await update.message.reply_text(f"⚠️ `{full_address}` ဆိုတဲ့လိပ်စာက ရှိပြီးသားဖြစ်နေပါသည်။", parse_mode=ParseMode.MARKDOWN_V2)
        except Exception as e:
            logger.error(f"Error in new_email: {e}")
            await update.message.reply_text("❌ အမှားအယွင်းတစ်ခု ဖြစ်ပွားပါသည်။")

async def my_emails(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    try:
        async with db_lock:
            with get_db_conn() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT full_address FROM addresses WHERE user_id = ?", (user_id,))
                addresses = cursor.fetchall()
        if not addresses:
            await update.message.reply_text("သင်ဖန်တီးထားတဲ့ အီးမေးလ်လိပ်စာ မရှိသေးပါ။ `/new` ကိုသုံးပြီး အသစ်ဖန်တီးနိုင်ပါတယ်။"); return
        
        escaped_addresses = [escape_markdown(f"• {addr[0]}") for addr in addresses]
        message_text = "*📬 သင်၏ Email လိပ်စာများ:*\n\n" + "\n".join(escaped_addresses)
        await update.message.reply_text(message_text, parse_mode=ParseMode.MARKDOWN_V2)
    except Exception as e:
        logger.error(f"Error in my_emails for user {user_id}: {e}")
        await update.message.reply_text("❌ သင်၏လိပ်စာများကို ပြသရာတွင် အမှားအယွင်းဖြစ်ပွားပါသည်။")

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
        
        text = f"*👑 Admin Panel*\n- 👥 Users: `{user_count}`\n- 📧 Addresses: `{email_count}`\n- 💽 DB Size: `{escape_markdown(str(db_size_mb))} MB`"
        keyboard = [[InlineKeyboardButton("👥 User စာရင်းကြည့်ရန်", callback_data="admin:users")]]
        
        if update.callback_query:
            await update.callback_query.answer()
            await update.callback_query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.MARKDOWN_V2)
        else:
            await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.MARKDOWN_V2)
    except Exception as e:
        logger.error(f"Error in admin_panel: {e}")
        error_text = "❌ Admin panel ကိုပြသရာတွင် အမှားအယွင်းဖြစ်ပွားပါသည်။"
        if update.callback_query: await update.callback_query.answer(error_text, show_alert=True)
        else: await update.message.reply_text(error_text)

async def show_admin_users(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query.from_user.id != ADMIN_ID: return
    try:
        await query.answer()
        async with db_lock:
            with get_db_conn() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT user_id, COUNT(id) FROM addresses GROUP BY user_id ORDER BY COUNT(id) DESC")
                users = cursor.fetchall()
        
        if not users: 
            text = "👥 Bot ကိုအသုံးပြုနေသူ မရှိသေးပါ။"
        else: 
            user_lines = [escape_markdown(f"• ID: {uid} (Addresses: {count})") for uid, count in users]
            text = "*👥 Active User List:*\n\n" + "\n".join(user_lines)
            
        keyboard = [[InlineKeyboardButton("◀️ Admin Panel သို့ပြန်သွားရန်", callback_data="admin:panel")]]
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.MARKDOWN_V2)
    except Exception as e:
        logger.error(f"Error in show_admin_users: {e}", exc_info=True)
        await query.answer("❌ User list ကိုပြသရာတွင် အမှားအယွင်းဖြစ်ပွားပါသည်။", show_alert=True)

# --- BACKGROUND EMAIL FETCHING ---
def _blocking_imap_check():
    """Connects to IMAP, fetches unseen emails, marks them as seen, and returns them."""
    try:
        with imaplib.IMAP4_SSL(IMAP_SERVER) as mail:
            mail.login(CATCH_ALL_EMAIL, CATCH_ALL_PASSWORD)
            mail.select("inbox")
            _, messages = mail.search(None, '(UNSEEN)')
            email_ids_bytes = messages[0].split()
            if not email_ids_bytes:
                return []

            fetched_emails = []
            for mail_id in email_ids_bytes:
                _, msg_data = mail.fetch(mail_id, "(RFC822)")
                fetched_emails.append(msg_data[0][1])
                # Mark the email as seen on the server
                mail.store(mail_id, '+FLAGS', '\\Seen')
            return fetched_emails
    except Exception as e:
        logger.error(f"Blocking IMAP check failed: {e}")
        return []

async def fetch_and_process_emails(application: Application):
    """Fetches emails in a separate thread and processes them."""
    raw_emails = await asyncio.to_thread(_blocking_imap_check)
    if not raw_emails:
        return
    
    logger.info(f"Found {len(raw_emails)} new emails. Processing...")
    async with db_lock:
        with get_db_conn() as conn:
            for raw_email_data in raw_emails:
                try:
                    msg = email.message_from_bytes(raw_email_data)
                    
                    message_id_header = msg.get("Message-ID")
                    if not message_id_header:
                        continue # Skip if no message-id to prevent duplicates

                    cursor = conn.cursor()
                    cursor.execute("SELECT id FROM emails WHERE message_id = ?", (message_id_header,))
                    if cursor.fetchone():
                        continue # Already processed

                    to_header = msg.get("To") or msg.get("Delivered-To") or ""
                    to_address = email.utils.parseaddr(to_header)[1].lower()
                    
                    if not to_address or not to_address.endswith(f"@{EMAIL_DOMAIN}"):
                        continue

                    cursor.execute("SELECT id, user_id FROM addresses WHERE full_address = ?", (to_address,))
                    address_row = cursor.fetchone()

                    if address_row:
                        address_id, user_id = address_row
                        
                        # Decode subject and from address properly
                        subject_header = decode_header(msg["Subject"])[0]
                        from_header = decode_header(msg.get("From"))[0]
                        
                        subject = subject_header[0].decode(subject_header[1] or 'utf-8', 'ignore') if isinstance(subject_header[0], bytes) else subject_header[0]
                        from_address = from_header[0].decode(from_header[1] or 'utf-8', 'ignore') if isinstance(from_header[0], bytes) else from_header[0]

                        if not from_address or from_address.isspace():
                            from_address = "Unknown Sender"

                        body = ""
                        if msg.is_multipart():
                            for part in msg.walk():
                                if part.get_content_type() == "text/plain":
                                    try:
                                        body = part.get_payload(decode=True).decode(part.get_content_charset() or 'utf-8', 'ignore')
                                        break
                                    except (UnicodeDecodeError, AttributeError):
                                        body = "[Could not decode email content]"
                        else:
                            try:
                                body = msg.get_payload(decode=True).decode(msg.get_content_charset() or 'utf-8', 'ignore')
                            except (UnicodeDecodeError, AttributeError):
                                body = "[Could not decode email content]"

                        cursor.execute("INSERT INTO emails (address_id, message_id, from_address, subject, body, received_at) VALUES (?, ?, ?, ?, ?, ?)", 
                                       (address_id, message_id_header, from_address, subject, body, datetime.now()))
                        new_db_email_id = cursor.lastrowid
                        conn.commit()

                        # Use APP_HOST_DOMAIN for the view link
                        view_url = f"https://{APP_HOST_DOMAIN}/view_email/{new_db_email_id}"
                        
                        escaped_from = escape_markdown(from_address)
                        escaped_subject = escape_markdown(subject)
                        
                        notification_text = f"📧 *စာအသစ်ရောက်ရှိပါသည်*\n\n*From:* {escaped_from}\n*Subject:* {escaped_subject}"
                        keyboard = [[InlineKeyboardButton("📖 Browser တွင်ဖွင့်ဖတ်ရန်", url=view_url)]]
                        
                        try:
                            await application.bot.send_message(
                                chat_id=user_id, 
                                text=notification_text, 
                                reply_markup=InlineKeyboardMarkup(keyboard), 
                                parse_mode=ParseMode.MARKDOWN_V2
                            )
                        except BadRequest as e:
                            logger.error(f"Failed to send notification to {user_id}: {e}")

                except Exception as e:
                    logger.error(f"Error processing single email in DB: {e}", exc_info=True)

async def background_tasks_loop(application: Application):
    logger.info("Background tasks loop started.")
    while True:
        try:
            await fetch_and_process_emails(application)
        except Exception as e:
            logger.error(f"Error in background_tasks_loop: {e}", exc_info=True)
        # Check for emails every 45 seconds
        await asyncio.sleep(45)

async def post_init(application: Application):
    """Post-initialization function to set up commands and background tasks."""
    commands = [
        BotCommand("start", "Bot ကိုစတင်ရန်"),
        BotCommand("new", "Email အသစ်ဖန်တီးရန်"),
        BotCommand("myemails", "သင်၏ email များကိုကြည့်ရန်"),
        BotCommand("help", "အကူအညီကြည့်ရန်")
    ]
    await application.bot.set_my_commands(commands)
    
    if ADMIN_ID != 0:
        admin_commands = commands + [BotCommand("admin", "👑 Admin Panel")]
        try:
            await application.bot.set_my_commands(admin_commands, scope=BotCommandScopeChat(chat_id=ADMIN_ID))
        except Exception as e:
            logger.warning(f"Could not set admin commands for chat {ADMIN_ID}: {e}")

    # Start the background task
    asyncio.create_task(background_tasks_loop(application))

def main():
    """Start the bot."""
    # Initialize the database
    init_db()
    
    # Start the Flask web server in a background thread
    start_web_server_in_thread()

    # Set up the Telegram bot application
    application = Application.builder().token(BOT_TOKEN).post_init(post_init).build()

    # Add handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("new", new_email))
    application.add_handler(CommandHandler("myemails", my_emails))
    application.add_handler(CommandHandler("admin", admin_panel))
    application.add_handler(CallbackQueryHandler(show_admin_users, pattern=r'^admin:users'))
    application.add_handler(CallbackQueryHandler(admin_panel, pattern=r'^admin:panel'))

    # Start polling
    logger.info("Bot is starting to poll...")
    application.run_polling()

if __name__ == '__main__':
    main()
