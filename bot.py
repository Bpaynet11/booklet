import os
import io
import gc
import asyncio
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
    fronts = None
    backs = None
    try:
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

        front_bytes = fronts.tobytes(garbage=4, deflate=True, clean=True)
        back_bytes = backs.tobytes(garbage=4, deflate=True, clean=True)
    finally:
        # Xotirani darrov bo'shatish uchun barcha PDF obyektlarini majburan yopamiz -
        # bo'lmasa keyingi so'rovda xotira to'lib, bot "qotib qolishi" mumkin.
        src.close()
        if fronts is not None:
            fronts.close()
        if backs is not None:
            backs.close()
        gc.collect()

    return front_bytes, back_bytes, sheets


async def handle_pdf(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    document = update.message.document
    await update.message.chat.send_action("upload_document")

    if document.file_size and document.file_size > 25 * 1024 * 1024:
        await update.message.reply_text(
            "Bu fayl juda katta (25MB dan ortiq) - bepul serverda ishlamasligi mumkin. "
            "Kichikroq fayl bilan urinib ko'ring."
        )
        return

    status_msg = await update.message.reply_text(
        "PDF qabul qilindi, qayta ishlanmoqda... Katta fayllar uchun bir necha daqiqa vaqt ketishi mumkin, kuting."
    )

    file = await context.bot.get_file(document.file_id)
    pdf_bytes = bytes(await file.download_as_bytearray())

    loop = asyncio.get_running_loop()
    try:
        # build_booklet protsessorni band qiladigan og'ir ish - shu sababli alohida oqimda
        # (thread) ishga tushiramiz, shunda bot shu vaqtda boshqa xabarlarga ham javob berishi mumkin.
        front_bytes, back_bytes, sheets = await loop.run_in_executor(None, build_booklet, pdf_bytes)
    except Exception as e:
        logger.exception("PDF booklet xatosi")
        await status_msg.edit_text(f"PDF'ni qayta ishlashda xatolik yuz berdi.\n({e})")
        return

    front_mb = len(front_bytes) / (1024 * 1024)
    back_mb = len(back_bytes) / (1024 * 1024)

    if front_mb > 45 or back_mb > 45:
        await status_msg.edit_text(
            f"Tayyor bo'ldi, lekin natija fayli juda katta chiqdi ({front_mb:.0f}MB / {back_mb:.0f}MB) - "
            "Telegram orqali yuborib bo'lmaydi. Kichikroq yoki kamroq sahifali PDF bilan urinib ko'ring."
        )
        return

    await status_msg.edit_text(f"Tayyor! Jami {sheets} varoq qog'oz kerak bo'ladi. Fayllar yuborilmoqda...")

    try:
        await update.message.reply_document(
            document=io.BytesIO(front_bytes),
            filename="oldi_tomoni.pdf",
            caption=(
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
    except Exception as e:
        logger.exception("Fayl yuborishda xato")
        await update.message.reply_text(
            f"Fayllarni yuborishda xatolik yuz berdi (ehtimol internet yoki fayl hajmi sababli). "
            f"Qaytadan urinib ko'ring.\n({e})"
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
