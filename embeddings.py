import logging
import os
import re
import json
from datetime import datetime
from urllib.parse import urlparse
from dotenv import load_dotenv
import openai
from supabase import create_client
from supabase.lib.client_options import ClientResponseError
from typing import List
from logging.handlers import RotatingFileHandler
from backoff import on_exception, expo
from circuitbreaker import circuit
from prometheus_client import Counter, Summary, Histogram, start_http_server
from tenacity import retry, stop_after_attempt, RetryError
from conexion import conectar_supabase

# === CONFIGURACIÓN GENERAL ===
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "text-embedding-3-small")
BATCH_SIZE = int(os.getenv("BATCH_SIZE", 500))
MAX_TOKENS = int(os.getenv("MAX_TOKENS", 2048))
MAX_RETRIES = int(os.getenv("MAX_RETRIES", 3))
TABLA_ORIGEN = os.getenv("TABLA_ORIGEN", "documentos")
TABLA_DESTINO = os.getenv("TABLA_DESTINO", "documentos_embeddings")

# === CONFIGURAR LOGGING ROTATIVO ===
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

configurar_logging("embeddings")
logger = logging.getLogger("embeddings")

# === MÉTRICAS PROMETHEUS ===
EMBEDDING_COUNTER = Counter('embeddings_generados', 'Total de embeddings generados')
PROCESS_TIME = Summary('embeddings_process_time', 'Tiempo total de procesamiento de embeddings')
DOCUMENTOS_INVALIDOS = Counter('documentos_invalidos', 'Documentos omitidos por ser inválidos')
LATENCIA_SUPABASE = Histogram('latencia_supabase', 'Tiempo de respuesta en consultas Supabase')

# === CARGAR VARIABLES DE ENTORNO ===
load_dotenv()
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
openai.api_key = OPENAI_API_KEY

logger.info(f"🔑 Usando clave Supabase: {SUPABASE_KEY[:2]}...{SUPABASE_KEY[-2:]}")
logger.info(f"🤖 Usando modelo OpenAI embeddings: {EMBEDDING_MODEL}")

# === VALIDACIONES MEJORADAS ===
def es_url_valida(url):
    parsed = urlparse(url)
    return all([parsed.scheme == "https", parsed.netloc.endswith(".supabase.co")])

def es_clave_valida(key):
    return key and len(key) > 10

# === CARGA PAGINADA DE DATOS ===
def cargar_datos_para_indexar(supabase, tabla=TABLA_ORIGEN, batch_size=1000):
    page = 0
    all_data = []
    while True:
        try:
            with LATENCIA_SUPABASE.time():
                response = supabase.table(tabla).select("*").range(page * batch_size, (page + 1) * batch_size - 1).execute()
            if not response.data:
                break
            logger.info(f"📥 Página {page + 1}: {len(response.data)} registros cargados.")
            all_data.extend(response.data)
            page += 1
        except ClientResponseError as e:
            logger.error(f"❌ Error Supabase al acceder a '{tabla}': {e.message}")
            raise
        except Exception as e:
            logger.error(f"❌ Error al cargar datos desde Supabase: {e}")
            raise
    logger.info(f"📄 Total registros cargados de la tabla '{tabla}': {len(all_data)}.")
    return all_data

# === PREPROCESAMIENTO DE TEXTO ===
def preprocesar_texto(texto: str) -> str:
    texto = re.sub(r'\s+', ' ', texto).strip()
    if len(texto.split()) > MAX_TOKENS:
        logger.warning("⚠️ Texto excede tamaño, se truncará.")
        texto = " ".join(texto.split()[:MAX_TOKENS])
    return texto

# === GENERAR EMBEDDINGS CON OPENAI ===
@PROCESS_TIME.time()
def generar_embeddings(textos: List[str], modelo=EMBEDDING_MODEL) -> List[List[float]]:
    try:
        response = openai.Embedding.create(input=textos, model=modelo)
        embeddings = [item["embedding"] for item in response["data"]]
        EMBEDDING_COUNTER.inc(len(embeddings))
        logger.info(f"✅ Generados {len(embeddings)} embeddings con OpenAI.")
        return embeddings
    except Exception as e:
        logger.error(f"❌ Error generando embeddings con OpenAI: {e}")
        raise

# === GUARDADO SEGURO DE EMBEDDINGS POR LOTES ===
@retry(stop=stop_after_attempt(MAX_RETRIES))
def guardar_batch(supabase, tabla, batch):
    supabase.table(tabla).insert(batch).execute()

def guardar_embeddings(supabase, tabla: str, datos: List[dict], batch_size=BATCH_SIZE):
    for i in range(0, len(datos), batch_size):
        batch = datos[i:i + batch_size]
        try:
            guardar_batch(supabase, tabla, batch)
            logger.info(f"💾 Guardado batch de {len(batch)} embeddings en '{tabla}'.")
        except RetryError:
            logger.critical("❌ Batch crítico fallido 3 veces. Guardando en archivo.")
            with open("failed_batches.log", "a") as f:
                json.dump(batch, f)
                f.write("\n")

# === EJECUCIÓN DIRECTA ===
if __name__ == "__main__":
    try:
        # Iniciar servidor Prometheus para métricas en puerto 8000
        start_http_server(8000)

        supabase = conectar_supabase()
        datos = cargar_datos_para_indexar(supabase)

        textos = []
        datos_validos = []
        documentos_invalidos = 0

        for i, d in enumerate(datos):
            columnas_requeridas = {"id", "contenido"}
            if not columnas_requeridas.issubset(d.keys()):
                logger.error(f"❌ Documento {i} no tiene columnas requeridas: {columnas_requeridas}")
                documentos_invalidos += 1
                DOCUMENTOS_INVALIDOS.inc()
                continue

            contenido = d.get("contenido", "").strip()
            if not contenido:
                logger.warning(f"⚠️ Documento {i} con contenido vacío o nulo")
                documentos_invalidos += 1
                DOCUMENTOS_INVALIDOS.inc()
                continue

            texto_preprocesado = preprocesar_texto(contenido)
            textos.append(texto_preprocesado)
            datos_validos.append(d)

        if documentos_invalidos > 0:
            logger.warning(f"⚠️ Se omitieron {documentos_invalidos} documentos inválidos.")

        embeddings = generar_embeddings(textos)

        datos_con_embeddings = [
            {**datos_validos[i], "embedding_vector": embeddings[i], "vectorizado_en": datetime.utcnow().isoformat()}
            for i in range(len(embeddings))
        ]

        guardar_embeddings(supabase, TABLA_DESTINO, datos_con_embeddings)
        logger.info("🚀 Proceso completado correctamente.")

    except Exception as e:
        logger.error(f"🚨 Error general en ejecución: {e}")
