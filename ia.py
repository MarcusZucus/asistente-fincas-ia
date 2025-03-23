# === IMPORTS ===
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
from openai import OpenAIError  # para capturar errores de OpenAI
from httpx import HTTPError
from logging.handlers import RotatingFileHandler
from prometheus_client import Histogram, start_http_server
from conexion import conectar_supabase
from circuitbreaker import circuit

# === CONFIGURACIÓN GENERAL ===
TABLA_EMBEDDINGS = os.getenv("TABLA_EMBEDDINGS", "documentos_embeddings")
# Se usará el modelo de OpenAI para vectorización, por lo que definimos:
OPENAI_EMBEDDING_MODEL = os.getenv("OPENAI_EMBEDDING_MODEL", "text-embedding-ada-002")
TOP_K = int(os.getenv("TOP_K", 3))
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "TU_API_KEY")

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

configurar_logging("ia")
logger = logging.getLogger("ia")

# === MÉTRICAS PROMETHEUS ===
GPT_RESPONSE_LATENCY = Histogram('gpt_response_latency_seconds', 'Tiempo de respuesta del modelo de IA')
SIMILITUD_SCORE = Histogram('similitud_score', 'Puntajes de similitud')
CONTEXTO_LENGTH = Histogram('contexto_length', 'Longitud del contexto en palabras')

# === CARGAR VARIABLES DE ENTORNO ===
load_dotenv()
os.environ["OPENAI_API_KEY"] = OPENAI_API_KEY
openai.api_key = OPENAI_API_KEY

# === SANITIZACIÓN DE PREGUNTA ===
def sanitizar_pregunta(pregunta: str, max_length=500) -> str:
    # Se eliminan caracteres no alfanuméricos (permitiendo acentos, ñ y signos de interrogación)
    pregunta = re.sub(r'[^\w\sáéíóúñ¿?]', '', pregunta.strip())
    return pregunta[:max_length]

# === VECTORIZACIÓN DE PREGUNTA (con OpenAI) ===
def vectorizar_pregunta(pregunta: str) -> List[float]:
    try:
        # Usamos la API de OpenAI para generar el embedding de la pregunta
        response = openai.Embedding.create(input=[pregunta], model=OPENAI_EMBEDDING_MODEL)
        vector = response["data"][0]["embedding"]
        return vector
    except Exception as e:
        logger.error(f"❌ Error vectorizando pregunta con OpenAI: {e}")
        raise

# === SIMILITUD COSENO ===
def similitud_coseno(v1: List[float], v2: List[float]) -> float:
    v1 = np.array(v1)
    v2 = np.array(v2)
    norm_v1 = np.linalg.norm(v1)
    norm_v2 = np.linalg.norm(v2)
    if norm_v1 == 0 or norm_v2 == 0:
        return 0.0
    return np.dot(v1, v2) / (norm_v1 * norm_v2)

# === TRUNCAR CONTEXTO ===
def truncar_contexto(contexto: str, max_palabras: int = 1500) -> str:
    palabras = contexto.split()
    if len(palabras) > max_palabras:
        logger.warning("⚠️ Contexto truncado por exceso de longitud.")
        return " ".join(palabras[:max_palabras])
    CONTEXTO_LENGTH.observe(len(palabras))
    return contexto

# === BÚSQUEDA DE CONTEXTO CON RPC VECTORIAL ===
def obtener_contexto_relevante(pregunta: str, supabase, k=TOP_K) -> str:
    try:
        logger.info("🔍 Recuperando contexto relevante...")
        # Vectorizamos la pregunta usando OpenAI
        pregunta_vector = vectorizar_pregunta(pregunta)
        # Se asume que el RPC 'vector_search' existe y retorna documentos con el campo 'embedding' y 'contenido'
        response = supabase.rpc('vector_search', {
            'query_embedding': pregunta_vector,
            'match_count': k * 2
        }).execute()

        if not response.data:
            logger.warning("⚠️ No se encontraron documentos relevantes.")
            return ""

        resultados = []
        for doc in response.data:
            embedding = doc.get("embedding")
            if isinstance(embedding, list) and embedding:
                score = similitud_coseno(pregunta_vector, embedding)
                SIMILITUD_SCORE.observe(score)
                resultados.append((score, doc))

        resultados.sort(key=lambda x: x[0], reverse=True)
        top_k_docs = [doc["contenido"] for _, doc in resultados[:k]]
        logger.info(f"📚 Top-{k} documentos seleccionados como contexto.")
        return truncar_contexto("\n\n".join(top_k_docs))
    except Exception as e:
        logger.error(f"❌ Error al recuperar contexto: {e}")
        raise

# === GPT (con Prometheus y circuit breaker) ===
@circuit(failure_threshold=3, recovery_timeout=60)
@GPT_RESPONSE_LATENCY.time()
def responder_con_gpt(pregunta: str, contexto: str) -> str:
    try:
        system_prompt = (
            "Eres un asistente de administración de fincas. Responde solo en base a la información del contexto. "
            "Si no encuentras respuesta, indica amablemente que no tienes información."
        )
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
        return completion.choices[0].message.content.strip()
    except OpenAIError as e:
        logger.error(f"❌ Error al generar respuesta con GPT: {e}")
        raise

# === LRU CACHE LOCAL DE RESPUESTAS ===
_respuestas_cache = {}

def responder_pregunta(pregunta: str, user_id: str = None) -> str:
    session_id = str(uuid.uuid4())[:8]
    pregunta = sanitizar_pregunta(pregunta)
    if not pregunta:
        return "Por favor, formula una pregunta válida."

    logger.info(f"🆔 Sesión: {session_id} {'(Usuario: ' + user_id + ')' if user_id else ''}")

    if pregunta in _respuestas_cache:
        logger.info(f"⚡ [{session_id}] Respuesta recuperada de caché.")
        return _respuestas_cache[pregunta]

    try:
        supabase = conectar_supabase()
        contexto = obtener_contexto_relevante(pregunta, supabase)
        respuesta = responder_con_gpt(pregunta, contexto)
        logger.info(f"✅ [{session_id}] Respuesta generada.")
        _respuestas_cache[pregunta] = respuesta
        return respuesta
    except Exception as e:
        logger.error(f"🚨 [{session_id}] Error: {e}")
        return "Hubo un problema al procesar tu solicitud. Intenta más tarde."

# === TEST MANUAL ===
if __name__ == "__main__":
    try:
        start_http_server(8010)  # Inicia el servidor de métricas en el puerto 8010
        pregunta = input("❓ Escribe tu pregunta: ")
        respuesta = responder_pregunta(pregunta)
        print(f"🧠 Respuesta:\n{respuesta}")
    except Exception as e:
        logger.error(f"❌ Error en ejecución directa: {e}")
