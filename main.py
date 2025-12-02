import logging
import os
import time
from dataclasses import dataclass, field
from datetime import date
from typing import Dict, Optional, Tuple, List

from telegram import (
    Update,
    ChatJoinRequest,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    ChatPermissions,
)
from telegram.ext import (
    ApplicationBuilder,
    ContextTypes,
    ChatJoinRequestHandler,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    filters,
)

# ---------------- Logging ----------------
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("OmniGate")

# ---------------- Env vars ----------------
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID_ENV = os.getenv("ADMIN_ID")  # owner/global admin (optional but recommended)


def get_admin_id() -> Optional[int]:
    if not ADMIN_ID_ENV:
        return None
    try:
        return int(ADMIN_ID_ENV)
    except ValueError:
        logger.warning("ADMIN_ID is not a valid integer.")
        return None


# ---------------- In-memory state ----------------

@dataclass
class ChatConfig:
    # Join logic
    mode: str = "AUTO"  # AUTO, FILTERED, OFF
    require_username: bool = False
    block_bots: bool = True
    min_username_length: int = 0

    # Moderation
    block_links: bool = False
    banned_words: List[str] = field(default_factory=list)

    # Warnings
    warnings_enabled: bool = True
    warnings_limit: int = 3
    warnings_mute_minutes: int = 10
    warnings_action: str = "mute"  # "mute" or "kick"

    # Flood control
    flood_enabled: bool = False
    flood_max_msgs: int = 5
    flood_window_seconds: int = 10

    # Welcome / verification
    safe_welcome_enabled: bool = False
    welcome_message: Optional[str] = None

    # Clean service messages
    clean_service_messages: bool = False

    # Strict mode (manual toggle)
    strict_mode_enabled: bool = False

    # Stats
    approved_total: int = 0
    declined_total: int = 0
    approved_today: int = 0
    declined_today: int = 0
    last_stats_date: date = field(default_factory=date.today)


# chat_id -> ChatConfig
chat_configs: Dict[int, ChatConfig] = {}

# Known chats for /mychats
known_chats: Dict[int, Dict[str, str]] = {}  # chat_id -> {"title": ..., "type": ...}

# Per-user warnings (chat_id, user_id) -> warning_count
user_warnings: Dict[Tuple[int, int], int] = {}

# Flood tracking (chat_id, user_id) -> list[timestamps]
flood_activity: Dict[Tuple[int, int], List[float]] = {}

# Pending verification for safe welcome (chat_id, user_id) -> bool
pending_verification: Dict[Tuple[int, int], bool] = {}


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


def remember_chat(chat) -> None:
    try:
        title = chat.title or "(no title)"
    except Exception:
        title = "(no title)"
    known_chats[chat.id] = {
        "title": title,
        "type": chat.type,
    }


def chat_type_label(chat) -> str:
    if chat.type in ("group", "supergroup"):
        return "group"
    if chat.type == "channel":
        return "channel"
    return "chat"


async def is_user_admin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Check if user is global admin or chat admin."""
    user = update.effective_user
    chat = update.effective_chat
    owner_id = get_admin_id()

    if user is None or chat is None:
        return False

    # Global owner/admin
    if owner_id and user.id == owner_id:
        return True

    # In private chat, only global admin is considered admin
    if chat.type == "private":
        return False

    try:
        admins = await context.bot.get_chat_administrators(chat.id)
    except Exception as e:
        logger.warning("Failed to get chat admins for %s: %s", chat.id, e)
        return False

    return any(a.user.id == user.id for a in admins)


async def is_bot_admin(chat_id: int, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Check if OmniGate has admin rights in this chat."""
    me = await context.bot.get_me()
    try:
        member = await context.bot.get_chat_member(chat_id, me.id)
    except Exception as e:
        logger.warning("Failed to get bot member info in chat %s: %s", chat_id, e)
        return False

    return member.status in ("administrator", "creator")


async def audit_log(context: ContextTypes.DEFAULT_TYPE, text: str) -> None:
    owner_id = get_admin_id()
    if not owner_id:
        return
    try:
        await context.bot.send_message(
            chat_id=owner_id, text=f"🛡 OmniGate Log\n\n{text}"
        )
    except Exception:
        pass


