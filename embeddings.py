import logging
import os
import re
import json
import uuid
from datetime import datetime
from urllib.parse import urlparse
from dotenv import load_dotenv
import openai
from supabase import create_client
from supabase.lib.client_options import ClientResponseError
from typing import List, Dict
from logging.handlers import RotatingFileHandler
from backoff import on_exception, expo
from circuitbreaker import circuit
from prometheus_client import Counter, Summary, Histogram, start_http_server
from tenacity import retry, stop_after_attempt, RetryError

# === CONFIGURACIÓN GENERAL ===
# Puedes cambiar el modelo de embeddings a tu preferencia (OpenAI o local)
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "text-embedding-3-small")
BATCH_SIZE = int(os.getenv("BATCH_SIZE", 500))
MAX_TOKENS = int(os.getenv("MAX_TOKENS", 2048))
MAX_RETRIES = int(os.getenv("MAX_RETRIES", 3))

# Tablas de donde extraer datos
TABLA_ORIGEN = os.getenv("TABLA_ORIGEN", "documentos")  # la que ya tenías
TABLA_ADMINISTRACIONES = "administraciones"
TABLA_FINCAS = "fincas"
TABLA_USUARIOS = "usuarios"
TABLA_INCIDENCIAS = "incidencias"

# Tabla destino para embeddings
TABLA_DESTINO = os.getenv("TABLA_DESTINO", "documentos_embeddings")

# === CONFIGURAR LOGGING ROTATIVO ===
def configurar_logging(nombre_modulo: str):
    """
    Configura logging con RotatingFileHandler y StreamHandler.
    """
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

# === FUNCIONES DE CARGA DE DATOS ===

def cargar_desde_tabla_documentos(supabase, tabla=TABLA_ORIGEN, batch_size=1000) -> List[Dict]:
    """
    Carga registros de la tabla 'documentos' (o la que indiques en TABLA_ORIGEN).
    Retorna una lista de diccionarios con al menos 'id' y 'contenido'.
    """
    page = 0
    all_data = []
    while True:
        try:
            with LATENCIA_SUPABASE.time():
                response = supabase.table(tabla)\
                                   .select("*")\
                                   .range(page * batch_size, (page + 1) * batch_size - 1)\
                                   .execute()
            if not response.data:
                break
            logger.info(f"📥 (documentos) Página {page + 1}: {len(response.data)} registros cargados.")
            all_data.extend(response.data)
            page += 1
        except ClientResponseError as e:
            logger.error(f"❌ Error Supabase al acceder a '{tabla}': {e.message}")
            raise
        except Exception as e:
            logger.error(f"❌ Error al cargar datos desde Supabase: {e}")
            raise

    # Convertimos los registros en el formato esperado: { "id": <>, "contenido": <> }
    documentos_result = []
    for i, row in enumerate(all_data):
        if "id" not in row or "contenido" not in row:
            logger.warning(f"⚠️ Registro {i} en '{tabla}' no tiene 'id' o 'contenido'.")
            DOCUMENTOS_INVALIDOS.inc()
            continue
        documentos_result.append({
            "id": row["id"],
            "contenido": row["contenido"]
        })

    logger.info(f"📄 Total documentos cargados de '{tabla}': {len(documentos_result)}.")
    return documentos_result


def cargar_administraciones(supabase) -> List[Dict]:
    """
    Carga datos desde la tabla 'administraciones' y los transforma a un texto con info clave.
    Retorna una lista de diccionarios con { "id": <>, "contenido": <> }.
    """
    rows = supabase.table(TABLA_ADMINISTRACIONES).select("*").execute().data
    logger.info(f"📥 (administraciones) Cargados {len(rows)} registros.")
    result = []
    for i, a in enumerate(rows):
        # Ajusta las columnas según tu estructura real
        if not all(k in a for k in ("id", "nombre", "direccion", "telefono", "email")):
            logger.warning(f"⚠️ Faltan columnas en administraciones (registro {i}).")
            DOCUMENTOS_INVALIDOS.inc()
            continue

        texto = (
            f"La administración '{a['nombre']}' está en {a['direccion']}, "
            f"teléfono {a['telefono']}, email {a['email']}."
        )
        result.append({
            "id": a["id"],
            "contenido": texto
        })
    return result


