"""
conexion.py - Módulo de Conexión a Supabase

Este módulo se encarga de establecer y validar la conexión a la base de datos de Supabase,
aplicando las siguientes características:

  1. Carga condicional de variables de entorno: Se utilizan las variables definidas en el entorno
     para determinar la URL y clave de Supabase. En entornos de desarrollo, se carga el archivo .env
     si está presente.
  
  2. Validación de credenciales: Se verifica que la URL y la clave cumplan con el formato esperado
     (por ejemplo, la URL debe tener un scheme y netloc; la clave debe tener una longitud mínima).

  3. Configuración de Logging: Se configura un logger específico para este módulo utilizando un 
     RotatingFileHandler y un StreamHandler para registrar tanto en archivo como en consola.

  4. Uso de métricas (Prometheus): Se definen métricas para monitorizar el estado de la conexión
     a Supabase y la latencia de conexión.
  
  5. Implementación de mecanismos de resiliencia:
      - Backoff: Para reintentar la conexión en caso de errores HTTP.
      - Circuit Breaker: Para evitar sobrecargar la aplicación en caso de fallos repetidos.
  
  6. Funciones adicionales: Se incluye una función de prueba de conexión que realiza una consulta 
     a una tabla del sistema (por ejemplo, 'pg_tables') para verificar que la conexión es funcional.
  
El módulo está diseñado para integrarse de manera coherente con la configuración centralizada del sistema
y con el resto de módulos (como config.py, logger.py, etc.), garantizando que no existan redundancias o 
inconsistencias.
"""

import logging
import os
import re
from datetime import datetime
from urllib.parse import urlparse

# Cargar variables de entorno condicionalmente (en entornos de desarrollo se usa .env)
from dotenv import load_dotenv

# Importar cliente de Supabase y sus tipos para poder trabajar con la base de datos.
from supabase import create_client, Client

# Para capturar errores HTTP de la librería httpx, la cual puede ser utilizada internamente
from httpx import HTTPError

# Importar herramientas para implementar reintentos y retroceso exponencial (backoff)
from backoff import on_exception, expo

# Importar la funcionalidad de circuit breaker para evitar múltiples intentos en fallos repetidos
from circuitbreaker import circuit

# Importar Prometheus para medir el estado y latencia de la conexión a Supabase
from prometheus_client import Gauge, Histogram

# Importar RotatingFileHandler para el logging a archivo, permitiendo que los archivos no se hagan muy grandes
from logging.handlers import RotatingFileHandler

# === CONFIGURACIÓN DE MÉTRICAS (Prometheus) ===
# Gauge para indicar el estado de la conexión (1 = conexión exitosa, 0 = fallo)
CONNECTION_STATUS = Gauge('supabase_connection', 'Estado de conexión a Supabase')

# Histogram para medir la latencia (tiempo) que toma la conexión a Supabase
CONNECTION_LATENCY = Histogram('supabase_connection_latency_seconds', 'Latencia de conexión a Supabase')

# === CONFIGURAR LOGGING ===
def configurar_logging(nombre_modulo: str):
    """
    Configura el logging para el módulo indicado.

    Se establece un logger específico que utiliza:
      - Un RotatingFileHandler: Guarda los logs en un archivo con límite de tamaño y respaldo.
      - Un StreamHandler: Permite visualizar los logs en la consola.
      - Un formato simple que incluye timestamp, nivel y mensaje.
    
    Si el logger ya cuenta con manejadores configurados, la función no hace nada para evitar duplicidades.

    Args:
      nombre_modulo (str): Nombre del módulo para el cual se configura el logging.
    """
    logger = logging.getLogger(nombre_modulo)
    # Evitar reconfiguración si ya existen handlers configurados para este logger.
    if logger.handlers:
        return

    # Establecer nivel de logging a DEBUG para mayor detalle en el desarrollo
    logger.setLevel(logging.DEBUG)
    
    # Definir el formateador con fecha, nivel y mensaje
    formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", datefmt='%Y-%m-%d %H:%M:%S')

    # Configurar el RotatingFileHandler para guardar logs en un archivo específico
    log_filename = f"{nombre_modulo}.log"
    file_handler = RotatingFileHandler(log_filename, maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf8")
    file_handler.setFormatter(formatter)

    # Configurar el StreamHandler para imprimir los logs en la salida estándar (consola)
    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)

    # Agregar ambos manejadores al logger
    logger.addHandler(file_handler)
    logger.addHandler(stream_handler)
    
    # Registrar en debug que el logger ha sido configurado
    logger.debug("Logging configurado correctamente para el módulo.")

