import json
import logging
import os
from datetime import date
from pathlib import Path

from cryptography.fernet import Fernet
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes
from aiohttp import web

load_dotenv()

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
DEV_WALLET = os.getenv("DEV_WALLET", "")
CRYSTALS_PER_SNIPE = int(os.getenv("CRYSTALS_PER_SNIPE", 75))
DAILY_BASE_CRYSTALS = int(os.getenv("DAILY_BASE_CRYSTALS", 25))
HIDDEN_FEE_SOL = float(os.getenv("HIDDEN_FEE_SOL", 0.004))
WEBHOOK_URL = os.getenv("WEBHOOK_URL", "")
PORT = int(os.getenv("PORT", 8443))

KEY_FILE = "secret.key"
BALANCES_FILE = "balances.enc"
DAILY_FILE = "dailyLogins.json"
EXAMPLE_MINT = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger(__name__)


def load_or_create_key() -> bytes:
    p = Path(KEY_FILE)
    if p.exists():
        return p.read_bytes()
    key = Fernet.generate_key()
    p.write_bytes(key)
    return key


def encrypt_data(data: dict) -> bytes:
    return Fernet(load_or_create_key()).encrypt(json.dumps(data).encode())


def decrypt_data(encrypted: bytes) -> dict:
    try:
        return json.loads(Fernet(load_or_create_key()).decrypt(encrypted).decode())
    except Exception:
        return {}


def _load_balances() -> dict:
    p = Path(BALANCES_FILE)
    return decrypt_data(p.read_bytes()) if p.exists() else {}


def _save_balances(data: dict):
    Path(BALANCES_FILE).write_bytes(encrypt_data(data))


def get_crystals(user_id: str) -> int:
    return _load_balances().get(user_id, {}).get("crystals", 0)


def add_crystals(user_id: str, amount: int):
    data = _load_balances()
    data.setdefault(user_id, {"crystals": 0})
    data[user_id]["crystals"] = data[user_id].get("crystals", 0) + amount
    _save_balances(data)


def spend_crystals(user_id: str, amount: int) -> bool:
    data = _load_balances()
    current = data.get(user_id, {}).get("crystals", 0)
    if current < amount:
        return False
    data.setdefault(user_id, {})["crystals"] = current - amount
    _save_balances(data)
    return True


def _load_logins() -> dict:
    if not Path(DAILY_FILE).exists():
        return {}
    with open(DAILY_FILE, "r") as f:
        return json.load(f)


def _save_logins(data: dict):
    with open(DAILY_FILE, "w") as f:
        json.dump(data, f, indent=2)


async def send_premium_snipe_alert(update: Update, mint: str):
    user = update.effective_user
    uid = str(user.id)
    first_name = user.first_name or "Trader"
    jupiter_link = f"https://jup.ag/swap/SOL-{mint}"
    short = mint[:8] + "..." + mint[-6:]

    if not spend_crystals(uid, CRYSTALS_PER_SNIPE):
        await update.message.reply_text(
            f"❌ Not enough crystals, {first_name}.\nNeeded: {CRYSTALS_PER_SNIPE} | Balance: {get_crystals(uid)}\nUse /daily or /shop.",
            parse_mode="Markdown",
        )
        return

    logger.info(f"Snipe {mint} for user {uid}")

    await update.message.reply_text(
        f"🚀 *ALCHEMIE SNIPE ALERT* 🚀\n\n"
        f"Yo {first_name}! The scanner just went crazy on this one 🔥\n"
        f"★ Alchemie Score: 92/100 ★\n\n"
        f"📌 Token: `{short}`\n"
        f"🔗 Jupiter: {jupiter_link}\n\n"
        f"💰 {CRYSTALS_PER_SNIPE} crystals spent • Balance: {get_crystals(uid)}\n\n"
        f"Early as fuck. Size smart and let it cook! 💎\n\n"
        f"_Data is key. Alchemie Portal_",
        parse_mode="Markdown",
    )


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    first_name = update.effective_user.first_name or "Trader"
    await update.message.reply_text(
        f"🚀 Welcome, {first_name}!\n\n"
        "Alchemie Sniper is live.\n"
        "/snipe → Premium alert\n"
        "/daily → Free crystals\n"
        "/balance → Your balance\n"
        "/shop → Buy crystals\n\n"
        "_Data is key. Alchemie Portal_",
        parse_mode="Markdown",
    )


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🚀 *Alchemie Sniper Bot*\n\n"
        "/daily   — Claim daily free crystals + streak\n"
        "/snipe   — Get premium snipe alert\n"
        "/balance — Check your crystals\n"
        "/shop    — Buy more crystals\n\n"
        "_Data is key. Alchemie Portal_",
        parse_mode="Markdown",
    )


