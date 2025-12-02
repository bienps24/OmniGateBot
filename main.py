import logging
import os
from dataclasses import dataclass, field
from datetime import date
from typing import Dict, Optional

from telegram import (
    Update,
    ChatJoinRequest,
)
from telegram.ext import (
    ApplicationBuilder,
    ContextTypes,
    ChatJoinRequestHandler,
    CommandHandler,
)

# ---------------- Logging (professional) ----------------
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# ---------------- Env vars ----------------
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID_ENV = os.getenv("ADMIN_ID")  # string, later to int


def get_admin_id() -> Optional[int]:
    if not ADMIN_ID_ENV:
        return None
    try:
        return int(ADMIN_ID_ENV)
    except ValueError:
        logger.warning("ADMIN_ID is not a valid integer.")
        return None


# ---------------- In-memory config & stats ----------------
@dataclass
class ChatConfig:
    mode: str = "AUTO"  # AUTO, FILTERED, OFF
    require_username: bool = False
    block_bots: bool = True
    min_username_length: int = 0

    approved_total: int = 0
    declined_total: int = 0
    approved_today: int = 0
    declined_today: int = 0
    last_stats_date: date = field(default_factory=date.today)


chat_configs: Dict[int, ChatConfig] = {}


def get_chat_config(chat_id: int) -> ChatConfig:
    cfg = chat_configs.get(chat_id)
    if cfg is None:
        cfg = ChatConfig()
        chat_configs[chat_id] = cfg
    # reset daily counters if date changed
    today = date.today()
    if cfg.last_stats_date != today:
        cfg.last_stats_date = today
        cfg.approved_today = 0
        cfg.declined_today = 0
    return cfg


