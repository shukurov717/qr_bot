import io, zipfile, json, os, datetime, asyncio
from PIL import Image
import qrcode

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler,
    CallbackQueryHandler, filters, ContextTypes
)

TOKEN   = os.getenv("TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID"))

DB_FILE  = "db.json"
sessions = {}   # chat_id -> session dict

def load_db():
    try:
        with open(DB_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {
            "allowed": [], "pending": [], "blocked": [],
            "locked": False,
            "stats": {"total_qr": 0},
            "user_info": {}
        }

def save_db():
    with open(DB_FILE, "w", encoding="utf-8") as f:
        json.dump(db, f, ensure_ascii=False, indent=2)

db = load_db()
# migrate old DBs
for _k, _v in [("stats", {"total_qr": 0}), ("user_info", {})]:
    if _k not in db:
        db[_k] = _v

# ===== HELPERS =====
def save_user_info(user):
    uid = str(user.id)
    old = db["user_info"].get(uid, {})
    db["user_info"][uid] = {
        "name":        user.full_name or "—",
        "username":    f"@{user.username}" if user.username else "—",
        "qr_count":   old.get("qr_count", 0),
        "last_active": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
    }
    save_db()

def get_display(uid):
    info = db["user_info"].get(str(uid), {})
    return "{} ({})".format(
    info.get('name', "Noma'lum"),
    info.get('username', '—')
)

def inc_qr(uid, n):
    s = str(uid)
    db["user_info"].setdefault(s, {"name":"—","username":"—","qr_count":0,"last_active":"—"})
    db["user_info"][s]["qr_count"] = db["user_info"][s].get("qr_count", 0) + n
    db["stats"]["total_qr"]        = db["stats"].get("total_qr", 0) + n
    save_db()

def user_status(uid):
    if uid in db["allowed"]: return "✅ Ruxsat berilgan"
    if uid in db["blocked"]: return "🚫 Bloklangan"
    if uid in db["pending"]: return "⏳ Kutmoqda"
    return "❓ Noma'lum"

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
    "black":  ("⚫ Qora",     "black",   "white"),
    "blue":   ("🔵 Ko'k",    "#1565c0", "white"),
    "red":    ("🔴 Qizil",   "#c62828", "white"),
    "green":  ("🟢 Yashil",  "#2e7d32", "white"),
    "purple": ("🟣 Binafsha","#6a1b9a", "white"),
}

def color_kb():
    btns = [InlineKeyboardButton(v[0], callback_data=f"color_{k}")
            for k, v in COLORS.items()]
    return InlineKeyboardMarkup([btns[:3], btns[3:]])

def format_kb():
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("📁 ZIP",          callback_data="fmt_zip"),
        InlineKeyboardButton("📄 Alohida PNG",  callback_data="fmt_single"),
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
            InlineKeyboardButton("📥 Pending",       callback_data="adm_pending"),
            InlineKeyboardButton("📋 Ruxsatlar",     callback_data="adm_allowed"),
        ],
        [
            InlineKeyboardButton("🚫 Bloklangan",    callback_data="adm_blocked"),
            InlineKeyboardButton("📊 Statistika",    callback_data="adm_stats"),
        ],
        [
            InlineKeyboardButton("📢 Broadcast",     callback_data="adm_broadcast"),
            InlineKeyboardButton("💌 Xabar yuborish",callback_data="adm_msg"),
        ],
        [
            InlineKeyboardButton("✅✅ Barchasini qabul", callback_data="adm_accept_all"),
            InlineKeyboardButton("❌❌ Barchasini rad",   callback_data="adm_reject_all"),
        ],
        [
            InlineKeyboardButton("🔒 Yopish",  callback_data="adm_lock"),
            InlineKeyboardButton("🔓 Ochish",  callback_data="adm_unlock"),
        ],
    ])

