import sys
# Reconfiguramos stdout y stderr para usar UTF-8
try:
    sys.stdout.reconfigure(encoding='utf-8')
    sys.stderr.reconfigure(encoding='utf-8')
except Exception:
    pass

import logging
import os
import re
import json
import uuid
from datetime import datetime
from urllib.parse import urlparse
from dotenv import load_dotenv
import openai  # Asegúrate de usar openai==0.28 para compatibilidad con openai.Embedding.create
from supabase import create_client
from typing import List, Dict
from logging.handlers import RotatingFileHandler
from backoff import on_exception, expo
from circuitbreaker import circuit
from prometheus_client import Counter, Summary, Histogram, start_http_server
from tenacity import retry, stop_after_attempt, RetryError

# === CONFIGURACIÓN GENERAL ===
# Se utiliza "text-embedding-ada-002" por defecto (económico y recomendado).
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "text-embedding-ada-002")
BATCH_SIZE = int(os.getenv("BATCH_SIZE", 500))
MAX_TOKENS = int(os.getenv("MAX_TOKENS", 2048))
MAX_RETRIES = int(os.getenv("MAX_RETRIES", 3))

# Tablas de donde extraer datos (solo se usan las que tienes)
TABLA_ADMINISTRACIONES = "administraciones"
TABLA_FINCAS = "fincas"
TABLA_USUARIOS = "usuarios"
TABLA_INCIDENCIAS = "incidencias"

# Tabla destino para embeddings (configurable mediante variable de entorno)
TABLA_DESTINO = os.getenv("TABLA_DESTINO", "documentos_embeddings")

# === CONFIGURAR LOGGING ROTATIVO (sin emojis) ===
def configurar_logging(nombre_modulo: str):
    """
    Configura logging con RotatingFileHandler y StreamHandler usando UTF-8.
    """
    logger = logging.getLogger(nombre_modulo)
    if logger.handlers:
        return
    logger.setLevel(logging.DEBUG)
    formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
    log_filename = f"{nombre_modulo}.log"
    file_handler = RotatingFileHandler(log_filename, maxBytes=5*1024*1024, backupCount=3)
    file_handler.setFormatter(formatter)
    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setFormatter(formatter)
    logger.addHandler(file_handler)
    logger.addHandler(stream_handler)

configurar_logging("embeddings")
logger = logging.getLogger("embeddings")

# === MÉTRICAS PROMETHEUS ===
EMBEDDING_COUNTER = Counter('embeddings_generados', 'Total de embeddings generados')
PROCESS_TIME = Summary('embeddings_process_time', 'Tiempo total de procesamiento de embeddings')
DOCUMENTOS_INVALIDOS = Counter('documentos_invalidos', 'Registros omitidos por ser inválidos')
LATENCIA_SUPABASE = Histogram('latencia_supabase', 'Tiempo de respuesta en consultas Supabase')

# === CARGAR VARIABLES DE ENTORNO ===
load_dotenv()
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
openai.api_key = OPENAI_API_KEY

logger.info(f"USANDO CLAVE SUPABASE: {SUPABASE_KEY[:2]}...{SUPABASE_KEY[-2:]}")
logger.info(f"USANDO MODELO OPENAI EMBEDDINGS: {EMBEDDING_MODEL}")
logger.debug("Variables de entorno cargadas correctamente.")

# === FUNCIONES DE CARGA DE DATOS ===

def cargar_administraciones(supabase) -> List[Dict]:
    """
    Carga y transforma datos desde la tabla 'administraciones'.
    """
    rows = supabase.table(TABLA_ADMINISTRACIONES).select("*").execute().data
    logger.info(f"(administraciones) Cargados {len(rows)} registros.")
    result = []
    for i, a in enumerate(rows):
        logger.debug(f"Procesando administración registro {i}: {a}")
        if not all(k in a for k in ("id", "nombre", "direccion", "telefono", "email")):
            logger.warning(f"Faltan columnas en administraciones (registro {i}).")
            DOCUMENTOS_INVALIDOS.inc()
            continue
        texto = (f"La administración '{a['nombre']}' está en {a['direccion']}, "
                 f"teléfono {a['telefono']}, email {a['email']}.")
        result.append({"id": a["id"], "contenido": texto})
    return result

