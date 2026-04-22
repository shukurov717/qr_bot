import io, zipfile, json, time, os
from PIL import Image
import qrcode

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler,
    CallbackQueryHandler, filters, ContextTypes
)

# ================= CONFIG =================
import os
TOKEN = os.getenv("8037120838:AAExWv4GRR6wQKOOTQ3zS4Otk_gt9bwruoE")
ADMIN_ID = 6594366391

DB_FILE = "db.json"

# ================= DB =================
def load_db():
    try:
        with open(DB_FILE, "r") as f:
            return json.load(f)
    except:
        return {
            "allowed": [],
            "pending": [],
            "blocked": [],
            "users": {},
            "config": {"locked": True, "max_range": 100}
        }

def save_db():
    with open(DB_FILE, "w") as f:
        json.dump(db, f)

db = load_db()

# ================= UTILS =================
def is_admin(uid):
    return uid == ADMIN_ID

def back(btn="admin"):
    return InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Orqaga", callback_data=btn)]])

def check_access(uid):
    if db["config"]["locked"]:
        return False, "🔒 Bot yopiq"

    if uid in db["blocked"]:
        return False, "🚫 Bloklangan"

    if uid not in db["allowed"]:
        if uid not in db["pending"]:
            db["pending"].append(uid)
            save_db()
        return False, "⏳ So‘rov yuborildi"

    return True, None

# ================= START =================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    ok, msg = check_access(uid)
    if not ok:
        return await update.message.reply_text(msg)
    await update.message.reply_text("📷 Rasm yubor")

# ================= ADMIN PANEL =================
async def admin_panel(update, context):
    kb = [
        [InlineKeyboardButton("👥 Users", callback_data="users")],
        [InlineKeyboardButton("📊 Stats", callback_data="stats")],
        [InlineKeyboardButton("🔒 Lock/Unlock", callback_data="lock")]
    ]
    await update.effective_message.reply_text("🎛 Admin Panel", reply_markup=InlineKeyboardMarkup(kb))

# ================= CALLBACK =================
async def callbacks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    uid = q.from_user.id
    data = q.data

    if not is_admin(uid):
        return await q.edit_message_text("❌ Ruxsat yo‘q")

    # ADMIN
    if data == "admin":
        return await admin_panel(update, context)

    # USERS
    if data == "users":
        text = f"""👥 Users:
Allowed: {len(db['allowed'])}
Pending: {len(db['pending'])}
Blocked: {len(db['blocked'])}
"""
        kb = [
            [InlineKeyboardButton("📋 Allowed", callback_data="allowed")],
            [InlineKeyboardButton("⏳ Pending", callback_data="pending")],
            [InlineKeyboardButton("🚫 Blocked", callback_data="blocked")],
            [InlineKeyboardButton("🔙 Orqaga", callback_data="admin")]
        ]
        return await q.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb))

    # ALLOWED LIST
    if data == "allowed":
        txt = "\n".join(map(str, db["allowed"])) or "Bo‘sh"
        return await q.edit_message_text(f"📋 Allowed:\n{txt}", reply_markup=back("users"))

    # BLOCKED LIST
    if data == "blocked":
        txt = "\n".join(map(str, db["blocked"])) or "Bo‘sh"
        return await q.edit_message_text(f"🚫 Blocked:\n{txt}", reply_markup=back("users"))

    # PENDING LIST
    if data == "pending":
        buttons = []
        for u in db["pending"][:10]:
            buttons.append([
                InlineKeyboardButton(f"✅ {u}", callback_data=f"ok_{u}"),
                InlineKeyboardButton(f"❌ {u}", callback_data=f"no_{u}")
            ])
        buttons.append([InlineKeyboardButton("🔙 Orqaga", callback_data="users")])
        return await q.edit_message_text("⏳ Pending:", reply_markup=InlineKeyboardMarkup(buttons))

    # ACCEPT (FIXED)
    if data.startswith("ok_"):
        u = int(data.split("_")[1])

        if u in db["pending"]:
            db["pending"].remove(u)

        if u not in db["allowed"]:
            db["allowed"].append(u)

        save_db()
        return await q.answer("✅ Qabul qilindi")

    # REJECT (FIXED)
    if data.startswith("no_"):
        u = int(data.split("_")[1])

        if u in db["pending"]:
            db["pending"].remove(u)

        if u not in db["blocked"]:
            db["blocked"].append(u)

        save_db()
        return await q.answer("🚫 Rad etildi")

    # LOCK
    if data == "lock":
        db["config"]["locked"] = not db["config"]["locked"]
        save_db()
        status = "🔒 Yopiq" if db["config"]["locked"] else "🔓 Ochiq"
        return await q.edit_message_text(f"Holat: {status}", reply_markup=back())

    # STATS
    if data == "stats":
        total_qr = sum([u.get("qr", 0) for u in db["users"].values()])
        txt = f"""📊 Statistika:
Users: {len(db["users"])}
QR: {total_qr}
"""
        return await q.edit_message_text(txt, reply_markup=back())

