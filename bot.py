import io, zipfile, json, os
from PIL import Image
import qrcode

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler,
    CallbackQueryHandler, filters, ContextTypes
)

# ================= CONFIG =================
TOKEN = os.getenv("TOKEN")
ADMIN_ID = 6594366391
DB_FILE = "db.json"

if not TOKEN:
    raise ValueError("TOKEN topilmadi! Railway ga TOKEN qo‘y!")

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
            "config": {"locked": False}
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
        return False, "🚫 Blocklangan"

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

# ================= ADMIN =================
async def admin_panel(update, context):
    kb = [
        [InlineKeyboardButton("👥 Users", callback_data="users")],
        [InlineKeyboardButton("📊 Stats", callback_data="stats")],
        [InlineKeyboardButton("🔒 Lock", callback_data="lock")]
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

    if data == "admin":
        return await admin_panel(update, context)

    if data == "users":
        txt = f"""👥 Users:
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
        return await q.edit_message_text(txt, reply_markup=InlineKeyboardMarkup(kb))

    if data == "allowed":
        txt = "\n".join(map(str, db["allowed"])) or "Bo‘sh"
        return await q.edit_message_text(txt, reply_markup=back("users"))

    if data == "blocked":
        txt = "\n".join(map(str, db["blocked"])) or "Bo‘sh"
        return await q.edit_message_text(txt, reply_markup=back("users"))

    if data == "pending":
        buttons = []
        for u in db["pending"][:10]:
            buttons.append([
                InlineKeyboardButton(f"✅ {u}", callback_data=f"ok_{u}"),
                InlineKeyboardButton(f"❌ {u}", callback_data=f"no_{u}")
            ])
        buttons.append([InlineKeyboardButton("🔙 Orqaga", callback_data="users")])
        return await q.edit_message_text("⏳ Pending:", reply_markup=InlineKeyboardMarkup(buttons))

    if data.startswith("ok_"):
        u = int(data.split("_")[1])
        if u in db["pending"]:
            db["pending"].remove(u)
        if u not in db["allowed"]:
            db["allowed"].append(u)
        save_db()
        return await q.answer("✅ Qabul qilindi")

    if data.startswith("no_"):
        u = int(data.split("_")[1])
        if u in db["pending"]:
            db["pending"].remove(u)
        if u not in db["blocked"]:
            db["blocked"].append(u)
        save_db()
        return await q.answer("🚫 Rad etildi")

    if data == "lock":
        db["config"]["locked"] = not db["config"]["locked"]
        save_db()
        return await q.edit_message_text("🔄 Holat o‘zgardi", reply_markup=back())

    if data == "stats":
        total = sum([u.get("qr", 0) for u in db["users"].values()])
        return await q.edit_message_text(f"QR: {total}", reply_markup=back())

# ================= QR =================
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
    await update.message.reply_text("🔢 Kod yoz")

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
        return await update.message.reply_text("📊 00 50 yoz")

    if user["step"] == "range":
        try:
            start, end = map(int, update.message.text.split())

            template = Image.open(user["template"]).convert("RGBA")
            w, h = template.size

            size = int(w * 0.35)

            zip_buffer = io.BytesIO()
            zipf = zipfile.ZipFile(zip_buffer, "w")

            for i in range(start, end + 1):
                code = user["base"] + str(i).zfill(2)

                qr = qrcode.make(code).resize((size, size))

                img = template.copy()
                img.paste(qr, (int(w*0.3), int(h*0.5)))

                buf = io.BytesIO()
                img.save(buf, format="PNG")

                zipf.writestr(f"{code}.png", buf.getvalue())

                db["users"].setdefault(str(uid), {"qr": 0})
                db["users"][str(uid)]["qr"] += 1

            zipf.close()
            zip_buffer.seek(0)

            await update.message.reply_document(zip_buffer, filename="qr.zip")
            save_db()
            users.pop(uid)

        except Exception as e:
            await update.message.reply_text(f"❌ Xato: {e}")

# ================= RUN =================
app = ApplicationBuilder().token(TOKEN).build()

app.add_handler(CommandHandler("start", start))
app.add_handler(CommandHandler("admin", admin_panel))
app.add_handler(CallbackQueryHandler(callbacks))
app.add_handler(MessageHandler(filters.PHOTO, photo))
app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text))

app.run_polling()