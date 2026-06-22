import os
import io
import logging
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
import fitz  # PyMuPDF

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")

if not TELEGRAM_TOKEN:
    raise RuntimeError("TELEGRAM_BOT_TOKEN environment variable sifatida o'rnatilishi kerak.")


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "Salom! Menga PDF faylni yuboring — uni booklet (kitobcha) tartibida qayta tuzib, "
        "ikki tomonlama (manual duplex) chop etishga tayyor holda qaytarib beraman."
    )


def build_booklet(pdf_bytes: bytes):
    """PDF sahifalarini booklet (kitobcha) tartibiga keltiradi.

    Har bir qog'oz varog'i 2 sahifani yonma-yon joylashtiradi. Qaytaradi:
    (oldi_tomoni_pdf_bytes, orqa_tomoni_pdf_bytes, kerakli_varoqlar_soni)
    """
    src = fitz.open(stream=pdf_bytes, filetype="pdf")
    num_pages = src.page_count

    # Booklet uchun sahifalar soni 4ga karrali bo'lishi kerak - kerak bo'lsa oxiriga bo'sh sahifa qo'shamiz
    pad = (4 - num_pages % 4) % 4
    if pad:
        rect = src[0].rect
        for _ in range(pad):
            src.insert_page(-1, width=rect.width, height=rect.height)

    n = src.page_count
    w, h = src[0].rect.width, src[0].rect.height
    sheets = n // 4

    fronts = fitz.open()
    backs = fitz.open()

    for i in range(sheets):
        front_left = n - 2 * i
        front_right = 2 * i + 1
        back_left = 2 * i + 2
        back_right = n - 2 * i - 1

        fp = fronts.new_page(width=2 * w, height=h)
        fp.show_pdf_page(fitz.Rect(0, 0, w, h), src, front_left - 1)
        fp.show_pdf_page(fitz.Rect(w, 0, 2 * w, h), src, front_right - 1)

        bp = backs.new_page(width=2 * w, height=h)
        bp.show_pdf_page(fitz.Rect(0, 0, w, h), src, back_left - 1)
        bp.show_pdf_page(fitz.Rect(w, 0, 2 * w, h), src, back_right - 1)

    return fronts.tobytes(), backs.tobytes(), sheets


async def handle_pdf(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    document = update.message.document
    await update.message.chat.send_action("upload_document")

    file = await context.bot.get_file(document.file_id)
    pdf_bytes = bytes(await file.download_as_bytearray())

    try:
        front_bytes, back_bytes, sheets = build_booklet(pdf_bytes)
    except Exception as e:
        logger.exception("PDF booklet xatosi")
        await update.message.reply_text(f"PDF'ni qayta ishlashda xatolik yuz berdi.\n({e})")
        return

    await update.message.reply_document(
        document=io.BytesIO(front_bytes),
        filename="oldi_tomoni.pdf",
        caption=(
            f"Tayyor! Jami {sheets} varoq qog'oz kerak bo'ladi.\n\n"
            "1) Avval 'oldi_tomoni.pdf'ni chop eting.\n"
            "2) Chop etilgan varoqlarni tartibini buzmasdan, bosilgan tomonini moslab printerga "
            "qayta soling (qaysi tomoni qaratib qo'yishni bitta varoqda sinab ko'ring).\n"
            "3) Keyin 'orqa_tomoni.pdf'ni chop eting.\n"
            "4) Agar orqa tomon teskari/aralash chiqsa, chop etish oynasida \"reverse order\" "
            "(teskari tartibda chop etish) belgisini yoqib qayta urinib ko'ring.\n"
            "5) Varoqlarni o'rtasidan buklab, skrepka bilan qadang."
        ),
    )
    await update.message.reply_document(
        document=io.BytesIO(back_bytes),
        filename="orqa_tomoni.pdf",
    )


async def fallback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text("Iltimos, PDF fayl yuboring.")


def main() -> None:
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.Document.PDF, handle_pdf))
    app.add_handler(MessageHandler(~filters.COMMAND, fallback))

    webhook_base_url = os.environ.get("RENDER_EXTERNAL_URL") or os.environ.get("WEBHOOK_URL")
    port = int(os.environ.get("PORT", 8443))

    if webhook_base_url:
        logger.info("Webhook rejimida ishga tushdi: %s", webhook_base_url)
        app.run_webhook(
            listen="0.0.0.0",
            port=port,
            url_path="webhook",
            webhook_url=f"{webhook_base_url.rstrip('/')}/webhook",
        )
    else:
        logger.info("Polling rejimida (lokal) ishga tushdi...")
        app.run_polling()


if __name__ == "__main__":
    main()
