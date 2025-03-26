"""
main.py - Punto de entrada de la aplicación principal del bot de Telegram

Este módulo se encarga de:
  - Cargar de forma temprana las variables de entorno mediante dotenv.
  - Inicializar y configurar el logger centralizado.
  - Construir la aplicación del bot a partir del módulo 'bot_telegram'.
  - Configurar e iniciar el webhook para la comunicación segura con Telegram.
  - Gestionar errores críticos durante el inicio de la aplicación.
  
La configuración del webhook se realiza utilizando el token de Telegram como parte de la URL,
lo que añade una capa extra de seguridad. El módulo está pensado para integrarse de forma
coherente con el resto del sistema (por ejemplo, config, logger, bot_telegram) sin redundancias ni
inconsistencias.
"""

import os
from dotenv import load_dotenv
from bot_telegram import build_bot
from logger import get_logger

# Cargar variables de entorno tan pronto como se importe el módulo.
# Esto garantiza que todas las configuraciones necesarias (como TELEGRAM_BOT_TOKEN, WEBHOOK_URL, PORT, etc.)
# estén disponibles para el resto de la aplicación.
load_dotenv()

# Inicializar el logger para este módulo, asegurando que todas las salidas de log sean consistentes con el sistema.
logger = get_logger("main")
logger.info("Iniciando aplicación principal...")

def main():
    """
    Función principal que orquesta el arranque del bot de Telegram utilizando webhooks.
    
    Proceso detallado:
      1. Se construye la aplicación del bot llamando a 'build_bot()' del módulo 'bot_telegram'.
      2. Se cargan y validan las variables de entorno necesarias: TELEGRAM_BOT_TOKEN, WEBHOOK_URL y PORT.
      3. Se registra en el log el inicio de la configuración del webhook.
      4. Se inicia el webhook con la configuración:
         - 'listen': Se utiliza "0.0.0.0" para escuchar en todas las interfaces de red.
         - 'port': Se extrae del entorno, con un valor de 8080.
         - 'url_path': Se utiliza el token de Telegram como parte de la ruta, para mejorar la seguridad.
         - 'webhook_url': Se construye concatenando la URL base y el token.
      5. En caso de error, se captura y se registra el error crítico y se vuelve a lanzar la excepción para
         detener la aplicación.
    
    Raises:
      Exception: Cualquier error que ocurra durante la configuración o el arranque del webhook.
    
    Ejemplo de uso:
      Al ejecutar este módulo directamente, se iniciará el bot en modo webhook.
    """
    try:
        # Construir la aplicación del bot utilizando el módulo 'bot_telegram'
        app = build_bot()
        logger.debug("Aplicación del bot construida exitosamente a partir de build_bot().")

        # Cargar y validar las variables de entorno específicas para Telegram
        TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
        WEBHOOK_URL = os.getenv("WEBHOOK_URL", "https://asistente-fincas-ia-production.up.railway.app")
        PORT = int(os.getenv("PORT", 8080))

        # Validar que el token de Telegram esté correctamente configurado
        if not TELEGRAM_TOKEN or len(TELEGRAM_TOKEN) < 30:
            logger.critical("Token de Telegram inválido o no definido en .env.")
            raise ValueError("Token de Telegram inválido o no definido en .env.")

        logger.info("Preparando arranque del webhook en main.py...")
        logger.debug(f"Variables de entorno: TELEGRAM_TOKEN=[{TELEGRAM_TOKEN[:4]}...{TELEGRAM_TOKEN[-4:]}], WEBHOOK_URL={WEBHOOK_URL}, PORT={PORT}")

        # Configurar e iniciar el webhook para que Telegram se comunique de forma segura
        # Se utiliza el token como parte de la ruta del webhook por motivos de seguridad.
        app.run_webhook(
            listen="0.0.0.0",           # Escucha en todas las interfaces de red
            port=PORT,                  # Puerto en el que se ejecutará el servidor del webhook
            url_path=TELEGRAM_TOKEN,    # El token se utiliza como parte de la URL para mayor seguridad
            webhook_url=f"{WEBHOOK_URL}/{TELEGRAM_TOKEN}"  # URL completa del webhook
        )

    except Exception as e:
        # Registrar un error crítico si falla el arranque del bot, incluyendo la traza completa de la excepción
        logger.critical(f"Error crítico al iniciar el bot: {e}", exc_info=True)
        # Relanzar la excepción para detener la aplicación en caso de fallo crítico
        raise

# Punto de entrada del módulo principal
if __name__ == "__main__":
    main()
