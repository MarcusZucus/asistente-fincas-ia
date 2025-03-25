# Importamos el módulo centralizado de logging y obtenemos el logger global.
import logger  # Este módulo configura el logging globalmente al importarse.
from logger import get_logger

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

# =============================================================================
# CARGA DE VARIABLES DE ENTORNO
# =============================================================================
load_dotenv()
TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
WEBHOOK_URL = os.getenv(
    "WEBHOOK_URL", "https://asistente-fincas-ia-production.up.railway.app"
)
PORT = int(os.getenv("PORT", 8443))  # El puerto se define en la variable PORT (por Railway u otro servicio)

# =============================================================================
# VALIDACIÓN DE TOKEN
# =============================================================================
if not TELEGRAM_TOKEN or len(TELEGRAM_TOKEN) < 30:
    raise ValueError("❌ Token de Telegram inválido o no definido en .env.")

# =============================================================================
# OBTENCIÓN DEL LOGGER GLOBAL
# =============================================================================
logger = get_logger("bot_telegram")
logger.info("Logger global cargado correctamente. Iniciando bot de Telegram...")

# =============================================================================
# DEFINICIÓN DEL COMANDO /start
# =============================================================================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Función para responder al comando /start.
    Envía un mensaje de bienvenida con un ejemplo de uso.
    """
    mensaje_bienvenida = (
        "👋 ¡Bienvenido! Soy tu asistente de administración de fincas.\n\n"
        "Ejemplo: ¿Cómo puedo contactar al portero?"
    )
    await update.message.reply_text(mensaje_bienvenida)
    logger.debug("Se ha enviado el mensaje de bienvenida al usuario.")

# =============================================================================
# MANEJO DE MENSAJES
# =============================================================================
async def manejar_mensaje(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Función que procesa los mensajes recibidos. Se encarga de:
      - Validar que el mensaje sea de texto.
      - Sanitizar la pregunta.
      - Validar la longitud de la entrada.
      - Loggear la pregunta y la respuesta.
      - Enviar la respuesta generada por la función responder_pregunta.
    """
    try:
        # Validar que se reciba un mensaje de texto
        if not update.message or not update.message.text:
            await update.message.reply_text("⚠️ Por favor, envíame un mensaje de texto.")
            logger.warning("Mensaje recibido sin contenido de texto; se envía aviso al usuario.")
            return

        mensaje = update.message.text
        mensaje_sanitizado = sanitizar_pregunta(mensaje)

        # Validación de longitud del mensaje
        if len(mensaje_sanitizado) > 500:
            await update.message.reply_text("❌ Pregunta demasiado larga (máx. 500 caracteres).")
            logger.warning("Mensaje demasiado largo; usuario informado de la restricción.")
            return

        user_id = update.message.from_user.id
        logger.info(f"🧾 Usuario {user_id}: Pregunta -> '{mensaje_sanitizado}'")

        # Procesar la respuesta mediante el sistema de IA
        respuesta = responder_pregunta(mensaje_sanitizado, user_id=str(user_id))
        logger.info(f"🤖 Usuario {user_id}: Respuesta -> '{respuesta}'")

        # Responder al usuario en Telegram
        await update.message.reply_text(respuesta)
        logger.debug(f"Respuesta enviada al usuario {user_id} correctamente.")

    except NetworkError as e:
        logger.error(f"🌐 Error de red con Telegram: {e}", exc_info=True)
    except Forbidden:
        logger.warning("🚫 El usuario bloqueó al bot; se omite el envío de respuesta.")
    except Exception as e:
        logger.error(f"❌ Error inesperado al procesar el mensaje: {e}", exc_info=True)
        await update.message.reply_text("⚠️ Ha ocurrido un error. Intenta nuevamente más tarde.")

# =============================================================================
# FUNCIÓN PRINCIPAL: CONFIGURACIÓN Y ARRANQUE DEL BOT
# =============================================================================
def main():
    """
    Función principal que:
      - Crea la aplicación de Telegram usando el token.
      - Registra los handlers para el comando /start y mensajes de texto.
      - Configura y activa el webhook en el puerto y URL definidos.
    """
    try:
        app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
        app.add_handler(CommandHandler("start", start))
        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, manejar_mensaje))
        logger.info("🤖 Bot de Telegram configurado correctamente. Preparando arranque del webhook.")

        # Iniciar el webhook para recibir actualizaciones de Telegram
        app.run_webhook(
            listen="0.0.0.0",
            port=PORT,
            url_path=TELEGRAM_TOKEN,  # Se utiliza el token como parte de la ruta por seguridad
            webhook_url=f"{WEBHOOK_URL}/{TELEGRAM_TOKEN}"
        )
    except Exception as e:
        logger.critical(f"❌ Error crítico al iniciar el bot: {e}", exc_info=True)
        raise

# =============================================================================
# EJECUCIÓN DIRECTA DEL SCRIPT
# =============================================================================
if __name__ == "__main__":
    main()