# ---------------- Command Handlers ----------------

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    chat = update.effective_chat
    owner_id = get_admin_id()

    if chat.type == "private":
        text = (
            "👋 Hello! I am *OmniGate Bot*.\n\n"
            "I manage join requests, filter spam, and keep your groups and channels clean.\n\n"
            "To use me:\n"
            "1️⃣ Add me to your group or channel\n"
            "2️⃣ Promote me as admin (manage members + delete messages)\n"
            "3️⃣ Inside that group/channel, send `/settings` to open the control panel.\n\n"
            "You can also use `/mychats` here to see where we are both admins."
        )
        if owner_id and user and user.id == owner_id:
            text += (
                "\n\nYou are registered as the global owner. "
                "You will receive audit logs and error reports."
            )
        await update.message.reply_markdown(text)
    else:
        remember_chat(chat)
        await update.message.reply_text(
            "✅ OmniGate is active in this chat.\n\n"
            "Only admins can configure me.\n"
            "Send /settings to open the control panel."
        )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat = update.effective_chat
    if chat.type == "private":
        scope = "in your groups/channels"
    else:
        scope = f"in this {chat_type_label(chat)}"

    text = (
        f"🤖 *OmniGate Help* ({scope})\n\n"
        "Core commands:\n"
        "• `/settings` – open the admin control panel\n"
        "• `/status` – show join stats for this chat\n"
        "• `/mychats` – (in DM) list chats where you and I are admins\n"
    )
    await update.message.reply_markdown(text)


async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat = update.effective_chat
    remember_chat(chat)
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


async def mychats_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """DM-only: show chats where both user and OmniGate are admins."""
    chat = update.effective_chat
    user = update.effective_user

    if chat.type != "private":
        await update.message.reply_text(
            "Please use /mychats in a private chat with me."
        )
        return

    if not known_chats:
        await update.message.reply_text(
            "I don't have any recorded chats yet. "
            "Use /settings inside a group or channel where I'm added."
        )
        return

    groups = []
    channels = []

    for chat_id, info in known_chats.items():
        try:
            member = await context.bot.get_chat_member(chat_id, user.id)
        except Exception:
            continue

        if member.status not in ("administrator", "creator"):
            continue

        title = info.get("title", "(no title)")
        ctype = info.get("type", "group")
        if ctype in ("group", "supergroup"):
            groups.append(title)
        elif ctype == "channel":
            channels.append(title)

    if not groups and not channels:
        await update.message.reply_text(
            "You are not an admin in any chat where I am also an admin."
        )
        return

    lines = [f"🧩 *OmniGate Admin Overview for* @{user.username or user.id}\n"]
    if groups:
        lines.append("\n*Groups:*")
        for g in groups:
            lines.append(f"• {g}")
    if channels:
        lines.append("\n*Channels:*")
        for c in channels:
            lines.append(f"• {c}")

    lines.append(
        "\n\nTo configure a specific chat, open it and send `/settings` there."
    )

    await update.message.reply_markdown("\n".join(lines))


# ---------------- Settings Panel (Inline Buttons) ----------------

def build_settings_keyboard(cfg: ChatConfig) -> InlineKeyboardMarkup:
    def on_off(value: bool) -> str:
        return "ON ✅" if value else "OFF ❌"

    buttons = [
        [
            InlineKeyboardButton(f"Mode: {cfg.mode}", callback_data="cfg:mode"),
        ],
        [
            InlineKeyboardButton(f"Require username: {on_off(cfg.require_username)}", callback_data="cfg:req_user"),
            InlineKeyboardButton(f"Block bots: {on_off(cfg.block_bots)}", callback_data="cfg:block_bots"),
        ],
        [
            InlineKeyboardButton(f"Block links: {on_off(cfg.block_links)}", callback_data="cfg:block_links"),
            InlineKeyboardButton(f"Clean service: {on_off(cfg.clean_service_messages)}", callback_data="cfg:clean_svc"),
        ],
        [
            InlineKeyboardButton(f"Warnings: {on_off(cfg.warnings_enabled)}", callback_data="cfg:warnings"),
            InlineKeyboardButton(f"Flood: {on_off(cfg.flood_enabled)}", callback_data="cfg:flood"),
        ],
        [
            InlineKeyboardButton(f"Safe welcome: {on_off(cfg.safe_welcome_enabled)}", callback_data="cfg:safe_welcome"),
            InlineKeyboardButton(f"Strict mode: {on_off(cfg.strict_mode_enabled)}", callback_data="cfg:strict"),
        ],
        [
            InlineKeyboardButton("Banned words 📜", callback_data="cfg:banned_words"),
            InlineKeyboardButton("Welcome msg ✏️", callback_data="cfg:welcome_msg"),
        ],
    ]
    return InlineKeyboardMarkup(buttons)


