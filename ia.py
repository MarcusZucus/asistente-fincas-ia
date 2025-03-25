import logging
import os
import re
import json
import uuid
import numpy as np
from typing import List
from datetime import datetime
from dotenv import load_dotenv
import openai
from openai import OpenAIError  # Para capturar errores de OpenAI
from httpx import HTTPError
from logging.handlers import RotatingFileHandler
from prometheus_client import Histogram, start_http_server
from conexion import conectar_supabase
from circuitbreaker import circuit

# =============================================================================
# CONFIGURACIÓN GENERAL
# =============================================================================
TABLA_EMBEDDINGS = os.getenv("TABLA_EMBEDDINGS", "documentos_embeddings")
# Se usa el modelo text-embedding-ada-002 para indexar y consultar embeddings de 1536 dimensiones.
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "text-embedding-ada-002")
TOP_K = int(os.getenv("TOP_K", 3))
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "TU_API_KEY")

# =============================================================================
# CONFIGURAR LOGGING
# =============================================================================
def configurar_logging(nombre_modulo: str):
    logger = logging.getLogger(nombre_modulo)
    if logger.handlers:
        return
    logger.setLevel(logging.INFO)
    formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
    log_filename = f"{nombre_modulo}.log"
    file_handler = RotatingFileHandler(log_filename, maxBytes=5 * 1024 * 1024, backupCount=3)
    file_handler.setFormatter(formatter)
    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)
    logger.addHandler(file_handler)
    logger.addHandler(stream_handler)

configurar_logging("ia")
logger = logging.getLogger("ia")

# =============================================================================
# MÉTRICAS PROMETHEUS
# =============================================================================
GPT_RESPONSE_LATENCY = Histogram('gpt_response_latency_seconds', 'Tiempo de respuesta del modelo de IA')
SIMILITUD_SCORE = Histogram('similitud_score', 'Puntajes de similitud')
CONTEXTO_LENGTH = Histogram('contexto_length', 'Longitud del contexto en palabras')

# =============================================================================
# CONFIGURAR VARIABLES DE ENTORNO Y OPENAI
# =============================================================================
load_dotenv()
os.environ["OPENAI_API_KEY"] = OPENAI_API_KEY
openai.api_key = OPENAI_API_KEY

# =============================================================================
# VECTORIZACIÓN DE PREGUNTA CON OPENAI
# =============================================================================
def vectorizar_pregunta(pregunta: str) -> List[float]:
    """
    Genera el embedding de la pregunta utilizando la API de OpenAI.
    Se espera un vector de 1536 dimensiones (modelo text-embedding-ada-002).
    """
    try:
        response = openai.Embedding.create(
            input=[pregunta],
            model=EMBEDDING_MODEL
        )
        embedding = response["data"][0]["embedding"]
        return embedding
    except Exception as e:
        logger.error(f"Error vectorizando pregunta: {e}")
        raise

# =============================================================================
# SANITIZACIÓN DE PREGUNTA
# =============================================================================
def sanitizar_pregunta(pregunta: str, max_length=500) -> str:
    """
    Limpia la pregunta eliminando caracteres no alfanuméricos,
    permitiendo acentos, ñ y signos de interrogación.
    """
    pregunta = re.sub(r'[^\w\sáéíóúñ¿?]', '', pregunta.strip())
    return pregunta[:max_length]

# =============================================================================
# CÁLCULO DE SIMILITUD COSENO
# =============================================================================
def similitud_coseno(v1: List[float], v2: List[float]) -> float:
    """
    Calcula la similitud coseno entre dos vectores (listas de floats).
    """
    v1 = np.array(v1)
    v2 = np.array(v2)
    norm_v1 = np.linalg.norm(v1)
    norm_v2 = np.linalg.norm(v2)
    if norm_v1 == 0 or norm_v2 == 0:
        return 0.0
    return np.dot(v1, v2) / (norm_v1 * norm_v2)

# =============================================================================
# TRUNCAR CONTEXTO
# =============================================================================
def truncar_contexto(contexto: str, max_palabras: int = 1500) -> str:
    """
    Limita el contexto a un número máximo de palabras
    para evitar entradas excesivas a ChatGPT.
    """
    palabras = contexto.split()
    if len(palabras) > max_palabras:
        logger.warning("Contexto truncado por exceso de longitud.")
        return " ".join(palabras[:max_palabras])
    CONTEXTO_LENGTH.observe(len(palabras))
    return contexto

