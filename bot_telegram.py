import logging
import os
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    ContextTypes,
    MessageHandler,
    CommandHandler,
    filters
)
from telegram.error import NetworkError, Forbidden
from ia import responder_pregunta, sanitizar_pregunta
from logging.handlers import RotatingFileHandler

# === CARGAR VARIABLES DE ENTORNO ===
load_dotenv()
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")

# === VALIDAR TOKEN ===
if not TELEGRAM_TOKEN or len(TELEGRAM_TOKEN) < 30:
    raise ValueError("❌ Token de Telegram inválido o no definido en .env.")

# === CONFIGURAR LOGGING ROTATIVO ===
def configurar_logging(nombre_modulo: str):
    logger = logging.getLogger(nombre_modulo)
    if logger.handlers:
        return
    logger.setLevel(logging.INFO)
    formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
    log_filename = f"{nombre_modulo}.log"
    file_handler = RotatingFileHandler(log_filename, maxBytes=5 * 1024 * 1024, backupCount=3)
    file_handler.setFormatter(formatter)
    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)
    logger.addHandler(file_handler)
    logger.addHandler(stream_handler)

configurar_logging("bot_telegram")
logger = logging.getLogger("bot_telegram")

# === COMANDO /start ===
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("👋 ¡Bienvenido! Soy tu asistente de administración de fincas.\n\nEjemplo: ¿Cómo puedo contactar al portero?")

# === MANEJAR MENSAJES ===
async def manejar_mensaje(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        if not update.message or not update.message.text:
            await update.message.reply_text("⚠️ Por favor, envíame un mensaje de texto.")
            return

        mensaje = update.message.text
        mensaje_sanitizado = sanitizar_pregunta(mensaje)

        if len(mensaje_sanitizado) > 500:
            await update.message.reply_text("❌ Pregunta demasiado larga (máx. 500 caracteres).")
            return

        user_id = update.message.from_user.id
        logger.info(f"🧾 Usuario {user_id}: Pregunta -> '{mensaje_sanitizado}'")

        respuesta = responder_pregunta(mensaje_sanitizado, user_id=str(user_id))
        logger.info(f"🤖 Usuario {user_id}: Respuesta -> '{respuesta}'")

        await update.message.reply_text(respuesta)

    except NetworkError as e:
        logger.error(f"🌐 Error de red con Telegram: {e}")
    except Forbidden:
        logger.warning("🚫 El usuario bloqueó al bot.")
    except Exception as e:
        logger.error(f"❌ Error inesperado al responder: {e}")
        await update.message.reply_text("⚠️ Ha ocurrido un error. Intenta nuevamente más tarde.")

# === MAIN ===
def main():
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, manejar_mensaje))
    logger.info("🤖 Bot de Telegram iniciado correctamente.")
    app.run_polling()

if __name__ == "__main__":
    main()