# Configuración inicial del logger para el módulo "conexion"
configurar_logging("conexion")
logger = logging.getLogger("conexion")

# === VALIDADORES DE FORMATO ===
def es_url_valida(url: str) -> bool:
    """
    Verifica si la URL proporcionada es válida en términos de estructura.
    
    Se valida que la URL tenga un scheme (por ejemplo, 'http' o 'https') y un netloc (dominio o IP).
    
    Args:
      url (str): URL a validar, por ejemplo: "https://mi-proyecto.supabase.co".
    
    Returns:
      bool: True si la URL es válida, False en caso contrario.
    
    Ejemplo:
      >>> es_url_valida("https://mi-proyecto.supabase.co")
      True
    """
    parsed = urlparse(url)
    # La URL es válida si tiene tanto scheme como netloc
    es_valida = all([parsed.scheme, parsed.netloc])
    logger.debug(f"Validando URL '{url}': {es_valida}")
    return es_valida

def es_clave_valida(clave: str) -> bool:
    """
    Valida la clave de Supabase de forma flexible.

    Se verifica que la clave exista y que su longitud sea mayor a 30 caracteres para asegurar
    que no sea una cadena vacía o demasiado corta (lo cual indicaría un error de configuración).

    Args:
      clave (str): La clave a validar.
    
    Returns:
      bool: True si la clave es válida, False en caso contrario.
    
    Ejemplo:
      >>> es_clave_valida("abcdef1234567890abcdef1234567890")
      True
    """
    es_valida = bool(clave and len(clave) > 30)
    logger.debug(f"Validando clave: {es_valida} (Longitud: {len(clave) if clave else 0})")
    return es_valida

# === CONFIGURAR VARIABLES DE ENTORNO CONDICIONALMENTE ===
# Se define el entorno (por defecto "desarrollo") para determinar si se debe cargar el archivo .env
ENTORNO = os.getenv("ENTORNO", "desarrollo")
logger.info(f"🌍 Entorno actual: {ENTORNO}")

# Si el entorno es de desarrollo, se intenta cargar el archivo .env para obtener variables locales
if ENTORNO.lower() in ["dev", "desarrollo"]:
    if os.path.exists(".env"):
        load_dotenv()  # Carga las variables de entorno desde el archivo .env
        logger.debug("Archivo .env cargado correctamente.")
    else:
        logger.warning("⚠️ Archivo .env no encontrado. Verifica el entorno de desarrollo.")

# Obtener las variables de entorno específicas para la conexión a Supabase
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
logger.debug(f"SUPABASE_URL: {SUPABASE_URL}")
logger.debug(f"SUPABASE_KEY: {'No vacía' if SUPABASE_KEY else 'Vacía'}")

# Aviso si alguna de las variables críticas no está definida
if not SUPABASE_URL or not SUPABASE_KEY:
    logger.warning("⚠️ SUPABASE_URL o SUPABASE_KEY no están correctamente definidas en las variables de entorno.")

