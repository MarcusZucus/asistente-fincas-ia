import logging
import os
import re
from datetime import datetime
from dotenv import load_dotenv
from supabase import create_client, Client
from httpx import HTTPError
from urllib.parse import urlparse
from circuitbreaker import circuit
from prometheus_client import Gauge, Histogram
from logging.handlers import RotatingFileHandler
from backoff import on_exception, expo

# === MÉTRICAS (Prometheus) ===
CONNECTION_STATUS = Gauge('supabase_connection', 'Estado de conexión a Supabase')
CONNECTION_LATENCY = Histogram('supabase_connection_latency_seconds', 'Latencia de conexión a Supabase')

# === CONFIGURAR LOGGING ===
def configurar_logging(nombre_modulo: str):
    logger = logging.getLogger(nombre_modulo)
    if logger.handlers:
        return

    logger.setLevel(logging.INFO)
    formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")

    log_filename = f"{nombre_modulo}.log"
    file_handler = RotatingFileHandler(log_filename, maxBytes=5*1024*1024, backupCount=3)
    file_handler.setFormatter(formatter)

    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)

    logger.addHandler(file_handler)
    logger.addHandler(stream_handler)

# === VALIDADORES ===
def es_url_valida(url: str) -> bool:
    parsed = urlparse(url)
    return all([parsed.scheme, parsed.netloc])

def es_clave_valida(clave: str) -> bool:
    return re.match(r"^[A-Za-z0-9_-]{32,}$", clave) is not None

# === CONFIGURAR LOGGING PARA ESTE SCRIPT ===
configurar_logging("conexion")
logger = logging.getLogger("conexion")

# === CARGAR VARIABLES DE ENTORNO CONDICIONALMENTE ===
ENTORNO = os.getenv("ENTORNO", "desarrollo")
if ENTORNO.lower() in ["dev", "desarrollo"]:
    if os.path.exists(".env"):
        load_dotenv()
    else:
        logger.warning("⚠️ Archivo .env no encontrado.")

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

logger.info(f"🌍 Entorno actual: {ENTORNO}")

if not SUPABASE_URL or not SUPABASE_KEY:
    logger.warning("⚠️ SUPABASE_URL o SUPABASE_KEY no están correctamente definidas")

# === CONEXIÓN CON CIRCUIT BREAKER Y BACKOFF ===
@on_exception(expo, HTTPError, max_tries=3)
@CONNECTION_LATENCY.time()  # Métrica de latencia
@circuit(failure_threshold=3, recovery_timeout=30)
def conectar_supabase(url: str = SUPABASE_URL, key: str = SUPABASE_KEY) -> Client:
    if not url or not key:
        logger.error("❌ Faltan las variables SUPABASE_URL o SUPABASE_KEY")
        CONNECTION_STATUS.set(0)
        raise EnvironmentError("Variables faltantes.")

    if not es_url_valida(url) or not es_clave_valida(key):
        logger.error("❌ Formato inválido en SUPABASE_URL o SUPABASE_KEY")
        CONNECTION_STATUS.set(0)
        raise ValueError("Formato inválido.")

    try:
        logger.info(f"🔑 Usando clave Supabase: {key[:2]}...{key[-2:]}")
        supabase = create_client(url, key)
        logger.info("✅ Conexión a Supabase establecida.")
        CONNECTION_STATUS.set(1)
        return supabase

    except HTTPError as http_err:
        logger.error(f"❌ Error HTTP: {http_err}")
        CONNECTION_STATUS.set(0)
        raise

    except Exception as e:
        logger.error(f"❌ Error inesperado: {e}")
        CONNECTION_STATUS.set(0)
        raise

# === PRUEBA DE CONEXIÓN CON TABLA DE SISTEMA ===
def probar_conexion(supabase: Client):
    try:
        response = supabase.table("pg_tables").select("schemaname").limit(1).execute()
        logger.info("✅ Consulta de prueba a pg_tables exitosa.")
        return True

    except ClientResponseError as e:
        logger.error(f"❌ Error Supabase: {e}")
        raise

    except Exception as e:
        logger.error(f"❌ Error al consultar tabla de sistema: {e}")
        raise

# === MAIN ===
if __name__ == "__main__":
    try:
        cliente = conectar_supabase()
        probar_conexion(cliente)
    except Exception as e:
        logger.error(f"🚨 Error general durante test: {e}")  # que piensas? ¿Es mejor que el anterior? ¿Cómo lo ves?