# config.py
import os
from dotenv import load_dotenv

# Cargar variables de entorno una sola vez
load_dotenv()

# --- Variables Generales ---
ENTORNO = os.getenv("ENTORNO", "desarrollo")

# --- Supabase ---
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

# --- OpenAI ---
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "text-embedding-ada-002")
MAX_TOKENS = int(os.getenv("MAX_TOKENS", 2048))
BATCH_SIZE = int(os.getenv("BATCH_SIZE", 500))
MAX_RETRIES = int(os.getenv("MAX_RETRIES", 3))

# --- Tablas ---
TABLA_ADMINISTRACIONES = "administraciones"
TABLA_FINCAS = "fincas"
TABLA_USUARIOS = "usuarios"
TABLA_INCIDENCIAS = "incidencias"
# Tabla destino para embeddings (se puede sobrescribir con variable de entorno)
TABLA_DESTINO = os.getenv("TABLA_DESTINO", "documentos_embeddings")
TABLA_EMBEDDINGS = os.getenv("TABLA_EMBEDDINGS", "documentos_embeddings")

# --- Telegram ---
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
WEBHOOK_URL = os.getenv("WEBHOOK_URL", "https://asistente-fincas-ia-production.up.railway.app")
PORT = int(os.getenv("PORT", 8443))

# --- JWT y Autenticación ---
SECRET_KEY = os.getenv("SECRET_KEY", "supersecretkey")
JWT_ALGORITHM = os.getenv("JWT_ALGORITHM", "HS256")
ACCESS_TOKEN_EXPIRE_MINUTES = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", 60))

# --- Otros ---
TOP_K = int(os.getenv("TOP_K", 3))

# Validaciones críticas
if not SUPABASE_URL or not SUPABASE_KEY:
    raise EnvironmentError("Faltan variables de entorno críticas para Supabase.")
if not OPENAI_API_KEY:
    raise EnvironmentError("Falta la variable de entorno OPENAI_API_KEY.")
if not TELEGRAM_BOT_TOKEN or len(TELEGRAM_BOT_TOKEN) < 30:
    raise EnvironmentError("Token de Telegram inválido o no definido.")