# ===== /start =====
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid     = update.effective_user.id
    chat_id = update.message.chat_id
    save_user_info(update.effective_user)

    ok, msg = check(uid)
    if not ok:
        return await update.message.reply_text(msg)

    session  = sessions.get(chat_id, {})
    has_last = bool(session.get("last_template") and
                    os.path.exists(session.get("last_template", "_")))

    rows = []
    if has_last:
        rows.append([InlineKeyboardButton("🔄 Oxirgi rasmni qayta ishlatish", callback_data="reuse")])
    rows.append([InlineKeyboardButton("📷 Yangi rasm yuborish", callback_data="new_photo")])

    await update.message.reply_text(
        "👋 <b>QR Kod Generator</b>\n\n"
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

# ===== QR GENERATOR =====
async def generate_qr(message, chat_id, uid, start_r: int, end_r: int):
    session = sessions.get(chat_id, {})
    try:
        template = Image.open(session["template"]).convert("RGBA")
        w, h = template.size

        # 🔥 original positioning — untouched
        block_w = int(w * 0.62)
        x1      = (w - block_w) // 2
        y1      = int(h * 0.44)
        size    = int(block_w * 0.85)

        _, fill, back = COLORS.get(session.get("color", "black"), ("", "black", "white"))
        fmt  = session.get("format", "zip")
        base = session.get("base", "")
        count = end_r - start_r + 1

        def make_qr_img(code: str) -> Image.Image:
            img = template.copy()
            qr  = qrcode.QRCode(
                version=None,
                error_correction=qrcode.constants.ERROR_CORRECT_L,
                box_size=5, border=1
            )
            qr.add_data(code)
            qr.make(fit=True)
            qr_img = qr.make_image(fill_color=fill, back_color=back).convert("RGBA")
            qr_img = qr_img.resize((size, size), Image.NEAREST)
            px = x1 + (block_w - size) // 2
            py = y1 + (block_w - size) // 2
            img.paste(qr_img, (px, py), qr_img)
            return img

        if fmt == "zip":
            zip_buf = io.BytesIO()
            with zipfile.ZipFile(zip_buf, "w") as zf:
                for i in range(start_r, end_r + 1):
                    code = base + str(i).zfill(2)
                    img  = make_qr_img(code)
                    buf  = io.BytesIO()
                    img.save(buf, "PNG")
                    zf.writestr(f"{code}.png", buf.getvalue())
            zip_buf.seek(0)
            await message.reply_document(
                zip_buf, filename="qr_codes.zip",
                caption=f"✅ <b>{count}</b> ta QR kod tayyor!",
                parse_mode="HTML"
            )
        else:
            for i in range(start_r, end_r + 1):
                code = base + str(i).zfill(2)
                img  = make_qr_img(code)
                buf  = io.BytesIO()
                img.save(buf, "PNG")
                buf.seek(0)
                await message.reply_document(buf, filename=f"{code}.png")
                await asyncio.sleep(0.15)
            await message.reply_text(f"✅ <b>{count}</b> ta PNG yuborildi!", parse_mode="HTML")

        inc_qr(uid, count)
        sessions.pop(chat_id, None)

    except Exception as e:
        await message.reply_text(f"❌ Xato: {e}")

# ===== PHOTO HANDLER =====
async def photo_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid     = update.effective_user.id
    chat_id = update.message.chat_id
    save_user_info(update.effective_user)

    ok, msg = check(uid)
    if not ok:
        return await update.message.reply_text(msg)

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
    await update.message.reply_text("🎨 QR rang tanlang:", reply_markup=color_kb())

# ===== TEXT HANDLER =====
async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.message.chat_id
    uid     = update.effective_user.id
    txt     = update.message.text.strip()
    save_user_info(update.effective_user)

    session = sessions.get(chat_id, {})
    step    = session.get("step")

    # --- Admin: broadcast text ---
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
        await update.message.reply_text(f"✅ {sent} ta foydalanuvchiga yuborildi.")
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
            await update.message.reply_text(f"✅ Xabar yuborildi: {get_display(target_uid)}")
        except Exception as e:
            await update.message.reply_text(f"❌ Yuborib bo'lmadi: {e}")
        return

    # --- User flows ---
    ok, msg = check(uid)
    if not ok:
        return await update.message.reply_text(msg)

    if not session:
        return await update.message.reply_text("⚠️ /start bosing")

    if step == "code":
        session["base"] = txt
        session["step"] = "range_select"
        await update.message.reply_text(
            f"🔢 Asosiy kod: <code>{txt}</code>\n\n📊 Diapazonni tanlang yoki son yozing (masalan: <code>00 50</code>):",
            parse_mode="HTML",
            reply_markup=range_kb()
        )
        return

    if step == "range":
        try:
            s, e = map(int, txt.split())
            session["step"] = "generating"
            color_label = COLORS.get(session.get("color","black"), ("⚫ Qora",))[0]
            fmt_label   = "📁 ZIP" if session.get("format","zip") == "zip" else "📄 Alohida PNG"
            await update.message.reply_text(
                f"⏳ <b>{s:02d}–{e:02d}</b> diapazoni yaratilmoqda...\n"
                f"🎨 Rang: {color_label} | {fmt_label}",
                parse_mode="HTML"
            )
            await generate_qr(update.message, chat_id, uid, s, e)
        except Exception:
            await update.message.reply_text("❌ Format noto'g'ri. Masalan: <code>00 50</code>", parse_mode="HTML")
        return

# ===== CALLBACK ROUTER =====
async def callback_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query   = update.callback_query
    await query.answer()
    data    = query.data
    uid     = query.from_user.id
    chat_id = query.message.chat_id

    if data == "new_photo":
        await query.message.reply_text("📷 Rasm yuboring:")
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
        fmt = data[4:]   # "zip" or "single"
        sessions.setdefault(chat_id, {})
        sessions[chat_id]["format"] = fmt
        sessions[chat_id]["step"]   = "code"
        await query.message.reply_text("🔢 Asosiy kodni yozing (masalan: <code>106243</code>):",
                                        parse_mode="HTML")
        return

    if data.startswith("rng_"):
        part = data[4:]
        if part == "custom":
            sessions.setdefault(chat_id, {})["step"] = "range"
            await query.message.reply_text(
                "✏️ Diapazon kiriting (masalan: <code>00 50</code>):",
                parse_mode="HTML"
            )
            return
        try:
            s_str, e_str = part.split("_")
            sr, er = int(s_str), int(e_str)
            sessions.setdefault(chat_id, {})["step"] = "generating"
            color_label = COLORS.get(sessions[chat_id].get("color","black"), ("⚫ Qora",))[0]
            fmt_label   = "📁 ZIP" if sessions[chat_id].get("format","zip") == "zip" else "📄 Alohida PNG"
            await query.message.reply_text(
                f"⏳ <b>{sr:02d}–{er:02d}</b> diapazoni yaratilmoqda...\n"
                f"🎨 Rang: {color_label} | {fmt_label}",
                parse_mode="HTML"
            )
            await generate_qr(query.message, chat_id, uid, sr, er)
        except Exception as ex:
            await query.message.reply_text(f"❌ Xato: {ex}")
        return

    if uid != ADMIN_ID:
        return

    # ---- Pending list ----
    if data == "adm_pending":
        if not db["pending"]:
            return await query.message.reply_text("✅ Hech kim kutmayapti")
        await query.message.reply_text(f"📥 <b>{len(db['pending'])} ta so'rov:</b>", parse_mode="HTML")
        for pid in db["pending"]:
            display = get_display(pid)
            kb = InlineKeyboardMarkup([[
                InlineKeyboardButton("✅ Qabul",    callback_data=f"ok_{pid}"),
                InlineKeyboardButton("❌ Rad",      callback_data=f"no_{pid}"),
                InlineKeyboardButton("👁 Ko'rish",  callback_data=f"view_{pid}"),
            ]])
            await query.message.reply_text(
                f"👤 {display}\n🆔 <code>{pid}</code>",
                parse_mode="HTML", reply_markup=kb
            )
        return

    # ---- Allowed list ----
    if data == "adm_allowed":
        if not db["allowed"]:
            return await query.message.reply_text("📋 Ruxsat berilganlar yo'q")
        await query.message.reply_text(f"📋 <b>{len(db['allowed'])} ta foydalanuvchi:</b>", parse_mode="HTML")
        for aid in db["allowed"]:
            display = get_display(aid)
            qr_c    = db["user_info"].get(str(aid), {}).get("qr_count", 0)
            kb = InlineKeyboardMarkup([[
                InlineKeyboardButton("👁 Ko'rish", callback_data=f"view_{aid}"),
                InlineKeyboardButton("🚫 Block",   callback_data=f"block_{aid}"),
                InlineKeyboardButton("💌 Xabar",   callback_data=f"msgone_{aid}"),
            ]])
            await query.message.reply_text(
                f"✅ {display} | 🖼 {qr_c} QR",
                reply_markup=kb
            )
        return

    # ---- Blocked list ----
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
        total = len(db["allowed"]) + len(db["pending"]) + len(db["blocked"])
        # top 5 users by QR count
        top_users = sorted(
            db["user_info"].items(),
            key=lambda x: x[1].get("qr_count", 0),
            reverse=True
        )[:5]
        top_text = "\n".join(
            f"  {i+1}. {v.get('name','—')} — {v.get('qr_count',0)} QR"
            for i, (_, v) in enumerate(top_users)
        ) or "  —"
        text = (
            f"📊 <b>Bot Statistikasi</b>\n\n"
            f"👥 Jami: <b>{total}</b> foydalanuvchi\n"
            f"✅ Ruxsat: <b>{len(db['allowed'])}</b>\n"
            f"⏳ Kutmoqda: <b>{len(db['pending'])}</b>\n"
            f"🚫 Bloklangan: <b>{len(db['blocked'])}</b>\n\n"
            f"🖼 Jami QR yasalgan: <b>{db['stats'].get('total_qr', 0)}</b> ta\n"
            f"🔒 Bot holati: <b>{'🔴 Yopiq' if db['locked'] else '🟢 Ochiq'}</b>\n\n"
            f"🏆 <b>Top 5 foydalanuvchi:</b>\n{top_text}"
        )
        await query.message.reply_text(text, parse_mode="HTML")
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
        display = get_display(xid)
        await query.message.reply_text(
            f"✏️ {display} ga yuboriladigan xabarni yozing:\n\n/cancel — bekor qilish"
        )
        return

    # ---- Accept all pending ----
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
        await query.message.reply_text(f"✅ {count} ta foydalanuvchi qabul qilindi")
        return

    # ---- Reject all pending ----
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
        await query.message.reply_text(f"❌ {count} ta foydalanuvchi rad etildi")
        return

    # ---- Lock / Unlock ----
    if data == "adm_lock":
        db["locked"] = True
        save_db()
        await query.message.reply_text("🔒 Bot yopildi")
        return
    if data == "adm_unlock":
        db["locked"] = False
        save_db()
        await query.message.reply_text("🔓 Bot ochildi")
        return

    # ---- Approve single pending ----
    if data.startswith("ok_"):
        xid = int(data[3:])
        if xid in db["pending"]:  db["pending"].remove(xid)
        if xid not in db["allowed"]: db["allowed"].append(xid)
        save_db()
        display = get_display(xid)
        await query.message.reply_text(f"✅ Qabul qilindi: {display}")
        try: await context.bot.send_message(xid, "🎉 So'rovingiz qabul qilindi! /start bosing.")
        except Exception: pass
        return

    # ---- Reject single pending ----
    if data.startswith("no_"):
        xid = int(data[3:])
        if xid in db["pending"]:  db["pending"].remove(xid)
        if xid not in db["blocked"]: db["blocked"].append(xid)
        save_db()
        display = get_display(xid)
        await query.message.reply_text(f"❌ Rad etildi: {display}")
        try: await context.bot.send_message(xid, "❌ So'rovingiz rad etildi.")
        except Exception: pass
        return

    # ---- View user ----
    if data.startswith("view_"):
        xid  = int(data[5:])
        info = db["user_info"].get(str(xid), {})
        status = user_status(xid)
        text = (
            f"👤 <b>Foydalanuvchi ma'lumoti</b>\n\n"
            f"🆔 ID: <code>{xid}</code>\n"
            f"📛 Ismi: {info.get('name','—')}\n"
            f"🔗 Username: {info.get('username','—')}\n"
            f"🖼 QR yasalgan: <b>{info.get('qr_count',0)}</b> ta\n"
            f"🕐 Oxirgi faollik: {info.get('last_active','—')}\n"
            f"📌 Holati: {status}"
        )
        btns = []
        if xid in db["allowed"]:
            btns = [
                InlineKeyboardButton("🚫 Bloklash",   callback_data=f"block_{xid}"),
                InlineKeyboardButton("💌 Xabar",      callback_data=f"msgone_{xid}"),
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

    # ---- Block user ----
    if data.startswith("block_"):
        xid = int(data[6:])
        if xid in db["allowed"]:  db["allowed"].remove(xid)
        if xid not in db["blocked"]: db["blocked"].append(xid)
        save_db()
        display = get_display(xid)
        await query.message.reply_text(f"🚫 Bloklandi: {display}")
        try: await context.bot.send_message(xid, "🚫 Ruxsatingiz bekor qilindi.")
        except Exception: pass
        return

    # ---- Unblock user ----
    if data.startswith("unblock_"):
        xid = int(data[8:])
        if xid in db["blocked"]:  db["blocked"].remove(xid)
        if xid not in db["allowed"]: db["allowed"].append(xid)
        save_db()
        display = get_display(xid)
        await query.message.reply_text(f"✅ Unblock qilindi: {display}")
        try: await context.bot.send_message(xid, "🎉 Qayta ruxsat berildi! /start bosing.")
        except Exception: pass
        return

# ===== RUN =====
app = ApplicationBuilder().token(TOKEN).build()
app.add_handler(CommandHandler("start",  cmd_start))
app.add_handler(CommandHandler("admin",  cmd_admin))
app.add_handler(CommandHandler("cancel", cmd_cancel))
app.add_handler(CallbackQueryHandler(callback_router))
app.add_handler(MessageHandler(filters.PHOTO, photo_handler))
app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))
if __name__ == '__main__':
    app.run_polling()