def cargar_fincas(supabase) -> List[Dict]:
    """
    Carga datos desde 'fincas' y los transforma a texto.
    """
    rows = supabase.table(TABLA_FINCAS).select("*").execute().data
    logger.info(f"📥 (fincas) Cargados {len(rows)} registros.")
    result = []
    for i, f in enumerate(rows):
        # Ajusta las columnas según tu estructura
        if not all(k in f for k in ("id", "nombre", "direccion", "numero_puertas", "administracion_id")):
            logger.warning(f"⚠️ Faltan columnas en fincas (registro {i}).")
            DOCUMENTOS_INVALIDOS.inc()
            continue

        texto = (
            f"La finca '{f['nombre']}' está ubicada en {f['direccion']}, "
            f"tiene {f['numero_puertas']} puertas y pertenece a la administración con ID {f['administracion_id']}."
        )
        result.append({
            "id": f["id"],
            "contenido": texto
        })
    return result


def cargar_usuarios(supabase) -> List[Dict]:
    """
    Carga datos desde 'usuarios' y los transforma a texto.
    """
    rows = supabase.table(TABLA_USUARIOS).select("*").execute().data
    logger.info(f"📥 (usuarios) Cargados {len(rows)} registros.")
    result = []
    for i, u in enumerate(rows):
        # Ajusta las columnas según tu estructura
        # Por ejemplo, "nombre_fincas" podría ser un alias o no existir
        if not all(k in u for k in ("id", "nombre", "rol", "nombre_fincas", "direccion_finca", "telefono_movil")):
            logger.warning(f"⚠️ Faltan columnas en usuarios (registro {i}).")
            DOCUMENTOS_INVALIDOS.inc()
            continue

        texto = (
            f"El usuario '{u['nombre']}' es {u['rol']} en la finca '{u['nombre_fincas']}' "
            f"(dirección: {u['direccion_finca']}), teléfono: {u['telefono_movil']}."
        )
        result.append({
            "id": u["id"],
            "contenido": texto
        })
    return result


def cargar_incidencias(supabase) -> List[Dict]:
    """
    Carga datos desde 'incidencias' y los transforma a texto.
    """
    rows = supabase.table(TABLA_INCIDENCIAS).select("*").execute().data
    logger.info(f"📥 (incidencias) Cargados {len(rows)} registros.")
    result = []
    for i, inc in enumerate(rows):
        # Ajusta las columnas según tu estructura
        if not all(k in inc for k in ("id", "tipo", "urgencia", "finca_id", "usuario_id", "telefono_movil", "descripcion")):
            logger.warning(f"⚠️ Faltan columnas en incidencias (registro {i}).")
            DOCUMENTOS_INVALIDOS.inc()
            continue

        texto = (
            f"Incidencia tipo '{inc['tipo']}' con urgencia '{inc['urgencia']}' en finca ID {inc['finca_id']}, "
            f"reportada por usuario ID {inc['usuario_id']}. Teléfono: {inc['telefono_movil']}. "
            f"Descripción: {inc['descripcion']}."
        )
        result.append({
            "id": inc["id"],
            "contenido": texto
        })
    return result


# === PREPROCESAMIENTO DE TEXTO ===
def preprocesar_texto(texto: str) -> str:
    """
    Elimina espacios extra y trunca el texto si supera MAX_TOKENS palabras.
    """
    texto = re.sub(r'\s+', ' ', texto).strip()
    if len(texto.split()) > MAX_TOKENS:
        logger.warning("⚠️ Texto excede tamaño, se truncará.")
        texto = " ".join(texto.split()[:MAX_TOKENS])
    return texto

# === GENERAR EMBEDDINGS CON OPENAI ===
@PROCESS_TIME.time()
def generar_embeddings(textos: List[str], modelo=EMBEDDING_MODEL) -> List[List[float]]:
    """
    Llama a la API de OpenAI para generar embeddings (batch) de una lista de textos.
    """
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
    """
    Inserta un batch de registros en la tabla de Supabase.
    Se reintenta hasta MAX_RETRIES si hay errores temporales.
    """
    supabase.table(tabla).insert(batch).execute()

