"""
bot_telegram.py - Módulo del Bot de Telegram para el Sistema de Administración de Fincas

Este módulo se encarga de configurar y ejecutar el bot de Telegram que interactúa con el usuario.
Las funcionalidades principales son:

  1. Cargar las variables de entorno necesarias (TOKEN de Telegram, URL del webhook, puerto, etc.).
  2. Configurar la aplicación de Telegram utilizando la librería python-telegram-bot.
  3. Definir los handlers:
      - /start: Envia un mensaje de bienvenida al usuario.
      - Manejador de mensajes: Procesa los mensajes de texto, valida la entrada, sanitiza la pregunta,
        invoca la función de respuesta de IA (responder_pregunta) y envía la respuesta al usuario.
  4. Manejo robusto de errores: Se capturan y registran errores de red, bloqueos y excepciones inesperadas.
  5. Integración completa con el sistema centralizado de logging y configuración.
  
Esta versión está diseñada para ser hiper robusta, completamente documentada y coherente con 
el resto de los módulos (por ejemplo, config.py, logger.py, ia.py), sin redundancias ni inconsistencias.
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

# Importar la función de autenticación para validar credenciales
from auth import authenticate

# Importar el sistema de logging centralizado para mantener la coherencia en la salida de logs
from logger import get_logger

# Cargar variables de entorno para asegurar que las claves, URL y puertos estén disponibles.
# Se utiliza dotenv para cargar un archivo .env, permitiendo configurar localmente en entornos de desarrollo.
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

# Este diccionario o el propio context.user_data servirá para llevar el estado de autenticación de cada usuario.
# En este ejemplo, usaremos context.user_data en lugar de un diccionario global.
# usuarios_autenticados = {}

# --- DEFINICIÓN DE HANDLERS DEL BOT ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Handler para el comando /start.

    Esta función se invoca cuando un usuario envía el comando /start.
    Envía un mensaje de bienvenida personalizado y registra en el log la acción.

    Además, se pide al usuario que ingrese sus credenciales en formato usuario:contraseña.
    
    Args:
      update (Update): Objeto que contiene la información de la actualización recibida de Telegram.
      context (ContextTypes.DEFAULT_TYPE): Contexto de la aplicación, usado para pasar información adicional.
    
    Ejemplo:
      Cuando el usuario envía "/start", se envía un mensaje de bienvenida y se registra el evento.
    """
    mensaje_bienvenida = (
        "üëã ¡Bienvenido! Soy tu asistente de administración de fincas.\n\n"
        "Para continuar, ingresa tus credenciales en formato:\n"
        "usuario:contraseña\n\n"
        "Ejemplo: marco:MiPassword123\n\n"
        "Después de autenticado, puedes hacer preguntas como:\n"
        "¬øCómo puedo contactar al portero?"
    )
    # Enviar el mensaje de bienvenida utilizando el método reply_text
    await update.message.reply_text(mensaje_bienvenida)
    logger.debug("Mensaje de bienvenida enviado a través del comando /start.")

