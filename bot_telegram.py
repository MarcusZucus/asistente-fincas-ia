"""
bot_telegram.py - Módulo del Bot de Telegram para el Sistema de Administración de Fincas

Este módulo se encarga de configurar y ejecutar el bot de Telegram que interactúa con el usuario.
Las funcionalidades principales son:

  1. Cargar las variables de entorno necesarias (TOKEN de Telegram, URL del webhook, puerto, etc.).
  2. Configurar la aplicación de Telegram utilizando la librería python-telegram-bot.
  3. Definir los handlers:
      - /start: Envía un mensaje de bienvenida y autentica automáticamente al usuario usando el id fijo.
      - Manejador de mensajes: Una vez autenticado, procesa las preguntas del usuario, las sanitiza,
         invoca la función de respuesta de IA (responder_pregunta) y envía la respuesta.
  4. Manejo robusto de errores: Se capturan y registran errores de red, bloqueos y excepciones inesperadas.
  5. Integración completa con el sistema centralizado de logging y configuración.
  
Esta versión está actualizada para trabajar con el nuevo auth, que asume que quien habla es el usuario con id fijo.
"""

import logging
import os
from dotenv import load_dotenv

# Importación de clases y funciones de la librería python-telegram-bot
from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    ContextTypes,
    MessageHandler,
    CommandHandler,
    filters
)
from telegram.error import NetworkError, Forbidden

# Importar las funciones de IA para responder preguntas y sanitizar el texto
from ia import responder_pregunta, sanitizar_pregunta

# Importar la función de autenticación por defecto (usuario fijo) del módulo auth
from auth import authenticate_default

# Importar el sistema de logging centralizado
from logger import get_logger

# Cargar variables de entorno
load_dotenv()

# --- CONFIGURACIÓN DE VARIABLES GLOBALES DEL BOT ---
TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
WEBHOOK_URL = os.getenv("WEBHOOK_URL", "https://asistente-fincas-ia-production.up.railway.app")
PORT = int(os.getenv("PORT", 8443))

# Validar el token
if not TELEGRAM_TOKEN or len(TELEGRAM_TOKEN) < 30:
    raise ValueError("Token de Telegram inválido o no definido en .env.")

# Inicializar el logger para este módulo
logger = get_logger("bot_telegram")
logger.info("Logger global cargado correctamente en bot_telegram.")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Handler para el comando /start.

    Al recibir /start, se autentica automáticamente el usuario usando el método por defecto,
    y se informa al usuario que ya está autenticado.
    """
    try:
        token, user = authenticate_default()
        context.user_data["token"] = token
        context.user_data["user"] = user
        mensaje_bienvenida = (
            f"¡Bienvenido! Te has autenticado automáticamente como {user.get('nombre', 'usuario')}.\n\n"
            "Ahora puedes hacer preguntas relacionadas con la gestión de fincas."
        )
        await update.message.reply_text(mensaje_bienvenida)
        logger.info(f"Usuario autenticado por defecto: {user.get('nombre', user['id'])}")
    except Exception as e:
        logger.error(f"Error durante la autenticación por defecto: {e}", exc_info=True)
        await update.message.reply_text("Error durante la autenticación. Intenta más tarde.")

async def manejar_mensaje(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Handler para procesar los mensajes de texto que no sean comandos.

    Si el usuario está autenticado, se sanitiza y procesa la pregunta,
    se obtiene la respuesta de IA y se envía al usuario.
    """
    try:
        if "user" not in context.user_data:
            await update.message.reply_text("Debes iniciar sesión con /start para continuar.")
            logger.warning("Usuario no autenticado intentando enviar mensajes.")
            return

        if not update.message or not update.message.text:
            await update.message.reply_text("Por favor, envíame un mensaje de texto.")
            logger.warning("Mensaje sin contenido recibido.")
            return

        mensaje = update.message.text
        mensaje_sanitizado = sanitizar_pregunta(mensaje)
        if len(mensaje_sanitizado) > 500:
            await update.message.reply_text("Pregunta demasiado larga (máx. 500 caracteres).")
            logger.warning("Mensaje demasiado largo recibido.")
            return

        user_id = update.message.from_user.id
        logger.info(f"Usuario {user_id}: Pregunta recibida -> '{mensaje_sanitizado}'")
        respuesta = responder_pregunta(mensaje_sanitizado, user_id=str(user_id))
        await update.message.reply_text(respuesta)
        logger.debug(f"Respuesta enviada correctamente al usuario {user_id}.")
    except NetworkError as e:
        logger.error(f"Error de red con Telegram: {e}", exc_info=True)
    except Forbidden:
        logger.warning("El usuario ha bloqueado al bot; omitiendo respuesta.")
    except Exception as e:
        logger.error(f"Error inesperado al procesar el mensaje: {e}", exc_info=True)
        await update.message.reply_text("Ha ocurrido un error. Intenta más tarde.")

def build_bot():
    """
    Configura y retorna la aplicación del bot de Telegram lista para ejecutarse.

    Se registran los handlers para el comando /start y para el manejo de mensajes.
    """
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()

    # Registrar el handler para el comando /start
    app.add_handler(CommandHandler("start", start))

    # Registrar el handler para mensajes de texto (preguntas) que no sean comandos
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, manejar_mensaje))

    logger.info("Bot de Telegram configurado correctamente en build_bot().")
    return app

# Ejecución directa del módulo para pruebas locales
if __name__ == "__main__":
    logger.info("Iniciando ejecución directa de bot_telegram.py para pruebas locales.")
    try:
        bot_app = build_bot()
        logger.info("Bot iniciado en modo polling. Presiona Ctrl+C para detener.")
        bot_app.run_polling()
    except Exception as e:
        logger.error(f"Error crítico durante la ejecución del bot: {e}", exc_info=True)
