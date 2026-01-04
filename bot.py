import os
import asyncio
import logging
from logging.handlers import RotatingFileHandler
from datetime import time, datetime
from zoneinfo import ZoneInfo
import aiosqlite
import re
from telegram.ext import MessageHandler, filters
from telegram import Update
from telegram.error import RetryAfter, Forbidden
from telegram.ext import Application, CommandHandler, ContextTypes
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import CallbackQueryHandler
from collections import deque
from dotenv import load_dotenv
load_dotenv()


# =======================
# LOG FILES
# =======================
ACTIVITY_LOG = "activity.log"
ERROR_LOG = "errors.log"

MAX_LOG_LINES = 4000          # προστασία μνήμης
MAX_TELEGRAM_CHARS = 3500     # για να μην κόβεται το μήνυμα
# =======================
# BOT LOGGER
# =======================
logger = logging.getLogger("bot")
logger.setLevel(logging.INFO)



def tail_lines(path: str, n: int) -> str:
    n = max(1, min(n, MAX_LOG_LINES))
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            dq = deque(f, maxlen=n)
        return "".join(dq).strip()
    except FileNotFoundError:
        return f"(Δεν βρέθηκε αρχείο: {path})"
    except Exception as e:
        return f"(Σφάλμα ανάγνωσης log: {e})"


async def reply_code(update: Update, text: str) -> None:
    # κόψε για να χωράει στο Telegram
    if len(text) > MAX_TELEGRAM_CHARS:
        text = text[-MAX_TELEGRAM_CHARS:]
        text = "…(κόπηκε)\n" + text

    await update.message.reply_text(f"```text\n{text}\n```", parse_mode="Markdown")


def is_admin(chat_id: int) -> bool:
    return chat_id in ADMIN_CHAT_IDS


async def logs_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    if not is_admin(chat_id):
        await update.message.reply_text("⛔ Δεν έχεις δικαίωμα.")
        return

    n = 80
    if context.args and context.args[0].isdigit():
        n = int(context.args[0])

    text = tail_lines(ACTIVITY_LOG, n) or "(κενό)"
    await reply_code(update, text)


async def errors_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    if not is_admin(chat_id):
        await update.message.reply_text("⛔ Δεν έχεις δικαίωμα.")
        return

    n = 120
    if context.args and context.args[0].isdigit():
        n = int(context.args[0])

    text = tail_lines(ERROR_LOG, n) or "(κενό)"
    await reply_code(update, text)


async def logsearch_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    if not is_admin(chat_id):
        await update.message.reply_text("⛔ Δεν έχεις δικαίωμα.")
        return

    if not context.args:
        await update.message.reply_text("Χρήση: /logsearch λέξη [lines]\nπ.χ. /logsearch set 500")
        return

    needle = context.args[0].lower()
    n = 500
    if len(context.args) >= 2 and context.args[1].isdigit():
        n = int(context.args[1])

    block = tail_lines(ACTIVITY_LOG, n)
    hits = [line for line in block.splitlines() if needle in line.lower()]

    out = "\n".join(hits[-200:])  # κόφ’το για να μη γίνεται τεράστιο
    await reply_code(update, out or "(δεν βρέθηκε)")



# ---- Activity handler (INFO+) ----
activity_handler = RotatingFileHandler(
    ACTIVITY_LOG,
    maxBytes=5 * 1024 * 1024,  # 5MB
    backupCount=5,
    encoding="utf-8",
)
activity_handler.setLevel(logging.INFO)
activity_handler.setFormatter(
    logging.Formatter("%(asctime)s %(levelname)s - %(message)s")
)

# ---- Error handler (ERROR+) ----
error_handler = RotatingFileHandler(
    ERROR_LOG,
    maxBytes=5 * 1024 * 1024,  # 5MB
    backupCount=5,
    encoding="utf-8",
)
error_handler.setLevel(logging.ERROR)
error_handler.setFormatter(
    logging.Formatter("%(asctime)s %(levelname)s - %(message)s")
)

# ---- Attach handlers ----
logger.addHandler(activity_handler)
logger.addHandler(error_handler)

# ---- No console output ----
logger.propagate = False

# =======================
# DISABLE LIBRARY LOGS
# =======================
logging.getLogger("httpx").disabled = True
logging.getLogger("telegram").disabled = True
logging.getLogger("telegram.ext").disabled = True
logging.getLogger("apscheduler").disabled = True