async def credenciales_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Handler para procesar credenciales en formato usuario:contraseña.

    Si la autenticación es exitosa, se almacena la información en context.user_data
    para indicar que el usuario está autenticado.
    """
    mensaje = update.message.text.strip()
    if ":" not in mensaje:
        await update.message.reply_text("Formato inválido. Ingresa usuario:contraseña.")
        return

    try:
        usuario, contrasena = mensaje.split(":", 1)
        usuario = usuario.strip()
        contrasena = contrasena.strip()

        token, user_data = authenticate(usuario, contrasena)
        context.user_data["token"] = token
        context.user_data["user"] = user_data

        await update.message.reply_text(
            f"¡Hola {user_data.get('nombre', 'usuario')}! Te has autenticado correctamente."
        )
        logger.info(f"Usuario '{usuario}' autenticado exitosamente.")

    except Exception as e:
        logger.error(f"Error durante la autenticación: {e}", exc_info=True)
        await update.message.reply_text("Credenciales inválidas. Intenta nuevamente.")

async def manejar_mensaje(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Handler para procesar los mensajes de texto que no sean comandos.

    Funcionalidades:
      - Verifica si el usuario está autenticado (si no, se solicita ingresar credenciales).
      - Valida que el mensaje tenga contenido de texto.
      - Sanitiza la entrada para eliminar caracteres no permitidos.
      - Verifica que la longitud del mensaje sanitizado no exceda los límites permitidos (500 caracteres).
      - Registra la pregunta del usuario junto con su ID.
      - Invoca la función responder_pregunta del módulo de IA para generar una respuesta.
      - Envía la respuesta generada al usuario.
    
    Args:
      update (Update): Objeto de actualización de Telegram que contiene la información del mensaje.
      context (ContextTypes.DEFAULT_TYPE): Contexto de la aplicación.
    
    Ejemplo:
      Si un usuario envía un mensaje de texto, la función lo procesa, sanitiza y responde con la información
      proporcionada por el módulo de IA, registrando el proceso en el log.
    """
    try:
        # Verificar si el usuario ya está autenticado
        if "token" not in context.user_data:
            await update.message.reply_text("Debes ingresar credenciales en formato usuario:contraseña para continuar.")
            logger.warning("Usuario no autenticado intentando enviar mensajes.")
            return

        # Validar que el mensaje recibido contenga texto
        if not update.message or not update.message.text:
            await update.message.reply_text("Por favor, envíame un mensaje de texto.")
            logger.warning("Mensaje sin contenido de texto recibido; se ha solicitado enviar texto.")
            return

        # Extraer el contenido del mensaje
        mensaje = update.message.text
        # Sanitizar el mensaje eliminando caracteres no permitidos y recortándolo a 500 caracteres máximo
        mensaje_sanitizado = sanitizar_pregunta(mensaje)
        if len(mensaje_sanitizado) > 500:
            await update.message.reply_text("Pregunta demasiado larga (máx. 500 caracteres).")
            logger.warning("Mensaje demasiado largo recibido; se ha informado al usuario sobre el límite.")
            return

        # Obtener el identificador del usuario que envió el mensaje
        user_id = update.message.from_user.id
        logger.info(f"Usuario {user_id}: Pregunta recibida -> '{mensaje_sanitizado}'")

        # Llamar a la función de IA para obtener una respuesta basada en la pregunta sanitizada
        respuesta = responder_pregunta(mensaje_sanitizado, user_id=str(user_id))
        logger.info(f"Usuario {user_id}: Respuesta generada -> '{respuesta}'")

        # Enviar la respuesta generada al usuario
        await update.message.reply_text(respuesta)
        logger.debug(f"Respuesta enviada correctamente al usuario {user_id}.")

    except NetworkError as e:
        # Capturar errores de red, que pueden ocurrir si hay problemas de conectividad entre Telegram y el servidor
        logger.error(f"Error de red con Telegram: {e}", exc_info=True)
    except Forbidden:
        # Capturar la excepción Forbidden, que puede ocurrir si el usuario ha bloqueado al bot
        logger.warning("El usuario ha bloqueado al bot; omitiendo respuesta.")
    except Exception as e:
        # Capturar cualquier otra excepción inesperada y notificar al usuario de un error general
        logger.error(f"Error inesperado al procesar el mensaje: {e}", exc_info=True)
        await update.message.reply_text("Ha ocurrido un error. Intenta más tarde.")

def build_bot():
    """
    Configura y retorna la aplicación del bot de Telegram lista para ejecutarse.

    Proceso:
      - Se crea una instancia de ApplicationBuilder usando el token de Telegram.
      - Se registran los handlers para los comandos y mensajes de texto.
      - Se registra en el log que el bot ha sido configurado correctamente.
    
    Returns:
      Application: La instancia de la aplicación configurada, lista para iniciarse.
    
    Ejemplo:
      >>> app = build_bot()
      >>> app.run_polling()
    """
    # Crear la aplicación de Telegram utilizando el token configurado
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()

    # Registrar el handler para el comando /start
    app.add_handler(CommandHandler("start", start))

    # Registrar el handler para credenciales (texto con ':')
    app.add_handler(MessageHandler(filters.TEXT & filters.Regex(":"), credenciales_handler))

    # Registrar el handler para mensajes de texto que no sean comandos ni credenciales
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, manejar_mensaje))

    logger.info("Bot de Telegram configurado correctamente en build_bot().")
    return app

# Esta sección de código se ejecuta solo si se corre este módulo de forma directa.
if __name__ == "__main__":
    logger.info("Iniciando ejecución directa de bot_telegram.py para pruebas locales.")
    try:
        # Construir la aplicación del bot
        bot_app = build_bot()
        # Iniciar el bot en modo polling (alternativamente se puede iniciar en webhook según la configuración)
        logger.info("Bot iniciado en modo polling. Presiona Ctrl+C para detener.")
        bot_app.run_polling()
    except Exception as e:
        logger.error(f"Error crítico durante la ejecución del bot: {e}", exc_info=True)
