import logging
import logging.config
import os
import sys

def setup_logging(default_level=logging.DEBUG):
    """
    Configura el logging para todo el proyecto con:
      - Console handler (nivel DEBUG y superiores).
      - RotatingFileHandler para logs generales (archivo 'project.log').
      - RotatingFileHandler exclusivo para errores (archivo 'error.log').
      - Formateo detallado con timestamp, nivel, nombre del logger y número de línea.
      - Captura global de excepciones no controladas para registrar errores inesperados.
      
    La configuración se basa en un diccionario que permite ampliar o modificar
    los manejadores y formateadores sin duplicar código en cada módulo.
    """
    # Crear directorio para logs si no existe
    log_dir = "logs"
    if not os.path.exists(log_dir):
        os.makedirs(log_dir)

    # Definición de formateadores
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

    # Definición de manejadores
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
            'maxBytes': 10 * 1024 * 1024,  # 10 MB
            'backupCount': 5,
            'encoding': 'utf8'
        },
    }

    # Configuración global y de loggers específicos
    logging_config = {
        'version': 1,
        'disable_existing_loggers': False,  # Mantener loggers ya configurados
        'formatters': formatters,
        'handlers': handlers,
        'root': {
            'handlers': ['console', 'file_all', 'file_error'],
            'level': default_level,
        },
        'loggers': {
            # Configuración específica para módulos como 'supabase' u otros
            'supabase': {
                'handlers': ['console', 'file_all'],
                'level': 'DEBUG',
                'propagate': False
            },
            # Se pueden agregar más loggers con configuraciones personalizadas
        }
    }

    # Aplicar la configuración definida
    logging.config.dictConfig(logging_config)

    # Definir función para capturar excepciones no controladas y registrarlas
    def handle_exception(exc_type, exc_value, exc_traceback):
        if issubclass(exc_type, KeyboardInterrupt):
            # Permitir la interrupción normal (Ctrl+C)
            sys.__excepthook__(exc_type, exc_value, exc_traceback)
            return
        logging.critical("Excepción no controlada", exc_info=(exc_type, exc_value, exc_traceback))

    sys.excepthook = handle_exception

# Configurar el logging inmediatamente al importar el módulo
setup_logging()

def get_logger(name: str = None) -> logging.Logger:
    """
    Devuelve un logger configurado globalmente. Si se especifica un nombre,
    se devuelve el logger correspondiente; en caso contrario, se retorna el logger raíz.
    
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