def cargar_fincas(supabase) -> List[Dict]:
    """
    Carga y transforma datos desde la tabla 'fincas'.
    """
    rows = supabase.table(TABLA_FINCAS).select("*").execute().data
    logger.info(f"(fincas) Cargados {len(rows)} registros.")
    result = []
    for i, f in enumerate(rows):
        logger.debug(f"Procesando finca registro {i}: {f}")
        if not all(k in f for k in ("id", "nombre", "direccion", "numero_puertas", "administracion_id")):
            logger.warning(f"Faltan columnas en fincas (registro {i}).")
            DOCUMENTOS_INVALIDOS.inc()
            continue
        texto = (f"La finca '{f['nombre']}' está ubicada en {f['direccion']}, "
                 f"tiene {f['numero_puertas']} puertas y pertenece a la administración con ID {f['administracion_id']}.")
        result.append({"id": f["id"], "contenido": texto})
    return result

def cargar_usuarios(supabase) -> List[Dict]:
    """
    Carga y transforma datos desde la tabla 'usuarios'.
    """
    rows = supabase.table(TABLA_USUARIOS).select("*").execute().data
    logger.info(f"(usuarios) Cargados {len(rows)} registros.")
    result = []
    for i, u in enumerate(rows):
        logger.debug(f"Procesando usuario registro {i}: {u}")
        if not all(k in u for k in ("id", "nombre", "rol", "nombre_fincas", "direccion_finca", "telefono_movil")):
            logger.warning(f"Faltan columnas en usuarios (registro {i}).")
            DOCUMENTOS_INVALIDOS.inc()
            continue
        texto = (f"El usuario '{u['nombre']}' es {u['rol']} en la finca '{u['nombre_fincas']}' "
                 f"(dirección: {u['direccion_finca']}), teléfono: {u['telefono_movil']}.")
        result.append({"id": u["id"], "contenido": texto})
    return result

def cargar_incidencias(supabase) -> List[Dict]:
    """
    Carga y transforma datos desde la tabla 'incidencias'.
    """
    rows = supabase.table(TABLA_INCIDENCIAS).select("*").execute().data
    logger.info(f"(incidencias) Cargados {len(rows)} registros.")
    result = []
    for i, inc in enumerate(rows):
        logger.debug(f"Procesando incidencia registro {i}: {inc}")
        if not all(k in inc for k in ("id", "tipo", "urgencia", "finca_id", "usuario_id", "telefono_movil", "descripcion")):
            logger.warning(f"Faltan columnas en incidencias (registro {i}).")
            DOCUMENTOS_INVALIDOS.inc()
            continue
        texto = (f"Incidencia tipo '{inc['tipo']}' con urgencia '{inc['urgencia']}' en finca ID {inc['finca_id']}, "
                 f"reportada por usuario ID {inc['usuario_id']}. Teléfono: {inc['telefono_movil']}. "
                 f"Descripción: {inc['descripcion']}.")
        result.append({"id": inc["id"], "contenido": texto})
    return result

# === PREPROCESAMIENTO DE TEXTO ===
def preprocesar_texto(texto: str) -> str:
    """
    Elimina espacios extra y trunca el texto si supera MAX_TOKENS palabras.
    """
    logger.debug(f"Texto original: {texto}")
    texto = re.sub(r'\s+', ' ', texto).strip()
    palabras = texto.split()
    if len(palabras) > MAX_TOKENS:
        logger.warning("Texto excede tamaño, se truncará.")
        texto = " ".join(palabras[:MAX_TOKENS])
    logger.debug(f"Texto preprocesado: {texto}")
    return texto

# === GENERAR EMBEDDINGS CON OPENAI ===
@PROCESS_TIME.time()
def generar_embeddings(textos: List[str], modelo=EMBEDDING_MODEL) -> List[List[float]]:
    """
    Llama a la API de OpenAI para generar embeddings en batch.
    """
    logger.debug(f"Iniciando generación de embeddings para {len(textos)} textos.")
    try:
        response = openai.Embedding.create(input=textos, model=modelo)
        logger.debug(f"Respuesta de OpenAI: {response}")
        embeddings = [item["embedding"] for item in response["data"]]
        EMBEDDING_COUNTER.inc(len(embeddings))
        logger.info(f"Generados {len(embeddings)} embeddings con OpenAI.")
        return embeddings
    except Exception as e:
        logger.error(f"Error generando embeddings con OpenAI: {e}")
        raise

# === GUARDADO SEGURO DE EMBEDDINGS POR LOTES ===
@retry(stop=stop_after_attempt(MAX_RETRIES))
def guardar_batch(supabase, tabla, batch):
    """
    Inserta un batch de registros en la tabla de Supabase.
    """
    logger.debug(f"Guardando batch en la tabla {tabla}: {batch}")
    supabase.table(tabla).insert(batch).execute()
    logger.debug("Batch guardado correctamente.")