# ---------------- Utility: admin check ----------------
async def is_user_admin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Check if user is global admin or chat admin."""
    user = update.effective_user
    chat = update.effective_chat
    admin_id = get_admin_id()

    if user is None or chat is None:
        return False

    # global owner/admin
    if admin_id and user.id == admin_id:
        return True

    # private chat: only global admin counts
    if chat.type == "private":
        return False

    try:
        admins = await context.bot.get_chat_administrators(chat.id)
    except Exception as e:
        logger.warning("Failed to get chat admins for %s: %s", chat.id, e)
        return False

    return any(a.user.id == user.id for a in admins)


def chat_type_label(chat) -> str:
    if chat.type in ("group", "supergroup"):
        return "group"
    if chat.type == "channel":
        return "channel"
    return "chat"


# ---------------- Command Handlers ----------------
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    chat = update.effective_chat
    admin_id = get_admin_id()

    if chat.type == "private":
        text = (
            "👋 Hello!\n\n"
            "I am a professional join request manager bot.\n"
            "I can automatically approve or filter join requests for groups and channels "
            "where I am an admin.\n\n"
            "To use me:\n"
            "1️⃣ Add me to your group or channel\n"
            "2️⃣ Promote me as admin\n"
            "3️⃣ Enable join requests (Request to Join)\n"
            "4️⃣ Use /settings inside the group/channel (admins only)\n"
        )
        if admin_id and user and user.id == admin_id:
            text += (
                "\nYou are registered as the global admin.\n"
                "Use /status in any chat to see stats."
            )
        await update.message.reply_text(text)
    else:
        await update.message.reply_text(
            "✅ I am active in this chat.\n\n"
            "Only admins can configure me.\n"
            "Use /settings to see current configuration.\n"
        )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat = update.effective_chat
    if chat.type == "private":
        scope = "global or per-chat"
    else:
        scope = "this chat"

    text = (
        "🤖 *Gatekeeper Bot Help*\n\n"
        f"These commands affect {scope}:\n\n"
        "/status - Show current mode and stats\n"
        "/settings - Show configuration\n"
        "/set_mode <auto|filtered|off> - Change mode\n"
        "/set_require_username <on|off>\n"
        "/set_block_bots <on|off>\n"
        "/set_min_username_length <number>\n"
        "/test_join - Simulate join handling (no real approve/decline)\n"
    )
    await update.message.reply_markdown(text)


async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat = update.effective_chat
    cfg = get_chat_config(chat.id)

    text = (
        f"📊 *Status for this {chat_type_label(chat)}*\n\n"
        f"Mode: `{cfg.mode}`\n"
        f"Approved today: `{cfg.approved_today}`\n"
        f"Declined today: `{cfg.declined_today}`\n"
        f"Approved total: `{cfg.approved_total}`\n"
        f"Declined total: `{cfg.declined_total}`\n"
    )
    await update.message.reply_markdown(text)


async def settings_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat = update.effective_chat
    cfg = get_chat_config(chat.id)

    text = (
        f"⚙️ *Settings for this {chat_type_label(chat)}*\n\n"
        f"Mode: `{cfg.mode}`\n"
        f"Require username: `{'ON' if cfg.require_username else 'OFF'}`\n"
        f"Block bots: `{'ON' if cfg.block_bots else 'OFF'}`\n"
        f"Min username length: `{cfg.min_username_length}`\n"
    )
    await update.message.reply_markdown(text)


# --- Helper to parse ON/OFF ---
def parse_on_off(arg: str) -> Optional[bool]:
    arg = arg.strip().lower()
    if arg in ("on", "true", "yes", "y", "1"):
        return True
    if arg in ("off", "false", "no", "n", "0"):
        return False
    return None


# --- Admin-only commands ---
async def set_mode_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await is_user_admin(update, context):
        await update.message.reply_text("❌ This command is for admins only.")
        return

    chat = update.effective_chat
    cfg = get_chat_config(chat.id)

    if not context.args:
        await update.message.reply_text("Usage: /set_mode auto | filtered | off")
        return

    mode = context.args[0].strip().upper()
    if mode not in ("AUTO", "FILTERED", "OFF"):
        await update.message.reply_text("Invalid mode. Use: auto, filtered, or off.")
        return

    cfg.mode = mode
    await update.message.reply_text(f"✅ Mode updated to: {mode}")


async def set_require_username_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await is_user_admin(update, context):
        await update.message.reply_text("❌ This command is for admins only.")
        return

    chat = update.effective_chat
    cfg = get_chat_config(chat.id)

    if not context.args:
        await update.message.reply_text("Usage: /set_require_username on | off")
        return

    value = parse_on_off(context.args[0])
    if value is None:
        await update.message.reply_text("Invalid value. Use: on or off.")
        return

    cfg.require_username = value
    await update.message.reply_text(
        f"✅ Require username set to: {'ON' if value else 'OFF'}"
    )


async def set_block_bots_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await is_user_admin(update, context):
        await update.message.reply_text("❌ This command is for admins only.")
        return

    chat = update.effective_chat
    cfg = get_chat_config(chat.id)

    if not context.args:
        await update.message.reply_text("Usage: /set_block_bots on | off")
        return

    value = parse_on_off(context.args[0])
    if value is None:
        await update.message.reply_text("Invalid value. Use: on or off.")
        return

    cfg.block_bots = value
    await update.message.reply_text(
        f"✅ Block bots set to: {'ON' if value else 'OFF'}"
    )


async def set_min_username_length_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await is_user_admin(update, context):
        await update.message.reply_text("❌ This command is for admins only.")
        return

    chat = update.effective_chat
    cfg = get_chat_config(chat.id)

    if not context.args:
        await update.message.reply_text("Usage: /set_min_username_length <number>")
        return

    try:
        value = int(context.args[0])
    except ValueError:
        await update.message.reply_text("Please provide a valid integer.")
        return

    if value < 0:
        await update.message.reply_text("Value must be 0 or higher.")
        return

    cfg.min_username_length = value
    await update.message.reply_text(f"✅ Min username length set to: {value}")


async def test_join_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Simulate join handling logic (no real approve/decline)."""
    if not await is_user_admin(update, context):
        await update.message.reply_text("❌ This command is for admins only.")
        return

    chat = update.effective_chat
    cfg = get_chat_config(chat.id)

    text = (
        f"🧪 Test Join Handling\n\n"
        f"Mode: {cfg.mode}\n"
        f"Require username: {cfg.require_username}\n"
        f"Block bots: {cfg.block_bots}\n"
        f"Min username length: {cfg.min_username_length}\n\n"
        "This is only a simulation. Real join requests will follow these rules."
    )
    await update.message.reply_text(text)


