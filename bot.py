import io, zipfile, json, os, datetime, asyncio, time, logging
from collections import defaultdict
from PIL import Image
import qrcode

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler,
    CallbackQueryHandler, filters, ContextTypes
)

# ===== LOGGING =====
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("bot.log", encoding="utf-8"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

TOKEN          = os.getenv("TOKEN")
ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "UZ_CS")  # Limit uchun admin
_admin_env = os.getenv("ADMIN_ID")
if not TOKEN or not _admin_env:
    raise RuntimeError("TOKEN yoki ADMIN_ID muhit o'zgaruvchisi o'rnatilmagan!")
ADMIN_ID = int(_admin_env)

DB_FILE  = "db.json"
sessions = {}   # chat_id -> session dict

# ===== RATE LIMITING =====
RATE_LIMIT = defaultdict(lambda: {"count": 0, "reset": 0})

def check_rate_limit(uid, max_requests=5, window=60):
    """Daqiqada max_requests martadan ko'p so'rov yubormasligi uchun."""
    now = time.time()
    rl  = RATE_LIMIT[uid]
    if now > rl["reset"]:
        RATE_LIMIT[uid] = {"count": 0, "reset": now + window}
    RATE_LIMIT[uid]["count"] += 1
    return RATE_LIMIT[uid]["count"] <= max_requests

# ===== DB =====
def load_db():
    try:
        with open(DB_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {
            "allowed": [], "pending": [], "blocked": [],
            "locked": False,
            "stats": {"total_qr": 0},
            "user_info": {},
            "errors": [],
            "presets": {}
        }

def save_db():
    with open(DB_FILE, "w", encoding="utf-8") as f:
        json.dump(db, f, ensure_ascii=False, indent=2)

def cleanup_templates():
    """Startup da eski template fayllarini diskdan tozalash."""
    import glob
    for f in glob.glob("template_*.png"):
        try:
            os.remove(f)
            logger.info(f"Eski template o'chirildi: {f}")
        except Exception:
            pass

db = load_db()
# Eski DB lar uchun migrate
for _k, _v in [
    ("stats",   {"total_qr": 0}),
    ("user_info", {}),
    ("errors",  []),
    ("presets", {}),
    ("history", {})
]:
    if _k not in db:
        db[_k] = _v
cleanup_templates()

# ===== ERROR LOGGING =====
def log_error(uid, error_msg, context_info=""):
    entry = {
        "timestamp":    datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "user_id":      uid,
        "error":        str(error_msg),
        "context":      str(context_info)
    }
    db["errors"].append(entry)
    # So'nggi 200 xatoni saqlash
    if len(db["errors"]) > 200:
        db["errors"] = db["errors"][-200:]
    save_db()
    logger.error(f"UID={uid} | {error_msg} | ctx={context_info}")

# ===== HELPERS =====
def save_user_info(user):
    uid = str(user.id)
    old = db["user_info"].get(uid, {})
    db["user_info"][uid] = {
        "name":        user.full_name or "—",
        "username":    f"@{user.username}" if user.username else "—",
        "qr_count":   old.get("qr_count", 0),
        "limit":       old.get("limit", 50),
        "last_active": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
        "joined":      old.get("joined", datetime.datetime.now().strftime("%Y-%m-%d")),
    }
    save_db()

def get_display(uid):
    info = db["user_info"].get(str(uid), {})
    return "{} ({})".format(
        info.get("name", "Noma'lum"),
        info.get("username", "—")
    )

def inc_qr(uid, n):
    s = str(uid)
    db["user_info"].setdefault(s, {
        "name": "—", "username": "—", "qr_count": 0, "limit": 50,
        "last_active": "—", "joined": "—"
    })
    db["user_info"][s]["qr_count"] = db["user_info"][s].get("qr_count", 0) + n
    db["user_info"][s]["limit"]    = max(0, db["user_info"][s].get("limit", 50) - n)
    db["stats"]["total_qr"]        = db["stats"].get("total_qr", 0) + n
    save_db()

def user_status(uid):
    if uid in db["allowed"]: return "✅ Ruxsat berilgan"
    if uid in db["blocked"]: return "🚫 Bloklangan"
    if uid in db["pending"]: return "⏳ Kutmoqda"
    return "❓ Noma'lum"

# ===== LIMIT TEKSHIRISH (YANGI #2) =====
def check_limit(uid, needed: int):
    """Foydalanuvchi limitini tekshiradi."""
    if uid == ADMIN_ID:
        return True, None
    info = db["user_info"].get(str(uid), {})
    remaining = info.get("limit", 50)
    if remaining <= 0:
        return False, "❌ Limitingiz tugadi. Admin bilan bog'laning: /limit"
    if needed > remaining:
        return False, (
            f"❌ Siz <b>{needed}</b> ta QR talab qildingiz,\n"
            f"ammo limitingiz faqat <b>{remaining}</b> ta.\n"
            "Admin bilan bog'laning: /limit"
        )
    return True, None

# ===== HISTORY (YANGI #4) =====
def save_history(uid, count, color, fmt, base, start_r, end_r):
    uid_str = str(uid)
    db.setdefault("history", {}).setdefault(uid_str, [])
    db["history"][uid_str].append({
        "date":    datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
        "count":   count,
        "color":   color,
        "format":  fmt,
        "base":    base,
        "range":   f"{start_r:02d}–{end_r:02d}"
    })
    # Har user uchun so'nggi 20 tarix
    db["history"][uid_str] = db["history"][uid_str][-20:]
    save_db()

# ===== ACTIVITY TRACKING (YANGI #4) =====
def track_activity(uid, action: str):
    s = str(uid)
    db["user_info"].setdefault(s, {})
    db["user_info"][s]["last_action"]  = action
    db["user_info"][s]["last_active"]  = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    save_db()
    logger.info(f"UID={uid} | action={action}")

# ===== ACCESS =====
def check(uid):
    if db["locked"] and uid != ADMIN_ID:
        return False, "🔒 Bot hozir yopiq"
    if uid in db["blocked"]:
        return False, "🚫 Siz bloklangansiz"
    if uid != ADMIN_ID and uid not in db["allowed"]:
        if uid not in db["pending"]:
            db["pending"].append(uid)
            save_db()
        return False, "⏳ So'rovingiz adminga yuborildi. Kuting..."
    return True, None

# ===== COLORS & KEYBOARDS =====
COLORS = {
    "black":   ("⚫ Qora",      "black",    "white"),
    "blue":    ("🔵 Ko'k",     "#1565c0",  "white"),
    "red":     ("🔴 Qizil",    "#c62828",  "white"),
    "green":   ("🟢 Yashil",   "#2e7d32",  "white"),
    "purple":  ("🟣 Binafsha", "#6a1b9a",  "white"),
    "orange":  ("🟠 To'q sariq","#e65100", "white"),
    "white":   ("⬜ Oq",       "white",    "black"),
}

def color_kb():
    btns = [InlineKeyboardButton(v[0], callback_data=f"color_{k}")
            for k, v in COLORS.items()]
    rows = [btns[:4], btns[4:]]
    return InlineKeyboardMarkup(rows)

def format_kb():
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("📁 ZIP",         callback_data="fmt_zip"),
        InlineKeyboardButton("📄 Alohida PNG", callback_data="fmt_single"),
    ]])