def guardar_embeddings(supabase, tabla: str, datos: List[dict], batch_size=BATCH_SIZE):
    """
    Divide 'datos' en lotes y los inserta en 'tabla' usando guardar_batch().
    """
    total_batches = (len(datos) + batch_size - 1) // batch_size
    logger.info(f"Iniciando guardado de {len(datos)} registros en {total_batches} batches.")
    for i in range(0, len(datos), batch_size):
        batch = datos[i:i + batch_size]
        try:
            guardar_batch(supabase, tabla, batch)
            logger.info(f"Guardado batch de {len(batch)} embeddings en '{tabla}'.")
        except RetryError:
            logger.critical("Batch crítico fallido 3 veces. Guardando en archivo failed_batches.log.")
            with open("failed_batches.log", "a", encoding="utf-8") as f:
                json.dump(batch, f)
                f.write("\n")

# === CONEXIÓN SUPABASE (Circuit Breaker / Backoff) ===
@on_exception(expo, Exception, max_tries=3)
@circuit(failure_threshold=3, recovery_timeout=30)
def conectar_supabase():
    """
    Crea un cliente de Supabase con manejo de errores.
    """
    logger.debug("Intentando conectar a Supabase...")
    if not SUPABASE_URL or not SUPABASE_KEY:
        logger.error("Faltan SUPABASE_URL o SUPABASE_KEY")
        raise EnvironmentError("Variables faltantes para Supabase.")
    
    parsed = urlparse(SUPABASE_URL)
    if not parsed.scheme or not parsed.netloc:
        logger.error("Formato inválido en SUPABASE_URL")
        raise ValueError("URL inválida.")
    
    if len(SUPABASE_KEY) < 20:
        logger.error("Formato inválido en SUPABASE_KEY (demasiado corta)")
        raise ValueError("KEY inválida.")
    
    logger.info(f"Conectando a Supabase: {SUPABASE_URL}")
    client = create_client(SUPABASE_URL, SUPABASE_KEY)
    logger.debug("Conexión a Supabase establecida.")
    return client

# === EJECUCIÓN DIRECTA ===
if __name__ == "__main__":
    try:
        # Iniciar servidor Prometheus para métricas en el puerto 8000
        start_http_server(8000)
        logger.info("Servidor Prometheus iniciado en el puerto 8000.")
        
        # 1. Conectarse a Supabase
        supabase = conectar_supabase()
        
        # 2. Cargar datos de las tablas disponibles
        logger.info("Cargando datos desde las tablas: administraciones, fincas, usuarios e incidencias...")
        data_administraciones = cargar_administraciones(supabase)
        data_fincas = cargar_fincas(supabase)
        data_usuarios = cargar_usuarios(supabase)
        data_incidencias = cargar_incidencias(supabase)
        
        # Combinar todas las fuentes de datos
        all_data = data_administraciones + data_fincas + data_usuarios + data_incidencias
        logger.debug(f"Total registros combinados: {len(all_data)}")
        
        # 3. Preprocesar y filtrar datos válidos
        textos = []
        datos_validos = []
        registros_invalidos = 0

        for i, d in enumerate(all_data):
            logger.debug(f"Validando registro {i}: {d}")
            if "id" not in d or "contenido" not in d:
                logger.error(f"Registro {i} sin 'id' o 'contenido'.")
                registros_invalidos += 1
                DOCUMENTOS_INVALIDOS.inc()
                continue

            contenido = d["contenido"].strip()
            if not contenido:
                logger.warning(f"Registro {i} con contenido vacío.")
                registros_invalidos += 1
                DOCUMENTOS_INVALIDOS.inc()
                continue

            texto_preprocesado = preprocesar_texto(contenido)
            textos.append(texto_preprocesado)
            datos_validos.append(d)
        
        if registros_invalidos > 0:
            logger.warning(f"Se omitieron {registros_invalidos} registros inválidos.")

        logger.info(f"Total de registros válidos para embeddings: {len(datos_validos)}")
        
        # 4. Generar embeddings
        embeddings = generar_embeddings(textos)
        
        # 5. Combinar datos + embeddings y guardar
        datos_con_embeddings = []
        for i in range(len(embeddings)):
            item = {
                **datos_validos[i],
                "embedding": embeddings[i],
                "vectorizado_en": datetime.utcnow().isoformat()
            }
            # Para asignar un nuevo UUID a cada registro, descomenta la siguiente línea:
            # item["id"] = str(uuid.uuid4())
            datos_con_embeddings.append(item)
        
        logger.debug(f"Datos con embeddings preparados: {datos_con_embeddings[:2]}... (mostrando 2 de {len(datos_con_embeddings)})")
        
        # Guardar en la tabla destino
        guardar_embeddings(supabase, TABLA_DESTINO, datos_con_embeddings)
        logger.info("Proceso completado correctamente.")
        
    except Exception as e:
        logger.error(f"Error general en ejecución: {e}")
