import io, zipfile, json, os
from PIL import Image
import qrcode

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes

TOKEN = os.getenv("TOKEN")
ADMIN_ID = 6594366391
DB_FILE = "db.json"

users = {}

# ================= DB =================
def load_db():
    try:
        with open(DB_FILE, "r") as f:
            return json.load(f)
    except:
        return {"allowed": [], "pending": [], "blocked": [], "config": {"locked": True}}

def save_db():
    with open(DB_FILE, "w") as f:
        json.dump(db, f)

db = load_db()

# ================= UTILS =================
def is_admin(uid):
    return uid == ADMIN_ID

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

# ================= ADMIN PANEL =================
async def admin(update, context):
    kb = [
        [InlineKeyboardButton("👥 Users", callback_data="users")],
        [InlineKeyboardButton("🔒 Lock", callback_data="lock")]
    ]
    await update.message.reply_text("🎛 Admin Panel", reply_markup=InlineKeyboardMarkup(kb))

async def callbacks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    uid = q.from_user.id
    if not is_admin(uid):
        return await q.edit_message_text("❌ Ruxsat yo‘q")

    data = q.data

    if data == "users":
        txt = f"Allowed: {len(db['allowed'])}\nPending: {len(db['pending'])}"
        kb = [
            [InlineKeyboardButton("⏳ Pending", callback_data="pending")],
            [InlineKeyboardButton("🔙 Back", callback_data="admin")]
        ]
        return await q.edit_message_text(txt, reply_markup=InlineKeyboardMarkup(kb))

    if data == "pending":
        buttons = []
        for u in db["pending"]:
            buttons.append([
                InlineKeyboardButton(f"✅ {u}", callback_data=f"ok_{u}"),
                InlineKeyboardButton(f"❌ {u}", callback_data=f"no_{u}")
            ])
        return await q.edit_message_text("Pending:", reply_markup=InlineKeyboardMarkup(buttons))

    if data.startswith("ok_"):
        u = int(data.split("_")[1])
        if u in db["pending"]:
            db["pending"].remove(u)
        db["allowed"].append(u)
        save_db()
        return await q.answer("OK")

    if data.startswith("no_"):
        u = int(data.split("_")[1])
        if u in db["pending"]:
            db["pending"].remove(u)
        db["blocked"].append(u)
        save_db()
        return await q.answer("Blocked")

    if data == "lock":
        db["config"]["locked"] = not db["config"]["locked"]
        save_db()
        return await q.edit_message_text("🔄 O‘zgardi")

# ================= QR =================
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

    if uid not in users:
        return

    user = users[uid]

    if user["step"] == "code":
        user["base"] = update.message.text.strip()
        user["step"] = "range"
        return await update.message.reply_text("📊 00 50")

    if user["step"] == "range":
        start, end = map(int, update.message.text.split())

        template = Image.open(user["template"]).convert("RGBA")
        w, h = template.size

        # 🔥 SENIKI (TEGILMADI)
        block_w = int(w * 0.62)
        x1 = (w - block_w) // 2
        y1 = int(h * 0.44)

        margin = int(block_w * 0.015)
        size = int(block_w * 0.85)

        zip_buffer = io.BytesIO()
        zipf = zipfile.ZipFile(zip_buffer, "w")

        for i in range(start, end + 1):
            code = user["base"] + str(i).zfill(2)

            qr = qrcode.QRCode(
                error_correction=qrcode.constants.ERROR_CORRECT_H,
                box_size=10,
                border=4
            )
            qr.add_data(code)
            qr.make(fit=True)

            qr_img = qr.make_image(fill_color="black", back_color="white").convert("RGBA")
            qr_img = qr_img.resize((size, size), Image.NEAREST)

            px = x1 + (block_w - size) // 2
            py = y1 + (block_w - size) // 2

            img = template.copy()
            img.paste(qr_img, (px, py), qr_img)

            buf = io.BytesIO()
            img.save(buf, format="PNG")

            zipf.writestr(f"{code}.png", buf.getvalue())

        zipf.close()
        zip_buffer.seek(0)

        await update.message.reply_document(zip_buffer, filename="qr.zip")
        users.pop(uid)

# ================= RUN =================
app = ApplicationBuilder().token(TOKEN).build()

app.add_handler(CommandHandler("start", start))
app.add_handler(CommandHandler("admin", admin))
app.add_handler(CallbackQueryHandler(callbacks))
app.add_handler(MessageHandler(filters.PHOTO, photo))
app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text))

app.run_polling()