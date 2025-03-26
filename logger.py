import logging
import logging.config
import os
import sys

# Opcional: integración con Sentry para capturar errores críticos en producción.
try:
    import sentry_sdk
    from sentry_sdk.integrations.logging import LoggingIntegration
except ImportError:
    sentry_sdk = None

def setup_logging(default_level=logging.DEBUG):
    """
    Configura el logging para todo el proyecto con:
      - Console handler (captura DEBUG y superiores).
      - RotatingFileHandler para logs generales (archivo 'project.log').
      - RotatingFileHandler exclusivo para errores (archivo 'error.log').
      - Opción de enviar logs de error a Sentry si se configura SENTRY_DSN.
      - Formateo detallado con timestamp, nivel, nombre del logger y número de línea.
      - Captura global de excepciones no controladas para registrar errores inesperados.
      
    La configuración se adapta al entorno (por ejemplo, desarrollo vs producción) y permite 
    extender fácilmente la salida de logs para monitorear conexiones, autenticación, Supabase, etc.
    """
    # Determinar nivel de logging según el entorno
    entorno = os.getenv("ENTORNO", "desarrollo").lower()
    if entorno in ["desarrollo", "dev"]:
        default_level = logging.DEBUG
    else:
        default_level = logging.INFO

    # Configurar Sentry si se proporciona un DSN y se tiene instalada la librería
    sentry_dsn = os.getenv("SENTRY_DSN")
    if sentry_dsn and sentry_sdk:
        # Captura los logs de nivel INFO en adelante como breadcrumbs y errores como eventos.
        sentry_logging = LoggingIntegration(
            level=logging.INFO,
            event_level=logging.ERROR
        )
        sentry_sdk.init(
            dsn=sentry_dsn,
            integrations=[sentry_logging]
        )
    
    # Crear directorio para logs si no existe
    log_dir = "logs"
    if not os.path.exists(log_dir):
        os.makedirs(log_dir)

    # Definir formateadores
    formatters = {
        'detailed': {
            'format': '%(asctime)s [%(levelname)s] [%(name)s:%(lineno)d] %(message)s',
            'datefmt': '%Y-%m-%d %H:%M:%S'
        },
        'simple': {
            'format': '%(asctime)s [%(levelname)s] %(message)s',
            'datefmt': '%Y-%m-%d %H:%M:%S'
        },
    }

    # Definir manejadores básicos
    handlers = {
        'console': {
            'class': 'logging.StreamHandler',
            'level': 'DEBUG',
            'formatter': 'simple',
            'stream': 'ext://sys.stdout'
        },
        'file_all': {
            'class': 'logging.handlers.RotatingFileHandler',
            'level': 'DEBUG',
            'formatter': 'detailed',
            'filename': os.path.join(log_dir, 'project.log'),
            'maxBytes': 10 * 1024 * 1024,  # 10 MB
            'backupCount': 5,
            'encoding': 'utf8'
        },
        'file_error': {
            'class': 'logging.handlers.RotatingFileHandler',
            'level': 'ERROR',
            'formatter': 'detailed',
            'filename': os.path.join(log_dir, 'error.log'),
            'maxBytes': 10 * 1024 * 1024,
            'backupCount': 5,
            'encoding': 'utf8'
        },
    }

    # Agregar un handler de Sentry si está configurado
    if sentry_dsn and sentry_sdk:
        handlers['sentry'] = {
            'class': 'sentry_sdk.integrations.logging.EventHandler',
            'level': 'ERROR'
        }

    # Configuración del diccionario de logging
    logging_config = {
        'version': 1,
        'disable_existing_loggers': False,  # Mantiene loggers ya configurados
        'formatters': formatters,
        'handlers': handlers,
        'root': {
            'handlers': ['console', 'file_all', 'file_error'] + (['sentry'] if 'sentry' in handlers else []),
            'level': default_level,
        },
        'loggers': {
            # Logger específico para Supabase (útil si la librería supabase utiliza logging)
            'supabase': {
                'handlers': ['console', 'file_all'],
                'level': 'DEBUG',
                'propagate': False
            },
            # Otros loggers específicos se pueden agregar aquí...
        }
    }

    logging.config.dictConfig(logging_config)

    # Captura global de excepciones no controladas para que Railway (y Sentry) las registre
    def handle_exception(exc_type, exc_value, exc_traceback):
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc_value, exc_traceback)
            return
        logging.critical("Excepción no controlada", exc_info=(exc_type, exc_value, exc_traceback))

    sys.excepthook = handle_exception

# Configurar el logging al importar el módulo
setup_logging()

def get_logger(name: str = None) -> logging.Logger:
    """
    Devuelve un logger configurado globalmente. Si se especifica un nombre,
    se devuelve el logger correspondiente; de lo contrario, se retorna el logger raíz.
    
    Args:
      name (str, opcional): Nombre del logger deseado.
    
    Returns:
      logging.Logger: Instancia del logger configurado.
    """
    return logging.getLogger(name)

# Ejemplo de uso y pruebas (se ejecuta solo cuando se corre este módulo directamente)
if __name__ == "__main__":
    logger = get_logger(__name__)
    
    logger.debug("Mensaje DEBUG: trazas internas y variables.")
    logger.info("Mensaje INFO: progreso de la aplicación.")
    logger.warning("Mensaje WARNING: advertencia sobre posibles problemas.")
    logger.error("Mensaje ERROR: se ha producido un error.")
    logger.critical("Mensaje CRITICAL: error grave, requiere atención inmediata.")

    # Simulación de excepción controlada
    try:
        result = 1 / 0
    except Exception:
        logger.exception("Ocurrió una excepción al dividir por cero.")

    # Para probar el manejo global de excepciones, descomenta la siguiente línea:
    # raise ValueError("Excepción no controlada para probar sys.excepthook")