# =============================================================================
# BÚSQUEDA DE CONTEXTO CON RPC VECTORIAL
# =============================================================================
def obtener_contexto_relevante(pregunta: str, supabase, k=TOP_K) -> str:
    """
    Llama a la función RPC 'vector_search' en Supabase, 
    que debe devolver registros con los campos 'contenido' y 'embedding_vector'.
    Ordena localmente los resultados por similitud coseno y devuelve el contexto concatenado.
    """
    try:
        logger.info("Recuperando contexto relevante...")
        pregunta_vector = vectorizar_pregunta(pregunta)

        # Llamada a la función RPC con la query
        response = supabase.rpc('vector_search', {
            'query_embedding': pregunta_vector,
            'match_count': k * 2  # recuperamos 2k para filtrar posteriormente
        }).execute()

        logger.debug(f"Respuesta RPC vector_search: {response.data}")

        if not response.data:
            logger.warning("No se encontraron documentos relevantes.")
            return ""

        resultados = []
        for doc in response.data:
            embedding = doc.get("embedding_vector")
            contenido = doc.get("contenido", "")
            if isinstance(embedding, list) and embedding:
                score = similitud_coseno(pregunta_vector, embedding)
                SIMILITUD_SCORE.observe(score)
                resultados.append((score, contenido))
            else:
                logger.debug(f"Documento sin embedding válido: {doc}")

        if not resultados:
            logger.warning("Ningún documento contenía un embedding válido.")
            return ""

        # Ordenar por score descendente y seleccionar los top k
        resultados.sort(key=lambda x: x[0], reverse=True)
        top_k_docs = [doc_text for _, doc_text in resultados[:k]]
        logger.info(f"Top-{k} documentos seleccionados como contexto.")
        return truncar_contexto("\n\n".join(top_k_docs))

    except Exception as e:
        logger.error(f"Error al recuperar contexto: {e}")
        raise

# =============================================================================
# RESPUESTA CON GPT (CON PROMETHEUS Y CIRCUIT BREAKER)
# =============================================================================
@circuit(failure_threshold=3, recovery_timeout=60)
@GPT_RESPONSE_LATENCY.time()
def responder_con_gpt(pregunta: str, contexto: str) -> str:
    """
    Genera una respuesta con ChatCompletion de OpenAI usando el contexto provisto.
    """
    try:
        system_prompt = """Eres un asistente experto en administración de fincas. Tu función es responder únicamente basándote en la información disponible en el contexto que te provea el sistema RAG, el cual utiliza embeddings generados a partir de datos extraídos de múltiples tablas de la base de datos Supabase. En particular, dispones de registros procesados y almacenados en la tabla documentos_embeddings, que integran información de las siguientes fuentes:

• Administraciones: Datos completos de las administraciones (nombre, dirección, teléfono, email).
• Fincas: Información detallada de cada finca (nombre_finca, dirección_finca, número de puertas, administración asociada, etc.).
• Usuarios: Registros de usuarios relacionados con las fincas, que incluyen nombre, rol, datos de contacto y la finca a la que pertenecen.
• Incidencias: Registros de incidencias con detalles como tipo, urgencia, descripción y datos de contacto.

El sistema RAG utiliza una búsqueda vectorial en la tabla documentos_embeddings para recuperar el contexto más relevante en función de la pregunta del usuario. Recuerda que:

– Tu respuesta debe estar estrictamente fundamentada en el contenido recuperado (los documentos embeddings) y en la información procesada; si el contexto es insuficiente, debes indicarlo amablemente al usuario.
– El usuario se identifica dinámicamente y tiene asignado un rol. Las políticas de seguridad aseguran que solo se acceda a la información pertinente a la finca o fincas asignadas.
– Emplea un lenguaje claro y preciso, adaptado a las necesidades de administración de fincas.

Cuando respondas, utiliza únicamente el contexto proporcionado y no inventes datos. Si la información no es suficiente para ofrecer una respuesta completa, indícalo al usuario."""
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"Contexto:\n{contexto}\n\nPregunta: {pregunta}"}
        ]
        completion = openai.ChatCompletion.create(
            model="gpt-3.5-turbo",
            messages=messages,
            temperature=0.2,
            timeout=15
        )
        respuesta = completion.choices[0].message.content.strip()
        return respuesta
    except OpenAIError as e:
        logger.error(f"Error al generar respuesta con GPT: {e}")
        raise

# =============================================================================
# CACHE LOCAL PARA RESPUESTAS (LRU)
# =============================================================================
_respuestas_cache = {}

def responder_pregunta(pregunta: str, user_id: str = None) -> str:
    """
    Orquesta la obtención del contexto relevante y la generación de la respuesta final.
    Implementa un cache local para evitar recalcular respuestas.
    """
    session_id = str(uuid.uuid4())[:8]
    pregunta = sanitizar_pregunta(pregunta)
    if not pregunta:
        return "Por favor, formula una pregunta válida."

    logger.info(f"Sesión {session_id} (Usuario: {user_id}) iniciado.")

    if pregunta in _respuestas_cache:
        logger.info(f"Sesión {session_id}: Respuesta recuperada de cache.")
        return _respuestas_cache[pregunta]

    try:
        supabase = conectar_supabase()
        contexto = obtener_contexto_relevante(pregunta, supabase)
        respuesta = responder_con_gpt(pregunta, contexto)
        logger.info(f"Sesión {session_id}: Respuesta generada correctamente.")
        _respuestas_cache[pregunta] = respuesta
        return respuesta
    except Exception as e:
        logger.error(f"Sesión {session_id}: Error durante el procesamiento: {e}")
        return "Hubo un problema al procesar tu solicitud. Intenta más tarde."

# =============================================================================
# PRUEBA MANUAL (EJECUCIÓN DIRECTA)
# =============================================================================
if __name__ == "__main__":
    try:
        # Inicia el servidor de métricas en el puerto 8010
        start_http_server(8010)
        pregunta = input("Escribe tu pregunta: ")
        respuesta = responder_pregunta(pregunta)
        print(f"Respuesta:\n{respuesta}")
    except Exception as e:
        logger.error(f"Error en ejecución directa: {e}")