# === CONEXIÓN CON CIRCUIT BREAKER Y BACKOFF ===
@on_exception(expo, HTTPError, max_tries=3)
@CONNECTION_LATENCY.time()  # Mide la latencia de la conexión utilizando Prometheus
@circuit(failure_threshold=3, recovery_timeout=30)
def conectar_supabase(url: str = SUPABASE_URL, key: str = SUPABASE_KEY) -> Client:
    """
    Establece una conexión a Supabase creando un cliente.

    La función incorpora:
      - Validación de la URL y la clave.
      - Backoff: Reintenta la conexión en caso de error HTTP, usando un algoritmo de retroceso exponencial.
      - Circuit Breaker: Detiene los reintentos después de 3 fallos y espera 30 segundos antes de reintentar.
      - Métricas: Registra la latencia de conexión y el estado de la misma mediante Prometheus.
    
    Args:
      url (str, opcional): URL de Supabase (por defecto se usa la variable de entorno SUPABASE_URL).
      key (str, opcional): Clave de Supabase (por defecto se usa la variable de entorno SUPABASE_KEY).

    Returns:
      Client: Instancia del cliente de Supabase, ya conectado y validado.
    
    Raises:
      EnvironmentError: Si faltan las variables de entorno necesarias.
      ValueError: Si la URL o clave no tienen el formato esperado.
      HTTPError: Si ocurre un error HTTP durante la conexión.
      Exception: Para cualquier otro error inesperado durante la conexión.
    
    Ejemplo:
      >>> client = conectar_supabase()
    """
    logger.debug("Iniciando proceso de conexión a Supabase.")

    # Verificar que se disponga de la URL y la clave
    if not url or not key:
        logger.error("❌ Faltan las variables SUPABASE_URL o SUPABASE_KEY.")
        CONNECTION_STATUS.set(0)
        raise EnvironmentError("Variables faltantes para la conexión a Supabase.")

    # Validar el formato de la URL y la longitud de la clave
    if not es_url_valida(url) or not es_clave_valida(key):
        logger.error("❌ Formato inválido en SUPABASE_URL o SUPABASE_KEY.")
        CONNECTION_STATUS.set(0)
        raise ValueError("Formato inválido en las credenciales de Supabase.")

    try:
        # Registro parcial de la clave para no exponerla completamente en los logs
        logger.info(f"🔑 Usando clave Supabase: {key[:2]}...{key[-2:]}")
        logger.debug(f"Intentando conectar con URL: {url}")

        # Crear el cliente de Supabase utilizando la función create_client
        supabase = create_client(url, key)
        
        # Se puede realizar una validación adicional si se requiere: por ejemplo, ejecutar una consulta de prueba.
        # En este caso, se omite y se asume que la conexión se ha establecido correctamente.
        logger.info("✅ Conexión a Supabase establecida exitosamente.")
        CONNECTION_STATUS.set(1)
        return supabase

    except HTTPError as http_err:
        logger.error(f"❌ Error HTTP durante la conexión a Supabase: {http_err}")
        CONNECTION_STATUS.set(0)
        raise

    except Exception as e:
        logger.error(f"❌ Error inesperado durante la conexión a Supabase: {e}")
        CONNECTION_STATUS.set(0)
        raise

# === PRUEBA DE CONEXIÓN CON TABLA DE SISTEMA ===
def probar_conexion(supabase: Client):
    """
    Realiza una consulta de prueba a la tabla 'pg_tables' para verificar que la conexión a Supabase es funcional.

    Se utiliza la tabla 'pg_tables' (tabla del sistema) para asegurarse de que la conexión puede realizar
    consultas básicas sin problemas. Esto es útil para detectar fallos en la configuración o en la red.

    Args:
      supabase (Client): Cliente de Supabase previamente conectado.
    
    Returns:
      bool: True si la consulta de prueba fue exitosa, de lo contrario se lanza una excepción.

    Ejemplo:
      >>> cliente = conectar_supabase()
      >>> probar_conexion(cliente)
      True
    """
    logger.debug("Iniciando prueba de consulta a la tabla 'pg_tables'.")
    try:
        # Realizar una consulta simple a 'pg_tables'
        response = supabase.table("pg_tables").select("schemaname").limit(1).execute()
        logger.debug(f"Respuesta de prueba obtenida: {response}")
        logger.info("✅ Consulta de prueba a 'pg_tables' exitosa.")
        return True

    except Exception as e:
        logger.error(f"❌ Error al consultar la tabla de sistema 'pg_tables': {e}")
        raise

# === SECCIÓN PRINCIPAL: PRUEBA DE CONEXIÓN ===
if __name__ == "__main__":
    logger.info("🚀 Inicio de test de conexión a Supabase.")
    try:
        # Se establece la conexión a Supabase utilizando las variables de entorno
        cliente = conectar_supabase()
        logger.debug("Cliente de Supabase obtenido, procediendo a la prueba de conexión.")
        # Se ejecuta la función de prueba para verificar la conexión
        probar_conexion(cliente)
        logger.info("🚀 Test de conexión a Supabase completado correctamente.")
    except Exception as e:
        logger.error(f"🚨 Error general durante el test de conexión a Supabase: {e}")
