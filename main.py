# main.py

import logging
import os
from telegram import Update
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes

# Enable logging to see errors
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

# --- အမှားကို ကိုင်တွယ်မယ့် function ကို ထည့်ပါ ---
# --- Add the error handling function here ---
async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Log the error and send a telegram message to notify the user."""
    # Log the error so you can see it in your Render logs
    logger.error("Exception while handling an update:", exc_info=context.error)

    # Send a user-friendly message back to the chat where the error happened.
    # This tells the user something went wrong instead of just showing "loading".
    if isinstance(update, Update) and update.effective_chat:
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="တောင်းပန်ပါတယ်။ တစ်ခုခုမှားယွင်းသွားလို့ပါ။ ခဏနေ ပြန်လည်ကြိုးစားပေးပါ။"
        )

# This is a placeholder for your existing command handlers
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Sends a message when the command /start is issued."""
    await update.message.reply_text('Bot is running! Add your other handlers.')

# This is a placeholder for your existing callback query handlers
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Parses the CallbackQuery and updates the message text."""
    query = update.callback_query
    await query.answer()
    # This is where your logic to show email content would go.
    # If an error happens here, the error_handler will now catch it.
    # For example, let's cause an intentional error:
    # x = 1 / 0 
    await query.edit_message_text(text=f"Selected option: {query.data}")


def main() -> None:
    """Start the bot."""
    # Get your bot token from environment variables for security
    # "YOUR_TOKEN" ကို သင့် bot ရဲ့ token နဲ့ အစားထိုးပါ
    TOKEN = os.environ.get("TELEGRAM_TOKEN", "YOUR_TOKEN")
    
    # Create the Application
    application = Application.builder().token(TOKEN).build()

    # Add your command handlers
    application.add_handler(CommandHandler("start", start))
    
    # Add your callback query handlers (for buttons)
    application.add_handler(CallbackQueryHandler(button_handler))

    # --- အရေးကြီးဆုံးအချက်- error handler ကို application ထဲသို့ ထည့်ပါ ---
    # --- CRITICAL STEP: Add the error handler to the application ---
    application.add_error_handler(error_handler)

    # Run the bot until the user presses Ctrl-C
    logger.info("Bot is starting...")
    application.run_polling()
    logger.info("Bot has stopped.")

if __name__ == "__main__":
    main()
