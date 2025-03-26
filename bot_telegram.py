"""
bot_telegram.py - Módulo del Bot de Telegram para el Sistema de Administración de Fincas

Este módulo se encarga de configurar y ejecutar el bot de Telegram que interactúa con el usuario.
Las funcionalidades principales son:

  1. Cargar las variables de entorno necesarias (TOKEN de Telegram, URL del webhook, puerto, etc.).
  2. Configurar la aplicación de Telegram utilizando la librería python-telegram-bot.
  3. Definir los handlers:
      - /start: Envía un mensaje de bienvenida e instruye al usuario a ingresar su número de teléfono.
      - Número de teléfono: Valida que el mensaje contenga solo dígitos y lo utiliza para autenticar al usuario.
      - Manejador de mensajes: Una vez autenticado, procesa las preguntas del usuario, las sanitiza,
         invoca la función de respuesta de IA (responder_pregunta) y envía la respuesta.
  4. Manejo robusto de errores: Se capturan y registran errores de red, bloqueos y excepciones inesperadas.
  5. Integración completa con el sistema centralizado de logging y configuración.
  
Esta versión está actualizada y diseñada para simplificar la autenticación: solo se pide el número de teléfono.
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

# Importar las funciones de IA que permiten responder a las preguntas del usuario y sanitizar la entrada
from ia import responder_pregunta, sanitizar_pregunta

# Importar la función para identificar al usuario por número
from numero import identificar_usuario_por_numero

# Importar el sistema de logging centralizado para mantener la coherencia en la salida de logs
from logger import get_logger

# Cargar variables de entorno para asegurar que las claves, URL y puertos estén disponibles.
load_dotenv()

# --- CONFIGURACIÓN DE VARIABLES GLOBALES DEL BOT ---
TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
WEBHOOK_URL = os.getenv("WEBHOOK_URL", "https://asistente-fincas-ia-production.up.railway.app")
PORT = int(os.getenv("PORT", 8443))

# Validación de la variable del token: debe existir y tener una longitud adecuada para garantizar su validez.
if not TELEGRAM_TOKEN or len(TELEGRAM_TOKEN) < 30:
    raise ValueError("Token de Telegram inválido o no definido en .env.")

# Inicialización del logger específico para este módulo, utilizando el sistema centralizado.
logger = get_logger("bot_telegram")
logger.info("Logger global cargado correctamente en bot_telegram.")

# --- DEFINICIÓN DE HANDLERS DEL BOT ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Handler para el comando /start.

    Esta función se invoca cuando un usuario envía el comando /start.
    Envía un mensaje de bienvenida e instruye al usuario a ingresar su número de teléfono para autenticarse.

    Args:
      update (Update): Objeto que contiene la información de la actualización recibida de Telegram.
      context (ContextTypes.DEFAULT_TYPE): Contexto de la aplicación, usado para pasar información adicional.
    """
    mensaje_bienvenida = (
        "¡Bienvenido! Soy tu asistente de administración de fincas.\n\n"
        "Para continuar, por favor ingresa tu número de teléfono tal como aparece en nuestros registros.\n"
        "Ejemplo: 1234567890\n\n"
        "Una vez autenticado, podrás hacer preguntas relacionadas con la gestión de fincas."
    )
    await update.message.reply_text(mensaje_bienvenida)
    logger.debug("Mensaje de bienvenida enviado a través del comando /start.")

async def numero_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Handler para procesar el número de teléfono enviado por el usuario.

    Se utiliza la función identificar_usuario_por_numero para autenticar al usuario.
    Si el número es válido y se encuentra en la base de datos, se guarda la información del usuario en context.user_data.
    """
    numero = update.message.text.strip()
    if not numero.isdigit():
        await update.message.reply_text("Formato inválido. Ingresa solo números de teléfono.")
        return

    try:
        usuario = identificar_usuario_por_numero(numero)
        if usuario:
            context.user_data["user"] = usuario
            await update.message.reply_text(
                f"¡Hola {usuario.get('nombre', 'usuario')}! Te has autenticado correctamente."
            )
            logger.info(f"Usuario con número {numero} autenticado exitosamente.")
        else:
            await update.message.reply_text("Número no encontrado. Por favor, intenta nuevamente.")
            logger.warning(f"Número {numero} no encontrado en la base de datos.")
    except Exception as e:
        logger.error(f"Error durante la autenticación por número: {e}", exc_info=True)
        await update.message.reply_text("Error en la autenticación. Intenta nuevamente.")

async def manejar_mensaje(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Handler para procesar los mensajes de texto que no sean comandos.

    Funcionalidades:
      - Verifica si el usuario está autenticado (si no, se solicita ingresar el número de teléfono).
      - Valida que el mensaje tenga contenido de texto.
      - Sanitiza la entrada para eliminar caracteres no permitidos.
      - Verifica que la longitud del mensaje sanitizado no exceda los límites permitidos (500 caracteres).
      - Registra la pregunta del usuario junto con su ID.
      - Invoca la función responder_pregunta del módulo de IA para generar una respuesta.
      - Envía la respuesta generada al usuario.

    Args:
      update (Update): Objeto de actualización de Telegram que contiene la información del mensaje.
      context (ContextTypes.DEFAULT_TYPE): Contexto de la aplicación.
    """
    try:
        if "user" not in context.user_data:
            await update.message.reply_text("Debes autenticarte ingresando tu número de teléfono para continuar.")
            logger.warning("Usuario no autenticado intentando enviar mensajes.")
            return

        if not update.message or not update.message.text:
            await update.message.reply_text("Por favor, envíame un mensaje de texto.")
            logger.warning("Mensaje sin contenido de texto recibido; se ha solicitado enviar texto.")
            return

        mensaje = update.message.text
        mensaje_sanitizado = sanitizar_pregunta(mensaje)
        if len(mensaje_sanitizado) > 500:
            await update.message.reply_text("Pregunta demasiado larga (máx. 500 caracteres).")
            logger.warning("Mensaje demasiado largo recibido; se ha informado al usuario sobre el límite.")
            return

        user_id = update.message.from_user.id
        logger.info(f"Usuario {user_id}: Pregunta recibida -> '{mensaje_sanitizado}'")
        respuesta = responder_pregunta(mensaje_sanitizado, user_id=str(user_id))
        logger.info(f"Usuario {user_id}: Respuesta generada -> '{respuesta}'")
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

    Proceso:
      - Se crea una instancia de ApplicationBuilder usando el token de Telegram.
      - Se registran los handlers para el comando /start, la autenticación (número de teléfono) y el manejo de preguntas.
      - Se registra en el log que el bot ha sido configurado correctamente.

    Returns:
      Application: La instancia de la aplicación configurada, lista para iniciarse.
    """
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()

    # Registrar el handler para el comando /start
    app.add_handler(CommandHandler("start", start))

    # Registrar el handler para la autenticación: se espera un mensaje que contenga solo números
    app.add_handler(MessageHandler(filters.TEXT & filters.Regex("^[0-9]+$"), numero_handler))

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