def settings_summary_text(chat, cfg: ChatConfig) -> str:
    return (
        f"⚙️ *OmniGate Settings – {chat.title or 'this chat'}*\n\n"
        f"Mode: `{cfg.mode}`\n"
        f"Require username: `{'ON' if cfg.require_username else 'OFF'}`\n"
        f"Block bots: `{'ON' if cfg.block_bots else 'OFF'}`\n"
        f"Block links: `{'ON' if cfg.block_links else 'OFF'}`\n"
        f"Clean service messages: `{'ON' if cfg.clean_service_messages else 'OFF'}`\n"
        f"Warnings: `{'ON' if cfg.warnings_enabled else 'OFF'}` (limit: `{cfg.warnings_limit}`, action: `{cfg.warnings_action}`)\n"
        f"Flood: `{'ON' if cfg.flood_enabled else 'OFF'}` (max `{cfg.flood_max_msgs}` / `{cfg.flood_window_seconds}`s)\n"
        f"Safe welcome: `{'ON' if cfg.safe_welcome_enabled else 'OFF'}`\n"
        f"Strict mode: `{'ON' if cfg.strict_mode_enabled else 'OFF'}`\n"
        f"Custom welcome: `{'YES' if cfg.welcome_message else 'NO'}`\n"
        f"Banned words: `{len(cfg.banned_words)}` entries\n"
        "\nTap the buttons below to toggle options."
    )


async def settings_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat = update.effective_chat
    user = update.effective_user

    if chat.type == "private":
        await update.message.reply_text(
            "Use /settings inside a group or channel where I am an admin."
        )
        return

    remember_chat(chat)

    if not await is_user_admin(update, context):
        await update.message.reply_text("❌ This menu is for chat admins only.")
        return

    if not await is_bot_admin(chat.id, context):
        await update.message.reply_text(
            "⚠️ I need to be an admin here with permission to manage members and delete messages "
            "before I can apply settings."
        )
        return

    cfg = get_chat_config(chat.id)
    text = settings_summary_text(chat, cfg)
    keyboard = build_settings_keyboard(cfg)

    await update.message.reply_markdown(text, reply_markup=keyboard)
    await audit_log(
        context,
        f"Admin {user.mention_html()} opened settings in {chat.title} ({chat.id}).",
    )