# ---------------- Join Request Handler ----------------
async def handle_join_request(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    join_request: ChatJoinRequest = update.chat_join_request
    chat = join_request.chat
    user = join_request.from_user
    cfg = get_chat_config(chat.id)
    admin_id = get_admin_id()

    logger.info(
        "Join request: chat_id=%s chat_title=%s chat_type=%s user_id=%s username=%s is_bot=%s",
        chat.id,
        chat.title,
        chat.type,
        user.id,
        user.username,
        user.is_bot,
    )

    # OFF mode → do nothing, just log
    if cfg.mode == "OFF":
        logger.info("Mode OFF for chat_id=%s, leaving request pending.", chat.id)
        if admin_id:
            try:
                await context.bot.send_message(
                    chat_id=admin_id,
                    text=(
                        f"ℹ️ Join request pending in {chat.title} ({chat.id}).\n"
                        f"Mode is OFF, so I am not auto-approving."
                    ),
                )
            except Exception:
                pass
        return

    allowed = True
    reasons = []

    if cfg.mode == "FILTERED":
        # Block bots
        if cfg.block_bots and user.is_bot:
            allowed = False
            reasons.append("User is a bot.")

        # Require username
        if cfg.require_username and not user.username:
            allowed = False
            reasons.append("Missing username.")

        # Min username length
        if cfg.min_username_length > 0 and user.username:
            if len(user.username) < cfg.min_username_length:
                allowed = False
                reasons.append(
                    f"Username too short (< {cfg.min_username_length})."
                )

    # Decide approve/decline
    try:
        if allowed:
            await context.bot.approve_chat_join_request(chat_id=chat.id, user_id=user.id)
            cfg.approved_total += 1
            cfg.approved_today += 1
            logger.info("Approved join request user_id=%s chat_id=%s", user.id, chat.id)

            # Send welcome DM
            chat_label = chat.title or "this chat"
            if chat.type in ("group", "supergroup"):
                type_label = "group"
            elif chat.type == "channel":
                type_label = "channel"
            else:
                type_label = "chat"

            welcome_text = (
                f"✅ You have been approved to join {chat_label}.\n\n"
                f"This {type_label} uses an automatic gatekeeper to manage join requests.\n"
                "Please read the rules and respect other members."
            )
            try:
                await context.bot.send_message(chat_id=user.id, text=welcome_text)
            except Exception as e:
                logger.warning(
                    "Could not send DM to user_id=%s: %s", user.id, e
                )
        else:
            await context.bot.decline_chat_join_request(chat_id=chat.id, user_id=user.id)
            cfg.declined_total += 1
            cfg.declined_today += 1
            logger.info("Declined join request user_id=%s chat_id=%s", user.id, chat.id)

            # Notify admin with reasons
            if admin_id:
                reason_text = "; ".join(reasons) if reasons else "Filtered by rules."
                try:
                    await context.bot.send_message(
                        chat_id=admin_id,
                        text=(
                            f"❌ Declined join request in {chat.title} ({chat.id}).\n"
                            f"User: {user.mention_html()} ({user.id})\n"
                            f"Reason: {reason_text}"
                        ),
                        parse_mode="HTML",
                    )
                except Exception as e:
                    logger.warning("Failed to notify admin about decline: %s", e)

    except Exception as e:
        logger.error("Error handling join request: %s", e, exc_info=True)
        if admin_id:
            try:
                await context.bot.send_message(
                    chat_id=admin_id,
                    text=(
                        f"⚠️ Error while processing join request in {chat.title} ({chat.id}).\n"
                        f"User: {user.mention_html()} ({user.id})\n"
                        f"Error: {e}"
                    ),
                    parse_mode="HTML",
                )
            except Exception:
                pass


# ---------------- Main ----------------
def main() -> None:
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN environment variable is missing.")

    app = ApplicationBuilder().token(BOT_TOKEN).build()

    # Commands
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("status", status_command))
    app.add_handler(CommandHandler("settings", settings_command))
    app.add_handler(CommandHandler("set_mode", set_mode_command))
    app.add_handler(CommandHandler("set_require_username", set_require_username_command))
    app.add_handler(CommandHandler("set_block_bots", set_block_bots_command))
    app.add_handler(CommandHandler("set_min_username_length", set_min_username_length_command))
    app.add_handler(CommandHandler("test_join", test_join_command))

    # Join requests
    app.add_handler(ChatJoinRequestHandler(handle_join_request))

    logger.info("Bot starting with long polling...")
    app.run_polling(allowed_updates=["chat_join_request", "message"])


if __name__ == "__main__":
    main()