async def cmd_daily(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    uid = str(user.id)
    first_name = user.first_name or "Trader"
    today = str(date.today())
    yesterday = str(date.fromordinal(date.today().toordinal() - 1))

    logins = _load_logins()
    logins.setdefault(uid, {"last_claim": None, "streak": 0})
    rec = logins[uid]
    last = rec.get("last_claim")
    streak = rec.get("streak", 0)

    if last == today:
        await update.message.reply_text(
            f"⏳ Already claimed today, {first_name}!\nStreak: {streak} days 🔥\nBalance: {get_crystals(uid)} crystals\n\n"
            "_Data is key. Alchemie Portal_",
            parse_mode="Markdown",
        )
        return

    streak = streak + 1 if last == yesterday else 1
    bonus = min((streak - 1) * 5, 50)
    earned = DAILY_BASE_CRYSTALS + bonus

    rec["last_claim"] = today
    rec["streak"] = streak
    _save_logins(logins)
    add_crystals(uid, earned)

    await update.message.reply_text(
        f"💎 Daily reward claimed, {first_name}!\n\n"
        f"Streak: {streak} days 🔥\n"
        f"Earned: +{earned} crystals\n"
        f"Total: {get_crystals(uid)} crystals\n\n"
        "Keep the streak alive!\n\n"
        "_Data is key. Alchemie Portal_",
        parse_mode="Markdown",
    )


async def cmd_balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = str(update.effective_user.id)
    first_name = update.effective_user.first_name or "Trader"
    crystals = get_crystals(uid)
    streak = _load_logins().get(uid, {}).get("streak", 0)

    await update.message.reply_text(
        f"💎 Balance — {first_name}\n\n"
        f"Crystals: {crystals}\n"
        f"Streak: {streak} days 🔥\n\n"
        "/daily → Free crystals\n"
        "/shop → Buy more\n\n"
        "_Data is key. Alchemie Portal_",
        parse_mode="Markdown",
    )


async def cmd_snipe(update: Update, context: ContextTypes.DEFAULT_TYPE):
    mint = context.args[0].strip() if context.args else EXAMPLE_MINT
    await send_premium_snipe_alert(update, mint)


async def cmd_shop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🛒 Crystal Shop\n\n"
        "Pay with USDT on TRON via NowPayments.\n"
        "Use your existing payment link.",
        parse_mode="Markdown",
    )


async def webhook_handler(request):
    application = request.app["application"]
    data = await request.json()
    update = Update.de_json(data, application.bot)
    await application.process_update(update)
    return web.Response(text="OK")


async def main():
    application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    application.add_handler(CommandHandler("start", cmd_start))
    application.add_handler(CommandHandler("help", cmd_help))
    application.add_handler(CommandHandler("daily", cmd_daily))
    application.add_handler(CommandHandler("balance", cmd_balance))
    application.add_handler(CommandHandler("snipe", cmd_snipe))
    application.add_handler(CommandHandler("shop", cmd_shop))

    await application.initialize()
    await application.bot.set_webhook(f"{WEBHOOK_URL}/webhook")

    app = web.Application()
    app["application"] = application
    app.router.add_post("/webhook", webhook_handler)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()
    
    logger.info(f"Webhook bot started on port {PORT}")
    
    import asyncio
    await asyncio.Event().wait()


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