async def settings_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    data = query.data
    chat = query.message.chat
    user = update.effective_user

    if not await is_user_admin(update, context):
        await query.edit_message_text(
            "❌ Only chat admins can use this panel."
        )
        return

    cfg = get_chat_config(chat.id)
    changed = False
    info_msg = ""

    if data == "cfg:mode":
        cfg.mode = {"AUTO": "FILTERED", "FILTERED": "OFF", "OFF": "AUTO"}[cfg.mode]
        changed = True
        info_msg = f"Mode changed to {cfg.mode}."
    elif data == "cfg:req_user":
        cfg.require_username = not cfg.require_username
        changed = True
        info_msg = f"Require username set to {cfg.require_username}."
    elif data == "cfg:block_bots":
        cfg.block_bots = not cfg.block_bots
        changed = True
        info_msg = f"Block bots set to {cfg.block_bots}."
    elif data == "cfg:block_links":
        cfg.block_links = not cfg.block_links
        changed = True
        info_msg = f"Block links set to {cfg.block_links}."
    elif data == "cfg:clean_svc":
        cfg.clean_service_messages = not cfg.clean_service_messages
        changed = True
        info_msg = f"Clean service messages set to {cfg.clean_service_messages}."
    elif data == "cfg:warnings":
        cfg.warnings_enabled = not cfg.warnings_enabled
        changed = True
        info_msg = f"Warnings enabled set to {cfg.warnings_enabled}."
    elif data == "cfg:flood":
        cfg.flood_enabled = not cfg.flood_enabled
        changed = True
        info_msg = f"Flood control enabled set to {cfg.flood_enabled}."
    elif data == "cfg:safe_welcome":
        cfg.safe_welcome_enabled = not cfg.safe_welcome_enabled
        changed = True
        info_msg = f"Safe welcome set to {cfg.safe_welcome_enabled}."
    elif data == "cfg:strict":
        cfg.strict_mode_enabled = not cfg.strict_mode_enabled
        changed = True
        info_msg = f"Strict mode set to {cfg.strict_mode_enabled}."
    elif data == "cfg:banned_words":
        # Simple info for now; advanced UI could be added later
        if cfg.banned_words:
            words = "\n".join(f"- {w}" for w in cfg.banned_words)
            await query.edit_message_text(
                f"🚫 *Banned words in this chat:*\n\n{words}\n\n"
                "You can add/remove words via commands or future UI.",
                parse_mode="Markdown",
            )
        else:
            await query.edit_message_text(
                "No banned words set for this chat.\n\n"
                "Use future commands/UI to add some.",
            )
        return
    elif data == "cfg:welcome_msg":
        await query.edit_message_text(
            "✏️ Custom welcome message is not yet interactive via buttons.\n\n"
            "For now, you can hardcode or extend this bot to support `/set_welcome`."
        )
        return

    if changed:
        text = settings_summary_text(chat, cfg)
        keyboard = build_settings_keyboard(cfg)
        try:
            await query.edit_message_text(
                text=text,
                parse_mode="Markdown",
                reply_markup=keyboard,
            )
        except Exception as e:
            logger.warning("Failed to edit settings message: %s", e)

        await audit_log(
            context,
            f"Admin {user.mention_html()} changed setting '{data}' in {chat.title} ({chat.id}).",
        )


# ---------------- Join Handling ----------------

