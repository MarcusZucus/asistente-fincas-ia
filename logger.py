import logging
import logging.config
import sys
import os

def setup_logging(default_level=logging.DEBUG):
    """
    Configura el logging para todo el proyecto con:
      - Console handler (nivel DEBUG y superior).
      - RotatingFileHandler para todos los logs (archivo 'project.log').
      - RotatingFileHandler exclusivo para errores (archivo 'error.log').
      - Formateo detallado con timestamp, nivel, nombre del logger y número de línea.
      - Manejo global de excepciones no controladas.
    """
    # Directorio para los logs
    log_dir = "logs"
    if not os.path.exists(log_dir):
        os.makedirs(log_dir)

    logging_config = {
        'version': 1,
        'disable_existing_loggers': False,  # No deshabilitar loggers ya existentes
        'formatters': {
            'detailed': {
                'format': '%(asctime)s [%(levelname)s] [%(name)s:%(lineno)d] %(message)s',
                'datefmt': '%Y-%m-%d %H:%M:%S'
            },
            'simple': {
                'format': '%(asctime)s [%(levelname)s] %(message)s',
                'datefmt': '%Y-%m-%d %H:%M:%S'
            },
        },
        'handlers': {
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
        },
        'root': {
            'handlers': ['console', 'file_all', 'file_error'],
            'level': default_level,
        },
        'loggers': {
            # Ejemplo: configuración específica para el logger "supabase"
            'supabase': {
                'handlers': ['console', 'file_all'],
                'level': 'DEBUG',
                'propagate': False
            },
            # Puedes agregar más loggers con configuraciones personalizadas si lo requieres.
        }
    }

    logging.config.dictConfig(logging_config)

    # Configurar una función global para capturar excepciones no controladas
    def handle_exception(exc_type, exc_value, exc_traceback):
        if issubclass(exc_type, KeyboardInterrupt):
            # No capturamos KeyboardInterrupt para permitir la interrupción normal
            sys.__excepthook__(exc_type, exc_value, exc_traceback)
            return
        logging.critical("Excepción no controlada", exc_info=(exc_type, exc_value, exc_traceback))

    sys.excepthook = handle_exception

# Ejecutar la configuración de logging de inmediato al importar el módulo
setup_logging()

def get_logger(name: str = None) -> logging.Logger:
    """
    Devuelve un logger configurado globalmente. Si no se especifica un nombre, retorna el logger raíz.
    """
    return logging.getLogger(name)

# Ejemplo de uso y pruebas (se ejecuta solo si se corre este módulo de forma directa)
if __name__ == "__main__":
    logger = get_logger(__name__)
    logger.debug("Mensaje DEBUG: trazas internas y variables.")
    logger.info("Mensaje INFO: progreso de la aplicación.")
    logger.warning("Mensaje WARNING: advertencia sobre posibles problemas.")
    logger.error("Mensaje ERROR: se ha producido un error.")
    logger.critical("Mensaje CRITICAL: error grave, requiere atención inmediata.")

    # Simulación de excepción controlada
    try:
        1 / 0
    except Exception:
        logger.exception("Ocurrió una excepción al dividir por cero.")

    # Descomenta la siguiente línea para probar el manejo global de excepciones (terminará el programa)
    # raise ValueError("Excepción no controlada para probar sys.excepthook")