DB_PATH = "bot.db"
TZ = ZoneInfo("Europe/Athens")
ADMIN_CHAT_IDS = {6447601553}
DAY_MAP = {
    "δευτερα": 0, "δευτέρα": 0, "δευτ": 0,
    "τριτη": 1, "τρίτη": 1, "τριτ": 1,
    "τεταρτη": 2, "τετάρτη": 2, "τεταρ": 2,
    "πεμπτη": 3, "πέμπτη": 3, "πεμπ": 3,
    "παρασκευη": 4, "παρασκευή": 4, "παρασκ": 4,
    "σαββατο": 5, "σάββατο": 5, "σαβ": 5,
    "κυριακη": 6, "κυριακή": 6, "κυρ": 6,
}
DAY_NAMES = ["Δευτέρα", "Τρίτη", "Τετάρτη", "Πέμπτη", "Παρασκευή", "Σάββατο", "Κυριακή"]
HELP_TEXT = (
    "🤖 *Ρυθμίσεις μηνύματος*\n\n"
    "Με αυτό το bot διαλέγεις *πότε* θέλεις να σου έρχεται το μήνυμα.\n\n"
    "━━━━━━━━━━━━━━\n"
    "▶️ *Ενεργοποίηση*\n"
    "/start\n\n"
    "⏸️ *Παύση*\n"
    "/stop\n\n"
    "━━━━━━━━━━━━━━\n"
    "🕒 *Αλλαγή μέρας & ώρας*\n"
    "Απλά αντέγραψε ένα από τα παρακάτω (ή γράψε το δικό σου):\n\n"
    "`/set Κυριακή 23:58`\n"
    "`Δευτέρα 08:00`\n"
    "`/set 21:15`\n\n"
    "━━━━━━━━━━━━━━\n"
    "📅 *Δες τη ρύθμισή σου*\n"
    "/when\n\n"
    "━━━━━━━━━━━━━━\n"
    "💡 *Tips*\n"
    "• Αν γράψεις μόνο ώρα, κρατάει την ίδια μέρα\n"
    "• Μπορείς να στείλεις και σκέτο μήνυμα, χωρίς /set\n"
    "• Παράδειγμα: `Τετάρτη 18:30`\n"
)

def main_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("▶️ Ενεργοποίηση", callback_data="action:start"),
         InlineKeyboardButton("⏸️ Παύση", callback_data="action:stop")],
        [InlineKeyboardButton("🛠️ Ρύθμιση", callback_data="action:set"),
         InlineKeyboardButton("📅 Tρέχουσα Ρύθμιση", callback_data="action:when")],
        [InlineKeyboardButton("ℹ️ Help", callback_data="action:help")],
    ])

def parse_day_time(text: str) -> tuple[int, int, int] | None:
    t = text.strip().lower()

    m = re.search(r"(\d{1,2})[:.](\d{2})", t)
    if not m:
        return None
    hour = int(m.group(1))
    minute = int(m.group(2))
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None

    dow = None
    for k, v in DAY_MAP.items():
        if k in t:
            dow = v
            break

    return (dow if dow is not None else -1, hour, minute)


async def init_db() -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS chats (
                chat_id INTEGER PRIMARY KEY,
                enabled INTEGER NOT NULL DEFAULT 1,
                dow INTEGER NOT NULL DEFAULT 0,      -- 0=Mon ... 6=Sun
                hour INTEGER NOT NULL DEFAULT 8,
                minute INTEGER NOT NULL DEFAULT 0
            )
            """
        )

        # Migration για παλιές βάσεις που είχαν μόνο chat_id/enabled
        cols = {row[1] async for row in await db.execute("PRAGMA table_info(chats)")}
        if "dow" not in cols:
            await db.execute("ALTER TABLE chats ADD COLUMN dow INTEGER NOT NULL DEFAULT 0")
        if "hour" not in cols:
            await db.execute("ALTER TABLE chats ADD COLUMN hour INTEGER NOT NULL DEFAULT 8")
        if "minute" not in cols:
            await db.execute("ALTER TABLE chats ADD COLUMN minute INTEGER NOT NULL DEFAULT 0")

        await db.commit()


async def set_enabled(chat_id: int, enabled: bool) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            INSERT INTO chats (chat_id, enabled)
            VALUES (?, ?)
            ON CONFLICT(chat_id) DO UPDATE SET enabled=excluded.enabled
            """,
            (chat_id, 1 if enabled else 0),
        )
        await db.commit()


