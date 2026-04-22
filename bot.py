import io, zipfile, json, os
from PIL import Image
import qrcode

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes

TOKEN = os.getenv("TOKEN")
ADMIN_ID = 6594366391
DB_FILE = "db.json"

users = {}

# ===== DB =====
def load_db():
    try:
        with open(DB_FILE, "r") as f:
            return json.load(f)
    except:
        return {"allowed": [], "pending": [], "blocked": [], "locked": False}

def save_db():
    with open(DB_FILE, "w") as f:
        json.dump(db, f)

db = load_db()

# ===== ACCESS =====
def check(uid):
    if db["locked"]:
        return False, "🔒 Bot yopiq"

    if uid in db["blocked"]:
        return False, "🚫 Block"

    if uid not in db["allowed"]:
        if uid not in db["pending"]:
            db["pending"].append(uid)
            save_db()
        return False, "⏳ Adminga so‘rov yuborildi"

    return True, None

# ===== START =====
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ok, msg = check(update.effective_user.id)
    if not ok:
        return await update.message.reply_text(msg)
    await update.message.reply_text("📷 Rasm yubor")

# ===== ADMIN =====
async def admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return

    kb = [
        [InlineKeyboardButton("⏳ Pending", callback_data="pending")],
        [InlineKeyboardButton("🔒 Lock", callback_data="lock")]
    ]
    await update.message.reply_text("Admin panel", reply_markup=InlineKeyboardMarkup(kb))

async def callbacks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    if q.from_user.id != ADMIN_ID:
        return

    data = q.data

    if data == "pending":
        if not db["pending"]:
            return await q.edit_message_text("Pending yo‘q")

        buttons = []
        for u in db["pending"]:
            buttons.append([
                InlineKeyboardButton(f"✅ {u}", callback_data=f"ok:{u}"),
                InlineKeyboardButton(f"❌ {u}", callback_data=f"no:{u}")
            ])

        return await q.edit_message_text("Pending:", reply_markup=InlineKeyboardMarkup(buttons))

    if data.startswith("ok:"):
        uid = int(data.split(":")[1])
        if uid in db["pending"]:
            db["pending"].remove(uid)
        if uid not in db["allowed"]:
            db["allowed"].append(uid)
        save_db()
        return await q.answer("Qabul qilindi")

    if data.startswith("no:"):
        uid = int(data.split(":")[1])
        if uid in db["pending"]:
            db["pending"].remove(uid)
        if uid not in db["blocked"]:
            db["blocked"].append(uid)
        save_db()
        return await q.answer("Rad etildi")

    if data == "lock":
        db["locked"] = not db["locked"]
        save_db()
        return await q.answer("Holat o‘zgardi")

# ===== QR =====
async def photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ok, msg = check(update.effective_user.id)
    if not ok:
        return await update.message.reply_text(msg)

    file = await update.message.photo[-1].get_file()
    path = f"{update.message.chat_id}.png"
    await file.download_to_drive(path)

    users[update.message.chat_id] = {
        "template": path,
        "step": "code"
    }

    await update.message.reply_text("🔢 Kod yoz")

async def text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.message.chat_id

    if chat_id not in users:
        return

    user = users[chat_id]

    if user["step"] == "code":
        user["base"] = update.message.text.strip()
        user["step"] = "range"
        return await update.message.reply_text("📊 00 50")

    if user["step"] == "range":
        start, end = map(int, update.message.text.split())

        template = Image.open(user["template"]).convert("RGBA")
        w, h = template.size

        # ===== SENING ORIGINAL JOYLASHUVING (TEGILMADI) =====
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

            # ❗ ORIGINAL (markaz emas!)
            px = x1 + margin
            py = y1 + margin

            img = template.copy()
            img.paste(qr_img, (px, py), qr_img)

            buf = io.BytesIO()
            img.save(buf, format="PNG")

            zipf.writestr(f"{code}.png", buf.getvalue())

        zipf.close()
        zip_buffer.seek(0)

        await update.message.reply_document(zip_buffer, filename="qr.zip")
        users.pop(chat_id)

# ===== RUN =====
app = ApplicationBuilder().token(TOKEN).build()

app.add_handler(CommandHandler("start", start))
app.add_handler(CommandHandler("admin", admin))
app.add_handler(CallbackQueryHandler(callbacks))
app.add_handler(MessageHandler(filters.PHOTO, photo))
app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text))

app.run_polling()