def range_kb():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("01–10",  callback_data="rng_1_10"),
            InlineKeyboardButton("01–20",  callback_data="rng_1_20"),
            InlineKeyboardButton("01–50",  callback_data="rng_1_50"),
        ],
        [
            InlineKeyboardButton("01–100", callback_data="rng_1_100"),
            InlineKeyboardButton("✏️ Boshqa...", callback_data="rng_custom"),
        ]
    ])

def admin_main_kb():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📥 Pending",        callback_data="adm_pending"),
            InlineKeyboardButton("📋 Ruxsatlar",      callback_data="adm_allowed"),
        ],
        [
            InlineKeyboardButton("🚫 Bloklangan",     callback_data="adm_blocked"),
            InlineKeyboardButton("📊 Statistika",     callback_data="adm_stats"),
        ],
        [
            InlineKeyboardButton("📢 Broadcast",      callback_data="adm_broadcast"),
            InlineKeyboardButton("💌 Xabar yuborish", callback_data="adm_msg"),
        ],
        [
            InlineKeyboardButton("✅✅ Barchasini qabul", callback_data="adm_accept_all"),
            InlineKeyboardButton("❌❌ Barchasini rad",   callback_data="adm_reject_all"),
        ],
        [
            InlineKeyboardButton("🔢 Limit berish",   callback_data="adm_setlimit"),
            InlineKeyboardButton("📜 Xatolar",        callback_data="adm_errors"),
        ],
        [
            InlineKeyboardButton("📈 Faollik",        callback_data="adm_activity"),
            InlineKeyboardButton("🗂 Tarixlar",        callback_data="adm_history"),
        ],
        [
            InlineKeyboardButton("🔒 Yopish",         callback_data="adm_lock"),
            InlineKeyboardButton("🔓 Ochish",         callback_data="adm_unlock"),
        ],
        [
            InlineKeyboardButton("💾 Backup DB",      callback_data="adm_backup"),
            InlineKeyboardButton("🧹 Xatolarni tozala", callback_data="adm_clear_errors"),
        ],
    ])

# ===== ADMIN NOTIFICATION HELPER =====
async def _notify_admin_pending(context, user):
    """Adminga yangi pending foydalanuvchi haqida inline tugmali xabar."""
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Qabul",   callback_data=f"ok_{user.id}"),
        InlineKeyboardButton("❌ Rad",     callback_data=f"no_{user.id}"),
        InlineKeyboardButton("👁 Ko'rish", callback_data=f"view_{user.id}"),
    ]])
    try:
        await context.bot.send_message(
            ADMIN_ID,
            f"🔔 <b>Yangi foydalanuvchi so'rovi!</b>\n\n"
            f"👤 {user.full_name}\n"
            f"🔗 {'@' + user.username if user.username else '—'}\n"
            f"🆔 <code>{user.id}</code>",
            parse_mode="HTML",
            reply_markup=kb
        )
    except Exception as e:
        logger.warning(f"Admin bildirishnomasi yuborilmadi: {e}")

# ===== /start =====
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid     = update.effective_user.id
    chat_id = update.message.chat_id
    save_user_info(update.effective_user)
    track_activity(uid, "start")

    was_pending = uid in db["pending"]
    ok, msg = check(uid)
    if not ok:
        await update.message.reply_text(msg)
        if not was_pending and uid in db["pending"]:
            await _notify_admin_pending(context, update.effective_user)
        return

    session  = sessions.get(chat_id, {})
    has_last = bool(session.get("last_template") and
                    os.path.exists(session.get("last_template", "_")))

    info      = db["user_info"].get(str(uid), {})
    limit_txt = f"📦 Limitingiz: <b>{info.get('limit', 50)}</b> ta QR\n" if uid != ADMIN_ID else ""
    qr_count  = info.get("qr_count", 0)

    rows = []
    if has_last:
        rows.append([InlineKeyboardButton("🔄 Oxirgi rasmni qayta ishlatish", callback_data="reuse")])
    rows.append([InlineKeyboardButton("📷 Yangi rasm yuborish", callback_data="new_photo")])
    rows.append([InlineKeyboardButton("📜 Mening tariximim",    callback_data="my_history")])

    await update.message.reply_text(
        f"👋 <b>QR Kod Generator</b>\n\n"
        f"{limit_txt}"
        f"🖼 Jami yaratilgan QR: <b>{qr_count}</b> ta\n\n"
        "📷 Shablon rasm yuboring yoki oxirgi rasmdan foydalaning:",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(rows)
    )

