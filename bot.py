import io, zipfile, json, os
from PIL import Image
import qrcode

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler,
    CallbackQueryHandler, filters, ContextTypes
)

# ===== ENV (Railway) =====
TOKEN = os.getenv("TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID"))

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
        return False, "🚫 Block qilingan"

    if uid not in db["allowed"]:
        if uid not in db["pending"]:
            db["pending"].append(uid)
            save_db()
        return False, "⏳ So‘rov adminga yuborildi"

    return True, None


# ===== START =====
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ok, msg = check(update.effective_user.id)
    if not ok:
        return await update.message.reply_text(msg)

    await update.message.reply_text("📷 Rasm yubor")


# ===== ADMIN PANEL =====
async def admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return

    kb = [
        [InlineKeyboardButton("📥 Pending", callback_data="pending")],
        [InlineKeyboardButton("🔒 Lock", callback_data="lock")],
        [InlineKeyboardButton("🔓 Unlock", callback_data="unlock")]
    ]

    await update.message.reply_text("⚙️ Admin panel", reply_markup=InlineKeyboardMarkup(kb))


# ===== ADMIN BUTTONS =====
async def admin_buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.from_user.id != ADMIN_ID:
        return

    if query.data == "pending":
        if not db["pending"]:
            return await query.message.reply_text("❌ Pending yo‘q")

        for uid in db["pending"]:
            kb = [
                [
                    InlineKeyboardButton("✅ Approve", callback_data=f"ok_{uid}"),
                    InlineKeyboardButton("❌ Reject", callback_data=f"no_{uid}")
                ]
            ]
            await query.message.reply_text(f"👤 {uid}", reply_markup=InlineKeyboardMarkup(kb))

    elif query.data.startswith("ok_"):
        uid = int(query.data.split("_")[1])
        if uid in db["pending"]:
            db["pending"].remove(uid)
            db["allowed"].append(uid)
            save_db()
            await query.message.reply_text(f"✅ Qabul qilindi: {uid}")

    elif query.data.startswith("no_"):
        uid = int(query.data.split("_")[1])
        if uid in db["pending"]:
            db["pending"].remove(uid)
            db["blocked"].append(uid)
            save_db()
            await query.message.reply_text(f"❌ Rad etildi: {uid}")

    elif query.data == "lock":
        db["locked"] = True
        save_db()
        await query.message.reply_text("🔒 Bot yopildi")

    elif query.data == "unlock":
        db["locked"] = False
        save_db()
        await query.message.reply_text("🔓 Bot ochildi")


# ===== 📷 Rasm qabul =====
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

    await update.message.reply_text("🔢 Kod yoz (masalan: 106243)")


# ===== ✍️ TEXT =====
async def text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.message.chat_id

    if chat_id not in users:
        return await update.message.reply_text("⚠️ Avval rasm yubor")

    user = users[chat_id]

    # 1-step
    if user["step"] == "code":
        user["base"] = update.message.text.strip()
        user["step"] = "range"
        return await update.message.reply_text("📊 Diapazon: 00 50")

    # 2-step
    if user["step"] == "range":
        try:
            start, end = map(int, update.message.text.split())
        except:
            return await update.message.reply_text("❌ Format: 00 50")

        try:
            template = Image.open(user["template"]).convert("RGBA")
            w, h = template.size

            # 🔥 SENING ORIGINAL JOYLASHUV (tegilmadi)
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

            zipf.close()
            zip_buffer.seek(0)

            await update.message.reply_document(zip_buffer, filename="qr.zip")

            users.pop(chat_id)

        except Exception as e:
            await update.message.reply_text(f"❌ Xato:\n{str(e)}")


# ===== RUN =====
app = ApplicationBuilder().token(TOKEN).build()

app.add_handler(CommandHandler("start", start))
app.add_handler(CommandHandler("admin", admin))
app.add_handler(CallbackQueryHandler(admin_buttons))

app.add_handler(MessageHandler(filters.PHOTO, photo))
app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text))

app.run_polling()