def guardar_embeddings(supabase, tabla: str, datos: List[dict], batch_size=BATCH_SIZE):
    """
    Divide 'datos' en lotes y los inserta en 'tabla' usando guardar_batch().
    Si un batch falla repetidamente, se escribe en failed_batches.log.
    """
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

# === CONEXIÓN SUPABASE (Circuit Breaker / Backoff) ===
@on_exception(expo, ClientResponseError, max_tries=3)
@circuit(failure_threshold=3, recovery_timeout=30)
def conectar_supabase():
    """
    Crea un cliente de Supabase con manejo de errores y circuit breaker.
    """
    if not SUPABASE_URL or not SUPABASE_KEY:
        logger.error("❌ Faltan SUPABASE_URL o SUPABASE_KEY")
        raise EnvironmentError("Variables faltantes para Supabase.")

    # Validaciones mínimas (puedes omitir si confías en la URL/KEY)
    parsed = urlparse(SUPABASE_URL)
    if not parsed.scheme or not parsed.netloc:
        logger.error("❌ Formato inválido en SUPABASE_URL")
        raise ValueError("URL inválida.")

    if len(SUPABASE_KEY) < 20:
        logger.error("❌ Formato inválido en SUPABASE_KEY (demasiado corta)")
        raise ValueError("KEY inválida.")

    logger.info(f"🔑 Conectando a Supabase: {SUPABASE_URL}")
    return create_client(SUPABASE_URL, SUPABASE_KEY)

# === EJECUCIÓN DIRECTA ===
if __name__ == "__main__":
    try:
        # Iniciar servidor Prometheus para métricas en puerto 8000
        start_http_server(8000)

        # 1. Conectarse a Supabase
        supabase = conectar_supabase()

        # 2. Cargar datos de todas las tablas
        logger.info("🚀 Cargando datos desde múltiples tablas...")
        data_documentos = cargar_desde_tabla_documentos(supabase)  # de la tabla 'documentos'
        data_administraciones = cargar_administraciones(supabase)
        data_fincas = cargar_fincas(supabase)
        data_usuarios = cargar_usuarios(supabase)
        data_incidencias = cargar_incidencias(supabase)

        # Unir todo en una sola lista
        all_data = (
            data_documentos
            + data_administraciones
            + data_fincas
            + data_usuarios
            + data_incidencias
        )

        # 3. Preprocesar y filtrar datos válidos
        textos = []
        datos_validos = []
        documentos_invalidos = 0

        for i, d in enumerate(all_data):
            # Esperamos que cada registro tenga al menos { "id": ..., "contenido": ... }
            if "id" not in d or "contenido" not in d:
                logger.error(f"❌ Registro {i} sin 'id' o 'contenido'.")
                documentos_invalidos += 1
                DOCUMENTOS_INVALIDOS.inc()
                continue

            contenido = d["contenido"].strip()
            if not contenido:
                logger.warning(f"⚠️ Registro {i} con contenido vacío.")
                documentos_invalidos += 1
                DOCUMENTOS_INVALIDOS.inc()
                continue

            texto_preprocesado = preprocesar_texto(contenido)
            textos.append(texto_preprocesado)
            # Guardamos todo el registro original, para luego añadir embedding
            datos_validos.append(d)

        if documentos_invalidos > 0:
            logger.warning(f"⚠️ Se omitieron {documentos_invalidos} registros inválidos.")

        # 4. Generar embeddings
        embeddings = generar_embeddings(textos)

        # 5. Combinar datos + embeddings y guardar
        datos_con_embeddings = []
        for i in range(len(embeddings)):
            # Incorporamos la clave 'embedding_vector' y 'vectorizado_en'
            # a cada registro
            item = {
                **datos_validos[i],
                "embedding_vector": embeddings[i],
                "vectorizado_en": datetime.utcnow().isoformat()
            }
            # Si tu tabla documentos_embeddings usa un ID propio,
            # podrías forzar un nuevo UUID:
            # item["id"] = str(uuid.uuid4())

            datos_con_embeddings.append(item)

        # Guardar en la tabla final
        guardar_embeddings(supabase, TABLA_DESTINO, datos_con_embeddings)
        logger.info("🚀 Proceso completado correctamente.")

    except Exception as e:
        logger.error(f"🚨 Error general en ejecución: {e}")