async def handle_join_request(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    join_request: ChatJoinRequest = update.chat_join_request
    chat = join_request.chat
    user = join_request.from_user
    cfg = get_chat_config(chat.id)
    remember_chat(chat)
    owner_id = get_admin_id()

    logger.info(
        "Join request: chat_id=%s chat_title=%s chat_type=%s user_id=%s username=%s is_bot=%s",
        chat.id,
        chat.title,
        chat.type,
        user.id,
        user.username,
        user.is_bot,
    )

    # OFF mode: leave pending
    if cfg.mode == "OFF":
        logger.info("Mode OFF for chat_id=%s, leaving join request pending.", chat.id)
        if owner_id:
            try:
                await context.bot.send_message(
                    chat_id=owner_id,
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

    # Basic filters for FILTERED mode (and optionally strict_mode)
    if cfg.mode == "FILTERED" or cfg.strict_mode_enabled:
        if cfg.block_bots and user.is_bot:
            allowed = False
            reasons.append("User is a bot.")

        if cfg.require_username and not user.username:
            allowed = False
            reasons.append("Missing username.")

        if cfg.min_username_length > 0 and user.username:
            if len(user.username) < cfg.min_username_length:
                allowed = False
                reasons.append(
                    f"Username too short (< {cfg.min_username_length})."
                )

    # In AUTO mode, we can still optionally block bots if strict_mode_enabled
    if cfg.mode == "AUTO" and cfg.strict_mode_enabled and user.is_bot:
        allowed = False
        reasons.append("User is a bot (strict mode).")

    try:
        if allowed:
            await context.bot.approve_chat_join_request(chat_id=chat.id, user_id=user.id)
            cfg.approved_total += 1
            cfg.approved_today += 1
            logger.info("Approved join request user_id=%s chat_id=%s", user.id, chat.id)

            # Safe welcome: restrict until verify
            if cfg.safe_welcome_enabled and chat.type in ("group", "supergroup"):
                try:
                    await context.bot.restrict_chat_member(
                        chat_id=chat.id,
                        user_id=user.id,
                        permissions=ChatPermissions(
                            can_send_messages=False,
                            can_send_media_messages=False,
                            can_send_other_messages=False,
                            can_add_web_page_previews=False,
                        ),
                    )
                except Exception as e:
                    logger.warning("Failed to restrict new member: %s", e)

                pending_verification[(chat.id, user.id)] = True

                # Send verification button in group
                keyboard = InlineKeyboardMarkup(
                    [
                        [
                            InlineKeyboardButton(
                                "✅ I am human", callback_data=f"verify:{chat.id}:{user.id}"
                            )
                        ]
                    ]
                )
                try:
                    await context.bot.send_message(
                        chat_id=chat.id,
                        text=(
                            f"Welcome {user.mention_html()}!\n\n"
                            "Please tap the button below within a few minutes to verify you are human. "
                            "Until then, your permissions are limited."
                        ),
                        reply_markup=keyboard,
                        parse_mode="HTML",
                    )
                except Exception as e:
                    logger.warning("Failed to send verification message: %s", e)
            else:
                # Normal welcome DM
                chat_label = chat.title or "this chat"
                if chat.type in ("group", "supergroup"):
                    type_label = "group"
                elif chat.type == "channel":
                    type_label = "channel"
                else:
                    type_label = "chat"

                if cfg.welcome_message:
                    welcome_text = cfg.welcome_message
                else:
                    welcome_text = (
                        f"✅ You have been approved to join {chat_label}.\n\n"
                        f"This {type_label} uses OmniGate to manage join requests.\n"
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

            if owner_id:
                reason_text = "; ".join(reasons) if reasons else "Filtered by rules."
                try:
                    await context.bot.send_message(
                        chat_id=owner_id,
                        text=(
                            f"❌ Declined join request in {chat.title} ({chat.id}).\n"
                            f"User: {user.mention_html()} ({user.id})\n"
                            f"Reason: {reason_text}"
                        ),
                        parse_mode="HTML",
                    )
                except Exception as e:
                    logger.warning("Failed to notify owner about decline: %s", e)

    except Exception as e:
        logger.error("Error handling join request: %s", e, exc_info=True)
        if owner_id:
            try:
                await context.bot.send_message(
                    chat_id=owner_id,
                    text=(
                        f"⚠️ Error while processing join request in {chat.title} ({chat.id}).\n"
                        f"User: {user.mention_html()} ({user.id})\n"
                        f"Error: {e}"
                    ),
                    parse_mode="HTML",
                )
            except Exception:
                pass


async def verify_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    data = query.data
    await query.answer()

    if not data.startswith("verify:"):
        return

    parts = data.split(":")
    if len(parts) != 3:
        return

    chat_id = int(parts[1])
    user_id = int(parts[2])

    user = update.effective_user
    if user.id != user_id:
        await query.answer("This button is not for you.", show_alert=True)
        return

    key = (chat_id, user_id)
    if key not in pending_verification:
        await query.answer("You are already verified or no longer pending.")
        return

    cfg = get_chat_config(chat_id)

    # Remove restriction
    try:
        await context.bot.restrict_chat_member(
            chat_id=chat_id,
            user_id=user_id,
            permissions=ChatPermissions(
                can_send_messages=True,
                can_send_media_messages=True,
                can_send_other_messages=True,
                can_add_web_page_previews=True,
            ),
        )
    except Exception as e:
        logger.warning("Failed to unrestrict verified member: %s", e)

    pending_verification.pop(key, None)

    await query.edit_message_text(
        f"✅ Thank you, {user.mention_html()}. You are now verified and can participate.",
        parse_mode="HTML",
    )


# ---------------- Moderation: links, banned words, warnings, flood ----------------

async def moderation_message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    chat = update.effective_chat
    user = update.effective_user

    if not message or not message.text or not user or chat.type not in ("group", "supergroup"):
        return

    remember_chat(chat)
    cfg = get_chat_config(chat.id)
    text_lower = message.text.lower()
    user_key = (chat.id, user.id)

    # Flood control
    if cfg.flood_enabled:
        now = time.time()
        bucket = flood_activity.setdefault(user_key, [])
        bucket.append(now)
        # keep only recent
        window = cfg.flood_window_seconds
        bucket[:] = [t for t in bucket if now - t <= window]
        if len(bucket) > cfg.flood_max_msgs:
            # Too many messages
            try:
                await message.delete()
            except Exception as e:
                logger.warning("Failed to delete flood message: %s", e)
            if cfg.warnings_enabled:
                await apply_warning(chat, user, context, reason="Flood / spam")
            return

    # Block links
    if cfg.block_links:
        has_url = False
        if message.entities:
            for e in message.entities:
                if e.type in ("url", "text_link"):
                    has_url = True
                    break
        if any(x in text_lower for x in ("http://", "https://", "www.", "t.me/")):
            has_url = True

        if has_url:
            try:
                await message.delete()
            except Exception as e:
                logger.warning("Failed to delete link message: %s", e)
            if cfg.warnings_enabled:
                await apply_warning(chat, user, context, reason="Links are not allowed")
            return

    # Banned words
    if cfg.banned_words:
        for bad in cfg.banned_words:
            if bad and bad.lower() in text_lower:
                try:
                    await message.delete()
                except Exception as e:
                    logger.warning("Failed to delete banned word message: %s", e)
                if cfg.warnings_enabled:
                    await apply_warning(chat, user, context, reason=f"Banned word: {bad}")
                return


async def apply_warning(chat, user, context: ContextTypes.DEFAULT_TYPE, reason: str) -> None:
    cfg = get_chat_config(chat.id)
    key = (chat.id, user.id)
    count = user_warnings.get(key, 0) + 1
    user_warnings[key] = count

    try:
        await context.bot.send_message(
            chat_id=chat.id,
            text=(
                f"⚠️ Warning {count}/{cfg.warnings_limit} for {user.mention_html()}.\n"
                f"Reason: {reason}"
            ),
            parse_mode="HTML",
        )
    except Exception as e:
        logger.warning("Failed to send warning message: %s", e)

    if count >= cfg.warnings_limit:
        # Take action
        if cfg.warnings_action == "mute":
            until = int(time.time() + cfg.warnings_mute_minutes * 60)
            try:
                await context.bot.restrict_chat_member(
                    chat_id=chat.id,
                    user_id=user.id,
                    permissions=ChatPermissions(
                        can_send_messages=False,
                        can_send_media_messages=False,
                        can_send_other_messages=False,
                        can_add_web_page_previews=False,
                    ),
                    until_date=until,
                )
            except Exception as e:
                logger.warning("Failed to mute user after warnings: %s", e)
            action_text = f"User muted for {cfg.warnings_mute_minutes} minutes."
        else:
            try:
                await context.bot.ban_chat_member(chat.id, user.id)
            except Exception as e:
                logger.warning("Failed to kick user after warnings: %s", e)
            action_text = "User kicked from the group."

        owner_id = get_admin_id()
        if owner_id:
            try:
                await context.bot.send_message(
                    chat_id=owner_id,
                    text=(
                        f"🚫 Warnings limit reached in {chat.title} ({chat.id}).\n"
                        f"User: {user.mention_html()} ({user.id})\n"
                        f"Action: {action_text}\n"
                        f"Reason: {reason}"
                    ),
                    parse_mode="HTML",
                )
            except Exception:
                pass


# ---------------- Clean service messages ----------------

async def service_message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    chat = update.effective_chat

    if not message or chat.type not in ("group", "supergroup"):
        return

    cfg = get_chat_config(chat.id)
    if not cfg.clean_service_messages:
        return

    # These are status updates like join/leave/pin, etc.
    try:
        await message.delete()
    except Exception as e:
        logger.warning("Failed to delete service message: %s", e)


# ---------------- Main ----------------

def main() -> None:
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN environment variable is missing.")

    app = ApplicationBuilder().token(BOT_TOKEN).build()

    # Commands
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("status", status_command))
    app.add_handler(CommandHandler("mychats", mychats_command))
    app.add_handler(CommandHandler("settings", settings_command))

    # Settings callbacks + verification button
    app.add_handler(CallbackQueryHandler(settings_callback, pattern=r"^cfg:"))
    app.add_handler(CallbackQueryHandler(verify_callback, pattern=r"^verify:"))

    # Join requests
    app.add_handler(ChatJoinRequestHandler(handle_join_request))

    # Moderation for regular text messages
    app.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND & (filters.ChatType.GROUPS),
            moderation_message_handler,
        )
    )

    # Service messages (join/leave/pin/etc.)
    app.add_handler(
        MessageHandler(
            filters.StatusUpdate.ALL & (filters.ChatType.GROUPS),
            service_message_handler,
        )
    )

    logger.info("OmniGate starting with long polling...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