# ===== /admin =====
async def cmd_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    await update.message.reply_text("⚙️ <b>Admin Panel</b>", parse_mode="HTML",
                                    reply_markup=admin_main_kb())

# ===== /cancel =====
async def cmd_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    sessions.pop(update.message.chat_id, None)
    await update.message.reply_text("✅ Bekor qilindi. /start bosing.")

# ===== /limit =====
async def cmd_limit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid  = update.effective_user.id
    info = db["user_info"].get(str(uid), {})
    rem  = info.get("limit", 50)
    used = info.get("qr_count", 0)
    await update.message.reply_text(
        f"📦 <b>Limitingiz:</b>\n\n"
        f"✅ Ishlatilgan: <b>{used}</b> ta\n"
        f"🔢 Qolgan: <b>{rem}</b> ta\n\n"
        "Limit tugasa admin bilan bog'laning.",
        parse_mode="HTML"
    )

# ===== /history =====
async def cmd_history(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    ok, msg = check(uid)
    if not ok:
        return await update.message.reply_text(msg)
    await _show_history(update.message, uid)

async def _show_history(message, uid):
    uid_str = str(uid)
    hist    = db.get("history", {}).get(uid_str, [])
    if not hist:
        return await message.reply_text("📜 Hali hech narsa yaratilmagan.")
    lines = [f"📜 <b>So'nggi {len(hist)} ta amal:</b>\n"]
    for i, h in enumerate(reversed(hist), 1):
        fmt_icon = "📁" if h.get("format") == "zip" else "📄"
        color    = COLORS.get(h.get("color", "black"), ("⚫",))[0]
        lines.append(
            f"{i}. {h['date']} — <code>{h['base']}</code> "
            f"[{h['range']}] {color} {fmt_icon} — <b>{h['count']}</b> ta"
        )
    await message.reply_text("\n".join(lines), parse_mode="HTML")

# ===== QR GENERATOR (MUKAMMAL VA TEZKOR) =====
async def generate_qr(message, chat_id, uid, start_r: int, end_r: int):
    session = sessions.get(chat_id, {})
    count   = end_r - start_r + 1

    # ---- Limit tekshirish (YANGI #2) ----
    ok_limit, limit_msg = check_limit(uid, count)
    if not ok_limit:
        await message.reply_text(limit_msg, parse_mode="HTML")
        return

    try:
        # Shablonni bir marta yuklash — loop ichida emas!
        template_orig = Image.open(session["template"]).convert("RGBA")
        w, h = template_orig.size

        block_w = int(w * 0.62)
        x1      = (w - block_w) // 2
        y1      = int(h * 0.44)
        size    = int(block_w * 0.85)
        px_off  = x1 + (block_w - size) // 2
        py_off  = y1 + (block_w - size) // 2

        _, fill, back = COLORS.get(session.get("color", "black"), ("", "black", "white"))
        fmt  = session.get("format", "zip")
        base = session.get("base", "")

        # ---- QR rasmini tez yaratish (optimized) ----
        def make_qr_img(code: str) -> Image.Image:
            img = template_orig.copy()
            qr  = qrcode.QRCode(
                version=None,
                error_correction=qrcode.constants.ERROR_CORRECT_L,
                box_size=10,   # Yuqori sifat
                border=1
            )
            qr.add_data(code)
            qr.make(fit=True)
            qr_img = qr.make_image(
                fill_color=fill, back_color=back
            ).convert("RGBA")
            qr_img = qr_img.resize((size, size), Image.LANCZOS)
            img.paste(qr_img, (px_off, py_off), qr_img)
            return img

        # ---- Formatga ko'ra yuborish ----
        if fmt == "zip":
            zip_buf = io.BytesIO()
            with zipfile.ZipFile(zip_buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
                for i in range(start_r, end_r + 1):
                    code = base + str(i).zfill(2)
                    img  = make_qr_img(code)
                    buf  = io.BytesIO()
                    img.save(buf, "PNG", optimize=True)
                    zf.writestr(f"{code}.png", buf.getvalue())
            zip_buf.seek(0)
            color_label = COLORS.get(session.get("color","black"), ("⚫ Qora",))[0]
            await message.reply_document(
                zip_buf,
                filename=f"qr_{base}_{start_r:02d}-{end_r:02d}.zip",
                caption=(
                    f"✅ <b>{count}</b> ta QR kod tayyor!\n"
                    f"🔢 Kod: <code>{base}</code> [{start_r:02d}–{end_r:02d}]\n"
                    f"🎨 Rang: {color_label}"
                ),
                parse_mode="HTML"
            )
        else:
            for i in range(start_r, end_r + 1):
                code = base + str(i).zfill(2)
                img  = make_qr_img(code)
                buf  = io.BytesIO()
                img.save(buf, "PNG", optimize=True)
                buf.seek(0)
                await message.reply_document(buf, filename=f"{code}.png")
                await asyncio.sleep(0.35)  # Telegram flood limit uchun
            await message.reply_text(
                f"✅ <b>{count}</b> ta PNG yuborildi!\n"
                f"🔢 Kod: <code>{base}</code> [{start_r:02d}–{end_r:02d}]",
                parse_mode="HTML"
            )

        inc_qr(uid, count)
        save_history(uid, count,
                     session.get("color","black"),
                     fmt, base, start_r, end_r)
        track_activity(uid, f"generated_{count}_qr")
        sessions.pop(chat_id, None)
        logger.info(f"UID={uid} | {count} ta QR yaratildi | base={base} | {start_r}-{end_r}")

        # ⚠️ Limit ogohlantirish
        if uid != ADMIN_ID:
            remaining = db["user_info"].get(str(uid), {}).get("limit", 50)
            if 0 < remaining <= 5:
                await message.reply_text(
                    f"⚠️ <b>Diqqat!</b> Limitingiz faqat <b>{remaining}</b> taga yetdi!\n"
                    f"Yangi limit olish uchun admin bilan bog'laning:\n"
                    f"👤 @{ADMIN_USERNAME}",
                    parse_mode="HTML"
                )

    except FileNotFoundError:
        log_error(uid, "template_not_found", session.get("template"))
        await message.reply_text(
            "❌ Shablon rasm topilmadi.\n"
            "Iltimos yangi rasm yuboring: /start"
        )
    except MemoryError:
        log_error(uid, "memory_error", f"count={count}")
        await message.reply_text(
            "❌ Xotira yetishmadi. Kichikroq diapazon kiriting."
        )
    except Exception as e:
        log_error(uid, str(e), f"range={start_r}-{end_r}")
        await message.reply_text(
            "❌ QR yaratishda xato yuz berdi.\n"
            "Admin xabardor qilindi. Keyinroq urinib ko'ring."
        )

# ===== PHOTO HANDLER =====
async def photo_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid     = update.effective_user.id
    chat_id = update.message.chat_id
    save_user_info(update.effective_user)

    was_pending = uid in db["pending"]
    ok, msg = check(uid)
    if not ok:
        await update.message.reply_text(msg)
        if not was_pending and uid in db["pending"]:
            await _notify_admin_pending(context, update.effective_user)
        return

    if not check_rate_limit(uid):
        return await update.message.reply_text(
            "⏳ Juda ko'p so'rov. 1 daqiqa kuting."
        )

    track_activity(uid, "photo_uploaded")

    try:
        file = await update.message.photo[-1].get_file()
        path = f"template_{chat_id}.png"
        await file.download_to_drive(path)

        sessions[chat_id] = {
            **sessions.get(chat_id, {}),
            "template":      path,
            "last_template": path,
            "step":          "color",
            "color":         "black",
            "format":        "zip",
        }
        await update.message.reply_text(
            "✅ Rasm qabul qilindi!\n\n🎨 QR rang tanlang:",
            reply_markup=color_kb()
        )
    except Exception as e:
        log_error(uid, f"photo_download_error: {e}", "photo_handler")
        await update.message.reply_text("❌ Rasmni yuklab bo'lmadi. Qayta yuboring.")

# ===== TEXT HANDLER =====
async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.message.chat_id
    uid     = update.effective_user.id
    txt     = update.message.text.strip()
    save_user_info(update.effective_user)

    session = sessions.get(chat_id, {})
    step    = session.get("step")

    # --- Admin: broadcast ---
    if uid == ADMIN_ID and step == "broadcast":
        sessions.pop(chat_id, None)
        sent = 0
        for target in db["allowed"]:
            try:
                await context.bot.send_message(
                    target,
                    f"📢 <b>Admin xabari:</b>\n\n{txt}",
                    parse_mode="HTML"
                )
                sent += 1
            except Exception:
                pass
        await update.message.reply_text(
            f"✅ {sent} ta foydalanuvchiga yuborildi.",
            reply_markup=admin_main_kb()
        )
        return

    # --- Admin: single user message ---
    if uid == ADMIN_ID and step == "msg_uid":
        try:
            target_uid = int(txt)
            session["msg_target"] = target_uid
            session["step"]       = "msg_text"
            await update.message.reply_text(
                f"✏️ {get_display(target_uid)} ga yuboriladigan xabarni yozing:\n\n/cancel — bekor qilish"
            )
        except ValueError:
            await update.message.reply_text("❌ Noto'g'ri ID. Raqam kiriting:")
        return

    if uid == ADMIN_ID and step == "msg_text":
        target_uid = session.get("msg_target")
        sessions.pop(chat_id, None)
        try:
            await context.bot.send_message(
                target_uid,
                f"💌 <b>Admin xabari:</b>\n\n{txt}",
                parse_mode="HTML"
            )
            await update.message.reply_text(
                f"✅ Xabar yuborildi: {get_display(target_uid)}",
                reply_markup=admin_main_kb()
            )
        except Exception as e:
            await update.message.reply_text(f"❌ Yuborib bo'lmadi: {e}")
        return

    # --- Admin: limit berish ---
    if uid == ADMIN_ID and step == "set_limit_uid":
        try:
            target_uid = int(txt)
            session["limit_target"] = target_uid
            session["step"]         = "set_limit_val"
            await update.message.reply_text(
                f"🔢 {get_display(target_uid)} uchun yangi limitni kiriting (masalan: 100):\n\n/cancel"
            )
        except ValueError:
            await update.message.reply_text("❌ Noto'g'ri ID.")
        return

    if uid == ADMIN_ID and step == "set_limit_val":
        try:
            new_limit  = int(txt)
            target_uid = session["limit_target"]
            s = str(target_uid)
            db["user_info"].setdefault(s, {"name":"—","username":"—","qr_count":0,"limit":50})
            db["user_info"][s]["limit"] = new_limit
            save_db()
            sessions.pop(chat_id, None)
            display = get_display(target_uid)
            await update.message.reply_text(
                f"✅ {display} uchun limit {new_limit} ga o'zgartirildi.",
                reply_markup=admin_main_kb()
            )
            try:
                await context.bot.send_message(
                    target_uid,
                    f"🎁 Limitingiz yangilandi: <b>{new_limit}</b> ta QR",
                    parse_mode="HTML"
                )
            except Exception:
                pass
        except ValueError:
            await update.message.reply_text("❌ Noto'g'ri qiymat. Raqam kiriting:")
        return

    # ===== User flows =====
    ok, msg = check(uid)
    if not ok:
        return await update.message.reply_text(msg)

    if not session:
        return await update.message.reply_text("⚠️ /start bosing")

    if step == "code":
        if len(txt) < 2 or len(txt) > 30:
            return await update.message.reply_text(
                "❌ Kod 2–30 belgidan iborat bo'lishi kerak."
            )
        session["base"] = txt
        session["step"] = "range_select"
        track_activity(uid, f"code_entered:{txt}")
        await update.message.reply_text(
            f"🔢 Asosiy kod: <code>{txt}</code>\n\n"
            f"📊 Diapazonni tanlang yoki son yozing (masalan: <code>00 50</code>):",
            parse_mode="HTML",
            reply_markup=range_kb()
        )
        return

    if step == "range":
        try:
            parts = txt.split()
            if len(parts) != 2:
                raise ValueError("2 ta son kerak")
            s, e = int(parts[0]), int(parts[1])
            if s < 0 or e > 999 or s > e:
                raise ValueError("0–999 oralig'ida va s<=e bo'lsin")
            count = e - s + 1
            if count > 500:
                return await update.message.reply_text(
                    "❌ Bir vaqtda maksimal <b>500</b> ta QR yaratsa bo'ladi.",
                    parse_mode="HTML"
                )
            session["step"] = "generating"
            color_label = COLORS.get(session.get("color","black"), ("⚫ Qora",))[0]
            fmt_label   = "📁 ZIP" if session.get("format","zip") == "zip" else "📄 Alohida PNG"
            await update.message.reply_text(
                f"⏳ <b>{s:02d}–{e:02d}</b> ({count} ta) yaratilmoqda...\n"
                f"🎨 Rang: {color_label} | {fmt_label}",
                parse_mode="HTML"
            )
            await generate_qr(update.message, chat_id, uid, s, e)
        except ValueError as ve:
            await update.message.reply_text(
                f"❌ Format noto'g'ri: {ve}\nMasalan: <code>01 50</code>",
                parse_mode="HTML"
            )
        return

# ===== CALLBACK ROUTER =====
async def callback_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query   = update.callback_query
    await query.answer()
    data    = query.data
    uid     = query.from_user.id
    chat_id = query.message.chat_id

    # ===== USER CALLBACKS =====
    if data == "new_photo":
        await query.message.reply_text("📷 Rasm yuboring:")
        return

    if data == "my_history":
        ok, msg = check(uid)
        if not ok:
            return await query.message.reply_text(msg)
        await _show_history(query.message, uid)
        return

    if data == "reuse":
        ok, msg = check(uid)
        if not ok:
            return await query.message.reply_text(msg)
        session = sessions.get(chat_id, {})
        last    = session.get("last_template")
        if last and os.path.exists(last):
            sessions[chat_id] = {
                **session,
                "template": last,
                "step":     "color",
                "color":    "black",
                "format":   "zip",
            }
            await query.message.reply_text("🎨 QR rang tanlang:", reply_markup=color_kb())
        else:
            await query.message.reply_text("❌ Oldingi shablon topilmadi. Yangi rasm yuboring.")
        return

    if data.startswith("color_"):
        color = data[6:]
        sessions.setdefault(chat_id, {})
        sessions[chat_id]["color"] = color
        label = COLORS.get(color, ("⚫ Qora",))[0]
        await query.message.reply_text(
            f"✅ Rang: <b>{label}</b>\n\n📁 Fayl formatini tanlang:",
            parse_mode="HTML",
            reply_markup=format_kb()
        )
        return

    if data.startswith("fmt_"):
        fmt = data[4:]
        sessions.setdefault(chat_id, {})
        sessions[chat_id]["format"] = fmt
        sessions[chat_id]["step"]   = "code"
        await query.message.reply_text(
            "🔢 Asosiy kodni yozing (masalan: <code>106243</code>):",
            parse_mode="HTML"
        )
        return

    if data.startswith("rng_"):
        part = data[4:]
        if part == "custom":
            sessions.setdefault(chat_id, {})["step"] = "range"
            await query.message.reply_text(
                "✏️ Diapazon kiriting (masalan: <code>01 50</code>):",
                parse_mode="HTML"
            )
            return
        try:
            s_str, e_str = part.split("_")
            sr, er = int(s_str), int(e_str)
            count  = er - sr + 1

            # Limit tekshirish
            ok_l, lmsg = check_limit(uid, count)
            if not ok_l:
                await query.message.reply_text(lmsg, parse_mode="HTML")
                return

            sessions.setdefault(chat_id, {})["step"] = "generating"
            color_label = COLORS.get(sessions[chat_id].get("color","black"), ("⚫ Qora",))[0]
            fmt_label   = "📁 ZIP" if sessions[chat_id].get("format","zip") == "zip" else "📄 Alohida PNG"
            await query.message.reply_text(
                f"⏳ <b>{sr:02d}–{er:02d}</b> ({count} ta) yaratilmoqda...\n"
                f"🎨 Rang: {color_label} | {fmt_label}",
                parse_mode="HTML"
            )
            await generate_qr(query.message, chat_id, uid, sr, er)
        except Exception as ex:
            log_error(uid, str(ex), f"rng_{part}")
            await query.message.reply_text(f"❌ Xato: {ex}")
        return

    # ===== ADMIN ONLY =====
    if uid != ADMIN_ID:
        return

    # ---- Pending ----
    if data == "adm_pending":
        if not db["pending"]:
            return await query.message.reply_text("✅ Hech kim kutmayapti")
        await query.message.reply_text(f"📥 <b>{len(db['pending'])} ta so'rov:</b>", parse_mode="HTML")
        for pid in db["pending"]:
            display = get_display(pid)
            kb = InlineKeyboardMarkup([[
                InlineKeyboardButton("✅ Qabul",   callback_data=f"ok_{pid}"),
                InlineKeyboardButton("❌ Rad",     callback_data=f"no_{pid}"),
                InlineKeyboardButton("👁 Ko'rish", callback_data=f"view_{pid}"),
            ]])
            await query.message.reply_text(
                f"👤 {display}\n🆔 <code>{pid}</code>",
                parse_mode="HTML", reply_markup=kb
            )
        return

    # ---- Allowed ----
    if data == "adm_allowed":
        if not db["allowed"]:
            return await query.message.reply_text("📋 Ruxsat berilganlar yo'q")
        await query.message.reply_text(f"📋 <b>{len(db['allowed'])} ta foydalanuvchi:</b>", parse_mode="HTML")
        for aid in db["allowed"]:
            display = get_display(aid)
            info    = db["user_info"].get(str(aid), {})
            qr_c    = info.get("qr_count", 0)
            limit   = info.get("limit", 50)
            kb = InlineKeyboardMarkup([[
                InlineKeyboardButton("👁 Ko'rish",     callback_data=f"view_{aid}"),
                InlineKeyboardButton("🚫 Block",       callback_data=f"block_{aid}"),
                InlineKeyboardButton("🔢 Limit",       callback_data=f"setlimitone_{aid}"),
                InlineKeyboardButton("💌 Xabar",       callback_data=f"msgone_{aid}"),
            ]])
            await query.message.reply_text(
                f"✅ {display} | 🖼 {qr_c} QR | 📦 Limit: {limit}",
                reply_markup=kb
            )
        return

    # ---- Blocked ----
    if data == "adm_blocked":
        if not db["blocked"]:
            return await query.message.reply_text("✅ Bloklangan foydalanuvchi yo'q")
        await query.message.reply_text(f"🚫 <b>{len(db['blocked'])} ta bloklangan:</b>", parse_mode="HTML")
        for bid in db["blocked"]:
            display = get_display(bid)
            kb = InlineKeyboardMarkup([[
                InlineKeyboardButton("✅ Unblock",  callback_data=f"unblock_{bid}"),
                InlineKeyboardButton("👁 Ko'rish",  callback_data=f"view_{bid}"),
            ]])
            await query.message.reply_text(f"🚫 {display}", reply_markup=kb)
        return

    # ---- Stats ----
    if data == "adm_stats":
        total     = len(db["allowed"]) + len(db["pending"]) + len(db["blocked"])
        top_users = sorted(
            db["user_info"].items(),
            key=lambda x: x[1].get("qr_count", 0),
            reverse=True
        )[:5]
        top_text = "\n".join(
            f"  {i+1}. {v.get('name','—')} — {v.get('qr_count',0)} QR (limit: {v.get('limit',50)})"
            for i, (_, v) in enumerate(top_users)
        ) or "  —"
        # Bugungi faollar
        today = datetime.datetime.now().strftime("%Y-%m-%d")
        active_today = sum(
            1 for v in db["user_info"].values()
            if v.get("last_active","").startswith(today)
        )
        text = (
            f"📊 <b>Bot Statistikasi</b>\n\n"
            f"👥 Jami: <b>{total}</b> foydalanuvchi\n"
            f"✅ Ruxsat: <b>{len(db['allowed'])}</b>\n"
            f"⏳ Kutmoqda: <b>{len(db['pending'])}</b>\n"
            f"🚫 Bloklangan: <b>{len(db['blocked'])}</b>\n"
            f"🟢 Bugun faol: <b>{active_today}</b>\n\n"
            f"🖼 Jami QR: <b>{db['stats'].get('total_qr', 0)}</b> ta\n"
            f"🔒 Bot: <b>{'🔴 Yopiq' if db['locked'] else '🟢 Ochiq'}</b>\n"
            f"🐛 Xatolar: <b>{len(db.get('errors',[]))}</b> ta\n\n"
            f"🏆 <b>Top 5:</b>\n{top_text}"
        )
        await query.message.reply_text(text, parse_mode="HTML", reply_markup=admin_main_kb())
        return

    # ---- Broadcast ----
    if data == "adm_broadcast":
        sessions[chat_id] = {"step": "broadcast"}
        await query.message.reply_text(
            "📢 Hammaga yuboriladigan xabarni yozing:\n\n/cancel — bekor qilish"
        )
        return

    # ---- Single message ----
    if data == "adm_msg":
        sessions[chat_id] = {"step": "msg_uid"}
        await query.message.reply_text(
            "💌 Foydalanuvchi ID sini kiriting:\n\n/cancel — bekor qilish"
        )
        return

    if data.startswith("msgone_"):
        xid = int(data[7:])
        sessions[chat_id] = {"step": "msg_text", "msg_target": xid}
        await query.message.reply_text(
            f"✏️ {get_display(xid)} ga xabar yozing:\n\n/cancel — bekor qilish"
        )
        return

    # ---- Limit berish (ADMIN paneldan) ----
    if data == "adm_setlimit":
        sessions[chat_id] = {"step": "set_limit_uid"}
        await query.message.reply_text(
            "🔢 Limit berilsin foydalanuvchi ID sini kiriting:\n\n/cancel — bekor qilish"
        )
        return

    if data.startswith("setlimitone_"):
        xid = int(data[12:])
        sessions[chat_id] = {"step": "set_limit_val", "limit_target": xid}
        await query.message.reply_text(
            f"🔢 {get_display(xid)} uchun yangi limit (masalan: 100):\n\n/cancel"
        )
        return

    # ---- Accept all ----
    if data == "adm_accept_all":
        count = len(db["pending"])
        if count == 0:
            return await query.message.reply_text("✅ Pending yo'q")
        for pid in list(db["pending"]):
            if pid not in db["allowed"]:
                db["allowed"].append(pid)
            try:
                await context.bot.send_message(pid, "🎉 So'rovingiz qabul qilindi! /start bosing.")
            except Exception:
                pass
        db["pending"].clear()
        save_db()
        await query.message.reply_text(f"✅ {count} ta foydalanuvchi qabul qilindi",
                                       reply_markup=admin_main_kb())
        return

    # ---- Reject all ----
    if data == "adm_reject_all":
        count = len(db["pending"])
        if count == 0:
            return await query.message.reply_text("✅ Pending yo'q")
        for pid in list(db["pending"]):
            if pid not in db["blocked"]:
                db["blocked"].append(pid)
            try:
                await context.bot.send_message(pid, "❌ So'rovingiz rad etildi.")
            except Exception:
                pass
        db["pending"].clear()
        save_db()
        await query.message.reply_text(f"❌ {count} ta foydalanuvchi rad etildi",
                                       reply_markup=admin_main_kb())
        return

    # ---- Lock / Unlock ----
    if data == "adm_lock":
        db["locked"] = True; save_db()
        await query.message.reply_text("🔒 Bot yopildi", reply_markup=admin_main_kb())
        return
    if data == "adm_unlock":
        db["locked"] = False; save_db()
        await query.message.reply_text("🔓 Bot ochildi", reply_markup=admin_main_kb())
        return

    # ---- Errors ----
    if data == "adm_errors":
        errs = db.get("errors", [])
        if not errs:
            return await query.message.reply_text("✅ Xatolar yo'q")
        last5 = errs[-5:]
        lines = ["🐛 <b>So'nggi 5 xato:</b>\n"]
        for e in reversed(last5):
            lines.append(
                f"🕐 {e['timestamp']}\n"
                f"👤 UID: <code>{e['user_id']}</code>\n"
                f"❌ {e['error']}\n"
                f"📍 {e['context']}\n"
            )
        await query.message.reply_text("\n".join(lines), parse_mode="HTML")
        return

    if data == "adm_clear_errors":
        db["errors"] = []; save_db()
        await query.message.reply_text("✅ Xatolar tozalandi.", reply_markup=admin_main_kb())
        return

    # ---- Activity ----
    if data == "adm_activity":
        today  = datetime.datetime.now().strftime("%Y-%m-%d")
        users  = db["user_info"]
        active = [(uid, v) for uid, v in users.items()
                  if v.get("last_active","").startswith(today)]
        active.sort(key=lambda x: x[1].get("last_active",""), reverse=True)
        if not active:
            return await query.message.reply_text("😶 Bugun hech kim faol emas")
        lines = [f"📈 <b>Bugungi faol foydalanuvchilar ({len(active)} ta):</b>\n"]
        for auid, v in active[:15]:
            lines.append(
                f"• {v.get('name','—')} | {v.get('last_active','—')} | "
                f"🖼 {v.get('qr_count',0)} QR | ▶ {v.get('last_action','—')}"
            )
        await query.message.reply_text("\n".join(lines), parse_mode="HTML")
        return

    # ---- History (admin barcha userlar uchun) ----
    if data == "adm_history":
        hist_all = db.get("history", {})
        if not hist_all:
            return await query.message.reply_text("📜 Tarix yo'q")
        total_entries = sum(len(v) for v in hist_all.values())
        lines = [f"🗂 <b>Jami tarix: {total_entries} ta amal</b>\n"]
        # So'nggi 10 ta global
        all_entries = []
        for auid, entries in hist_all.items():
            for e in entries:
                all_entries.append((auid, e))
        all_entries.sort(key=lambda x: x[1].get("date",""), reverse=True)
        for auid, e in all_entries[:10]:
            name = db["user_info"].get(auid, {}).get("name","—")
            lines.append(
                f"• {e['date']} | {name} | <code>{e['base']}</code> "
                f"[{e['range']}] — {e['count']} ta"
            )
        await query.message.reply_text("\n".join(lines), parse_mode="HTML")
        return

    # ---- Backup DB ----
    if data == "adm_backup":
        try:
            buf = io.BytesIO(json.dumps(db, ensure_ascii=False, indent=2).encode("utf-8"))
            buf.seek(0)
            ts  = datetime.datetime.now().strftime("%Y%m%d_%H%M")
            await query.message.reply_document(
                buf,
                filename=f"backup_{ts}.json",
                caption=f"💾 DB Backup — {ts}"
            )
        except Exception as e:
            await query.message.reply_text(f"❌ Backup xatosi: {e}")
        return

    # ---- Approve single ----
    if data.startswith("ok_"):
        xid = int(data[3:])
        if xid in db["pending"]:  db["pending"].remove(xid)
        if xid not in db["allowed"]: db["allowed"].append(xid)
        save_db()
        await query.message.reply_text(f"✅ Qabul: {get_display(xid)}")
        try: await context.bot.send_message(xid, "🎉 So'rovingiz qabul qilindi! /start bosing.")
        except Exception: pass
        return

    # ---- Reject single ----
    if data.startswith("no_"):
        xid = int(data[3:])
        if xid in db["pending"]:  db["pending"].remove(xid)
        if xid not in db["blocked"]: db["blocked"].append(xid)
        save_db()
        await query.message.reply_text(f"❌ Rad: {get_display(xid)}")
        try: await context.bot.send_message(xid, "❌ So'rovingiz rad etildi.")
        except Exception: pass
        return

    # ---- View user ----
    if data.startswith("view_"):
        xid  = int(data[5:])
        info = db["user_info"].get(str(xid), {})
        hist = db.get("history",{}).get(str(xid),[])
        status = user_status(xid)
        text = (
            f"👤 <b>Foydalanuvchi ma'lumoti</b>\n\n"
            f"🆔 ID: <code>{xid}</code>\n"
            f"📛 Ismi: {info.get('name','—')}\n"
            f"🔗 Username: {info.get('username','—')}\n"
            f"🖼 QR yasalgan: <b>{info.get('qr_count',0)}</b> ta\n"
            f"📦 Qolgan limit: <b>{info.get('limit',50)}</b> ta\n"
            f"📅 Qo'shilgan: {info.get('joined','—')}\n"
            f"🕐 Oxirgi faollik: {info.get('last_active','—')}\n"
            f"▶ Oxirgi amal: {info.get('last_action','—')}\n"
            f"📜 Tarix yozuvlari: {len(hist)} ta\n"
            f"📌 Holati: {status}"
        )
        btns = []
        if xid in db["allowed"]:
            btns = [
                InlineKeyboardButton("🚫 Bloklash",    callback_data=f"block_{xid}"),
                InlineKeyboardButton("🔢 Limit",       callback_data=f"setlimitone_{xid}"),
                InlineKeyboardButton("💌 Xabar",       callback_data=f"msgone_{xid}"),
            ]
        elif xid in db["blocked"]:
            btns = [InlineKeyboardButton("✅ Unblock", callback_data=f"unblock_{xid}")]
        elif xid in db["pending"]:
            btns = [
                InlineKeyboardButton("✅ Qabul", callback_data=f"ok_{xid}"),
                InlineKeyboardButton("❌ Rad",   callback_data=f"no_{xid}"),
            ]
        kb = InlineKeyboardMarkup([btns]) if btns else None
        await query.message.reply_text(text, parse_mode="HTML", reply_markup=kb)
        return

    # ---- Block ----
    if data.startswith("block_"):
        xid = int(data[6:])
        if xid in db["allowed"]:  db["allowed"].remove(xid)
        if xid not in db["blocked"]: db["blocked"].append(xid)
        save_db()
        await query.message.reply_text(f"🚫 Bloklandi: {get_display(xid)}")
        try: await context.bot.send_message(xid, "🚫 Ruxsatingiz bekor qilindi.")
        except Exception: pass
        return

    # ---- Unblock ----
    if data.startswith("unblock_"):
        xid = int(data[8:])
        if xid in db["blocked"]:  db["blocked"].remove(xid)
        if xid not in db["allowed"]: db["allowed"].append(xid)
        save_db()
        await query.message.reply_text(f"✅ Unblock: {get_display(xid)}")
        try: await context.bot.send_message(xid, "🎉 Qayta ruxsat berildi! /start bosing.")
        except Exception: pass
        return

# ===== /help =====
async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    save_user_info(update.effective_user)
    await update.message.reply_text(
        "❓ <b>Bot qo'llanmasi</b>\n\n"
        "📷 <b>Ishlash tartibi:</b>\n"
        "1. /start — botni ishga tushiring\n"
        "2. Shablon rasm yuboring (background)\n"
        "3. 🎨 QR rangini tanlang\n"
        "4. 📁 Fayl formatini tanlang (ZIP / PNG)\n"
        "5. 🔢 Asosiy kodni kiriting (masalan: <code>106243</code>)\n"
        "6. 📊 Diapazonni tanlang (masalan: <code>01–50</code>)\n"
        "7. ✅ QR kodlar tayyor!\n\n"
        "📋 <b>Komandalar:</b>\n"
        "/start — Boshlash\n"
        "/profile — Mening profilim\n"
        "/history — Yaratish tarixi\n"
        "/limit — Limit holati\n"
        "/help — Ushbu qo'llanma\n"
        "/cancel — Jarayonni bekor qilish\n\n"
        f"💬 Savollar uchun: @{ADMIN_USERNAME}",
        parse_mode="HTML"
    )

# ===== /profile =====
async def cmd_profile(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    save_user_info(update.effective_user)
    info   = db["user_info"].get(str(uid), {})
    status = user_status(uid)
    hist   = db.get("history", {}).get(str(uid), [])
    used   = info.get("qr_count", 0)
    limit  = info.get("limit", 50)
    limit_line = "📦 Limit: <b>\u221e (Admin)</b>\n" if uid == ADMIN_ID else (
        f"✅ Ishlatilgan: <b>{used}</b> ta\n"
        f"📦 Qolgan limit: <b>{limit}</b> ta\n"
    )
    await update.message.reply_text(
        f"👤 <b>Profilingiz</b>\n\n"
        f"📛 Ism: {info.get('name', '—')}\n"
        f"🔗 Username: {info.get('username', '—')}\n"
        f"🆔 ID: <code>{uid}</code>\n"
        f"🖼 Yaratilgan QR: <b>{used}</b> ta\n"
        f"{limit_line}"
        f"📜 Tarix yozuvlari: <b>{len(hist)}</b> ta\n"
        f"📅 Qo'shilgan: {info.get('joined', '—')}\n"
        f"🕐 Oxirgi faollik: {info.get('last_active', '—')}\n"
        f"📌 Holati: {status}",
        parse_mode="HTML"
    )

# ===== RUN =====
PORT        = int(os.getenv("PORT", 8443))
WEBHOOK_URL = os.getenv("WEBHOOK_URL", "")  # Railway: https://yourapp.up.railway.app

app = ApplicationBuilder().token(TOKEN).build()
app.add_handler(CommandHandler("start",   cmd_start))
app.add_handler(CommandHandler("admin",   cmd_admin))
app.add_handler(CommandHandler("cancel",  cmd_cancel))
app.add_handler(CommandHandler("limit",   cmd_limit))
app.add_handler(CommandHandler("history", cmd_history))
app.add_handler(CommandHandler("help",    cmd_help))
app.add_handler(CommandHandler("profile", cmd_profile))
app.add_handler(CallbackQueryHandler(callback_router))
app.add_handler(MessageHandler(filters.PHOTO, photo_handler))
app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))

if __name__ == '__main__':
    if WEBHOOK_URL:
        logger.info(f"Webhook rejimida ishga tushdi: {WEBHOOK_URL} port={PORT} ✅")
        app.run_webhook(
            listen="0.0.0.0",
            port=PORT,
            webhook_url=WEBHOOK_URL,
            drop_pending_updates=True,
        )
    else:
        logger.info("Polling rejimida ishga tushdi ✅")
        app.run_polling(drop_pending_updates=True)