# ================= QR FLOW (SENIKI) =================
users = {}

async def photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    ok, msg = check_access(uid)
    if not ok:
        return await update.message.reply_text(msg)

    file = await update.message.photo[-1].get_file()
    path = f"{uid}.png"
    await file.download_to_drive(path)

    users[uid] = {"template": path, "step": "code"}
    await update.message.reply_text("🔢 Kod yoz (masalan: 106243)")

async def text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    ok, msg = check_access(uid)
    if not ok:
        return await update.message.reply_text(msg)

    if uid not in users:
        return

    user = users[uid]

    if user["step"] == "code":
        user["base"] = update.message.text.strip()
        user["step"] = "range"
        return await update.message.reply_text("📊 Diapazon: 00 50")

    if user["step"] == "range":
        try:
            start, end = map(int, update.message.text.split())

            template = Image.open(user["template"]).convert("RGBA")
            w, h = template.size

            block_w = int(w * 0.62)
            x1 = (w - block_w) // 2
            y1 = int(h * 0.44)

            margin = int(block_w * 0.015)
            size = int(block_w * 0.85)

            zip_buffer = io.BytesIO()
            zipf = zipfile.ZipFile(zip_buffer, "w")

            for i in range(start, end + 1):
                code = user["base"] + str(i).zfill(2)

                img = template.copy()

                qr = qrcode.QRCode(
                    version=None,
                    error_correction=qrcode.constants.ERROR_CORRECT_L,
                    box_size=5,
                    border=1
                )
                qr.add_data(code)
                qr.make(fit=True)

                qr_img = qr.make_image(fill_color="black", back_color="white").convert("RGBA")
                qr_img = qr_img.resize((size, size), Image.NEAREST)

                px = x1 + (block_w - size) // 2
                py = y1 + (block_w - size) // 2

                img.paste(qr_img, (px, py), qr_img)

                img_bytes = io.BytesIO()
                img.save(img_bytes, format="PNG")

                zipf.writestr(f"{code}.png", img_bytes.getvalue())

                db["users"].setdefault(str(uid), {"qr": 0})
                db["users"][str(uid)]["qr"] += 1

            zipf.close()
            zip_buffer.seek(0)

            await update.message.reply_document(zip_buffer, filename="qr.zip")
            save_db()
            users.pop(uid)

        except:
            await update.message.reply_text("❌ Xato")

# ================= RUN =================
app = ApplicationBuilder().token(TOKEN).build()

app.add_handler(CommandHandler("start", start))
app.add_handler(CommandHandler("admin", admin_panel))
app.add_handler(CallbackQueryHandler(callbacks))
app.add_handler(MessageHandler(filters.PHOTO, photo))
app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text))

app.run_polling()