async def get_enabled_chat_ids() -> list[int]:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT chat_id FROM chats WHERE enabled=1")
        rows = await cur.fetchall()
        return [r[0] for r in rows]

async def get_counts() -> tuple[int, int]:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT COUNT(*) FROM chats WHERE enabled=1")
        enabled_count = (await cur.fetchone())[0]

        cur = await db.execute("SELECT COUNT(*) FROM chats")
        total_count = (await cur.fetchone())[0]

        return enabled_count, total_count

async def set_schedule(chat_id: int, dow: int, hour: int, minute: int) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            INSERT INTO chats (chat_id, enabled, dow, hour, minute)
            VALUES (?, 1, ?, ?, ?)
            ON CONFLICT(chat_id) DO UPDATE SET
                dow=excluded.dow,
                hour=excluded.hour,
                minute=excluded.minute
            """,
            (chat_id, dow, hour, minute),
        )
        await db.commit()


async def get_schedule(chat_id: int) -> tuple[int, int, int] | None:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT dow, hour, minute FROM chats WHERE chat_id=?", (chat_id,))
        row = await cur.fetchone()
        if not row:
            return None
        return int(row[0]), int(row[1]), int(row[2])


async def get_due_chat_ids(dow: int, hour: int, minute: int) -> list[int]:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT chat_id FROM chats WHERE enabled=1 AND dow=? AND hour=? AND minute=?",
            (dow, hour, minute),
        )
        rows = await cur.fetchall()
        return [r[0] for r in rows]

async def schedule_tick(context: ContextTypes.DEFAULT_TYPE) -> None:
    now = datetime.now(TZ)
    dow = now.weekday()     # 0=Mon..6=Sun
    hour = now.hour
    minute = now.minute

    chat_ids = await get_due_chat_ids(dow, hour, minute)
    if not chat_ids:
        return

    text = "☀️ Καλημέρα! Αυτό είναι το προγραμματισμένο μήνυμά σου."
    logger.info("SCHEDULE send due=%d day=%s time=%02d:%02d", len(chat_ids), DAY_NAMES[dow], hour, minute)

    for chat_id in chat_ids:
        try:
            await context.bot.send_message(chat_id=chat_id, text=text)
            await asyncio.sleep(0.05)
        except RetryAfter as e:
            await asyncio.sleep(e.retry_after)
        except Forbidden:
            await set_enabled(chat_id, False)
        except Exception:
            logger.exception("Failed sending to chat_id=%s", chat_id)


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(HELP_TEXT, parse_mode="Markdown")


async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.info("USER start chat_id=%s user_id=%s", update.effective_chat.id, update.effective_user.id)

    chat_id = update.effective_chat.id

    # Αν δεν έχει πρόγραμμα ακόμα, βάλε default: Δευτέρα 08:00
    sched = await get_schedule(chat_id)
    if not sched:
        await set_schedule(chat_id, 0, 8, 0)
        sched = (0, 8, 0)

    # Ενεργοποίησε (χωρίς να αλλάξεις την ώρα/μέρα)
    await set_enabled(chat_id, True)

    dow, hour, minute = sched

    await update.message.reply_text(
        "✅ Ενεργοποιήθηκε!\n\n"
        f"🗓️ Τρέχουσα ρύθμιση:\n"
        f"{DAY_NAMES[dow]} στις {hour:02d}:{minute:02d}\n\n"
        "🔧 Για αλλαγή ώρας, απλά αντέγραψε ένα από τα παρακάτω "
        "ή στείλε το δικό σου με την ίδια λογική:\n\n"
        "`/set Κυριακή 23:58`\n"
        "`Δευτέρα 08:00`\n"
        "`/set 21:15`\n\n"
        "ℹ️ Tips:\n"
        "• Αν γράψεις μόνο ώρα, κρατάει την ίδια μέρα\n"
        "• Μπορείς να δεις τη ρύθμισή σου με /when\n"
        "• Οδηγίες: /help",
        parse_mode="Markdown",
        reply_markup=main_menu_keyboard(),  # (αν έχεις βάλει τα κουμπιά)
    )

async def set_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    raw = " ".join(context.args).strip()

    if not raw:
        await update.message.reply_text(
                "🕒 Ρύθμιση ώρας\n\n"
                "Παραδείγματα (tap για copy):\n"
                "`/set Κυριακή 23:58`\n"
                "`Δευτέρα 08:00`\n"
                "`/set 21:15`\n\n"
                "💡 Tip: Αν γράψεις μόνο ώρα, κρατάει την ίδια μέρα.",
                parse_mode="Markdown",
        )
        return

    parsed = parse_day_time(raw)
    if not parsed:
        await update.message.reply_text("❌ Δεν κατάλαβα. Δοκίμασε π.χ. /set Τετάρτη 18:30")
        return

    dow, hour, minute = parsed

    # αν δεν έδωσε μέρα, κράτα την παλιά (ή default Δευτέρα)
    current = await get_schedule(chat_id)
    if dow == -1:
        dow = current[0] if current else 0

    await set_schedule(chat_id, dow, hour, minute)
    logger.info("USER set chat_id=%s day=%s time=%02d:%02d", chat_id, DAY_NAMES[dow], hour, minute)
    await set_enabled(chat_id, True)

    await update.message.reply_text(f"✅ ΟΚ! {DAY_NAMES[dow]} στις {hour:02d}:{minute:02d}")


async def when_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    sched = await get_schedule(chat_id)
    if not sched:
        await update.message.reply_text("Δεν έχεις ρύθμιση ακόμα. Στείλε /start ή /set Δευτέρα 08:00")
        return
    dow, hour, minute = sched
    await update.message.reply_text(f"🗓️ Ρύθμιση: {DAY_NAMES[dow]} στις {hour:02d}:{minute:02d}\n\n Πάτα Help για επιστροφή στο μενού.")


async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    txt = (update.message.text or "").strip()
    if not txt or txt.startswith("/"):
        return

    parsed = parse_day_time(txt)
    if not parsed:
        return

    dow, hour, minute = parsed
    chat_id = update.effective_chat.id
    current = await get_schedule(chat_id)
    if dow == -1:
        dow = current[0] if current else 0

    await set_schedule(chat_id, dow, hour, minute)
    logger.info("USER set_text chat_id=%s day=%s time=%02d:%02d", chat_id, DAY_NAMES[dow], hour, minute)
    await set_enabled(chat_id, True)
    await update.message.reply_text(f"✅ Ρυθμίστηκε: {DAY_NAMES[dow]} στις {hour:02d}:{minute:02d}")


async def stop_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.info("USER stop chat_id=%s user_id=%s", update.effective_chat.id, update.effective_user.id)
    chat_id = update.effective_chat.id
    await set_enabled(chat_id, False)
    await update.message.reply_text("⏸️ Έγινε παύση. Στείλε /start για να ξαναξεκινήσει.")

async def sendnow_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    admin_chat_id = update.effective_chat.id
    if admin_chat_id not in ADMIN_CHAT_IDS:
        await update.message.reply_text("⛔ Δεν έχεις δικαίωμα για αυτή την εντολή.")
        return

    # Παίρνουμε το custom μήνυμα μετά το /sendnow
    custom_text = " ".join(context.args).strip()
    if not custom_text:
        await update.message.reply_text("Χρήση: /sendnow το μήνυμα εδώ")
        return

    chat_ids = await get_enabled_chat_ids()
    if not chat_ids:
        await update.message.reply_text("❌ Δεν υπάρχουν ενεργοί χρήστες.")
        return

    sent = 0
    failed = 0

    for target_chat_id in chat_ids:
        try:
            await context.bot.send_message(chat_id=target_chat_id, text=custom_text)
            sent += 1
            await asyncio.sleep(0.05)
        except RetryAfter as e:
            await asyncio.sleep(e.retry_after)
            # retry μία φορά μετά το sleep
            try:
                await context.bot.send_message(chat_id=target_chat_id, text=custom_text)
                sent += 1
            except Exception:
                failed += 1
        except Forbidden:
            await set_enabled(target_chat_id, False)
            failed += 1
        except Exception:
            logger.exception("Failed sending to chat_id=%s", target_chat_id)
            failed += 1

    await update.message.reply_text(f"✅ Στάλθηκε σε {sent} | ❌ Απέτυχε σε {failed}")


async def stats_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    if chat_id not in ADMIN_CHAT_IDS:
        await update.message.reply_text("⛔ Δεν έχεις δικαίωμα για αυτή την εντολή.")
        return

    enabled_count, total_count = await get_counts()
    await update.message.reply_text(
        f"📊 Stats:\n✅ Ενεργοί: {enabled_count}\n👥 Σύνολο: {total_count}"
    )


async def on_startup(app: Application) -> None:
    await init_db()

    # Scheduler: τρέχει κάθε 60 δευτερόλεπτα
    app.job_queue.run_repeating(
        schedule_tick,
        interval=60,
        first=1,
        name="schedule_tick",
    )

async def menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()  # σημαντικό!

    chat_id = query.message.chat_id
    data = query.data
    if data in {"action:start", "action:stop", "action:set"}:
        logger.info("USER button chat_id=%s action=%s", chat_id, data)


    if data == "action:start":
        sched = await get_schedule(chat_id)
        if not sched:
            await set_schedule(chat_id, 0, 8, 0)
            sched = (0, 8, 0)
        await set_enabled(chat_id, True)
        dow, hour, minute = sched
        await query.edit_message_text(
            f"✅ Ενεργοποιήθηκε!\n🗓️ {DAY_NAMES[dow]} στις {hour:02d}:{minute:02d}",
            reply_markup=main_menu_keyboard(),
        )

    elif data == "action:stop":
        await set_enabled(chat_id, False)
        await query.edit_message_text(
            "⏸️ Έγινε παύση.",
            reply_markup=main_menu_keyboard(),
        )
    elif data == "action:set":
        keyboard = [
            [
                InlineKeyboardButton("Δευτέρα", callback_data="setday:0"),
                InlineKeyboardButton("Τρίτη", callback_data="setday:1"),
            ],
            [
                InlineKeyboardButton("Τετάρτη", callback_data="setday:2"),
                InlineKeyboardButton("Πέμπτη", callback_data="setday:3"),
            ],
            [
                InlineKeyboardButton("Παρασκευή", callback_data="setday:4"),
                InlineKeyboardButton("Σάββατο", callback_data="setday:5"),
            ],
            [
                InlineKeyboardButton("Κυριακή", callback_data="setday:6"),
            ],
            [
                InlineKeyboardButton("⬅️ Πίσω", callback_data="action:help"),
            ],
        ]
        await query.edit_message_text(
            "📅 Διάλεξε μέρα για το μήνυμα:",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )




    elif data == "action:when":
        sched = await get_schedule(chat_id)
        if not sched:
            await query.edit_message_text(
                "Δεν έχεις ρύθμιση ακόμα. Πάτα ▶️ Ενεργοποίηση.",
                reply_markup=main_menu_keyboard(),
            )
            return
        dow, hour, minute = sched
        await query.edit_message_text(
            f"📅 Ρύθμιση:\n{DAY_NAMES[dow]} στις {hour:02d}:{minute:02d}\n\n ━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
            reply_markup=main_menu_keyboard(),
        )

    elif data == "action:help":
        await query.edit_message_text(
            HELP_TEXT,
            parse_mode="Markdown",
            reply_markup=main_menu_keyboard(),
    )

async def setday_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()

    chat_id = query.message.chat_id
    dow = int(query.data.split(":")[1])

    # κράτα την τρέχουσα ώρα αν υπάρχει, αλλιώς default 08:00
    current = await get_schedule(chat_id)
    hour, minute = (8, 0) if not current else (current[1], current[2])

    await set_schedule(chat_id, dow, hour, minute)
    logger.info("USER set_day chat_id=%s day=%s", chat_id, DAY_NAMES[dow])
    await set_enabled(chat_id, True)

    await query.edit_message_text(
        f"✅ Ορίστηκε μέρα: {DAY_NAMES[dow]}\n\n"
        "Τώρα στείλε ώρα (copy/paste):\n"
        "`21:15`\n\n"
        "ή γράψε π.χ. `Κυριακή 23:58`",
        parse_mode="Markdown",
        reply_markup=main_menu_keyboard(),
    )


def main() -> None:
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        raise SystemExit("❌ Λείπει το TELEGRAM_BOT_TOKEN (θα το βάλουμε σε .env)")

    app = Application.builder().token(token).post_init(on_startup).build()
    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("stop", stop_cmd))
    app.add_handler(CommandHandler("sendnow", sendnow_cmd))
    app.add_handler(CommandHandler("stats", stats_cmd))
    app.add_handler(CommandHandler("set", set_cmd))
    app.add_handler(CommandHandler("when", when_cmd))
    app.add_handler(CommandHandler("logs", logs_cmd))       # /logs 80
    app.add_handler(CommandHandler("errors", errors_cmd))   # /errors 120
    app.add_handler(CommandHandler("logsearch", logsearch_cmd))  # /logsearch set 500
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CallbackQueryHandler(setday_callback, pattern=r"^setday:\d$"))
    app.add_handler(CallbackQueryHandler(menu_callback, pattern=r"^action:"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
