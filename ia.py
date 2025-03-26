"""
ia.py - Módulo de Inteligencia Artificial para el Sistema RAG

Este módulo se encarga de:
  - Generar embeddings de preguntas utilizando la API de OpenAI.
  - Sanitizar y preprocesar las preguntas recibidas.
  - Calcular la similitud coseno entre vectores para comparar embeddings.
  - Truncar el contexto para evitar que sea demasiado extenso.
  - Realizar una búsqueda de contexto relevante en Supabase mediante una función RPC.
  - Generar respuestas utilizando el modelo ChatCompletion de OpenAI (GPT-3.5-turbo) con un sistema de 
    circuit breaker y monitorización de latencia a través de Prometheus.
  - Implementar un cache local (LRU) para evitar la recalculación de respuestas repetidas.
  
El módulo está diseñado para integrarse perfectamente con la configuración central (variables de entorno, 
logging, métricas y conexión a Supabase) y utiliza mecanismos de resiliencia y monitorización para entornos 
de producción. Cada función incluye documentación y comentarios detallados que explican su funcionalidad 
y manejo de errores.
"""

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
from openai import OpenAIError  # Se usa para capturar errores específicos de OpenAI
from httpx import HTTPError
from logging.handlers import RotatingFileHandler
from prometheus_client import Histogram, start_http_server
from conexion import conectar_supabase
from circuitbreaker import circuit

# =============================================================================
# CONFIGURACIÓN GENERAL Y VARIABLES DE ENTORNO
# =============================================================================

# Cargar variables de entorno (asegurando que se utilicen configuraciones locales o de producción)
load_dotenv()

# Definir constantes de configuración
TABLA_EMBEDDINGS = os.getenv("TABLA_EMBEDDINGS", "documentos_embeddings")
# Modelo a usar para generar embeddings, actualmente se utiliza text-embedding-ada-002 con 1536 dimensiones.
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "text-embedding-ada-002")
# Número de documentos a recuperar (top-k) durante la búsqueda de contexto.
TOP_K = int(os.getenv("TOP_K", 3))
# Clave de API de OpenAI; en producción debe configurarse correctamente.
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "TU_API_KEY")

# Configurar la API key para OpenAI
os.environ["OPENAI_API_KEY"] = OPENAI_API_KEY
openai.api_key = OPENAI_API_KEY

# =============================================================================
# CONFIGURAR LOGGING
# =============================================================================

def configurar_logging(nombre_modulo: str):
    """
    Configura el logging para el módulo indicado utilizando:
      - Un RotatingFileHandler para registrar en archivo con límite de tamaño y respaldo.
      - Un StreamHandler para imprimir en consola.
      - Un formato simple que incluye timestamp, nivel y mensaje.
    
    Si el logger ya tiene handlers configurados, se omite la reconfiguración.
    
    Args:
      nombre_modulo (str): Nombre del módulo para el logging.
    """
    logger = logging.getLogger(nombre_modulo)
    if logger.handlers:
        return  # Evita reconfigurar si ya existen handlers
    logger.setLevel(logging.INFO)
    formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
    log_filename = f"{nombre_modulo}.log"
    file_handler = RotatingFileHandler(log_filename, maxBytes=5 * 1024 * 1024, backupCount=3)
    file_handler.setFormatter(formatter)
    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)
    logger.addHandler(file_handler)
    logger.addHandler(stream_handler)

# Configurar el logger para este módulo
configurar_logging("ia")
logger = logging.getLogger("ia")

# =============================================================================
# MÉTRICAS PROMETHEUS
# =============================================================================

# Mide el tiempo que tarda la respuesta del modelo de OpenAI
GPT_RESPONSE_LATENCY = Histogram('gpt_response_latency_seconds', 'Tiempo de respuesta del modelo de IA')
# Mide los puntajes de similitud coseno para las comparaciones de embeddings
SIMILITUD_SCORE = Histogram('similitud_score', 'Puntajes de similitud')
# Mide la longitud del contexto en palabras antes de enviarlo a GPT
CONTEXTO_LENGTH = Histogram('contexto_length', 'Longitud del contexto en palabras')

# =============================================================================
# VECTORIZACIÓN DE PREGUNTA CON OPENAI
# =============================================================================

def vectorizar_pregunta(pregunta: str) -> List[float]:
    """
    Genera el embedding (vectorización) de la pregunta utilizando la API de OpenAI.
    
    Se espera que el modelo 'text-embedding-ada-002' devuelva un vector de 1536 dimensiones.
    En caso de error, se captura la excepción y se registra en el log.
    
    Args:
      pregunta (str): La pregunta en texto que se desea vectorizar.
    
    Returns:
      List[float]: Lista de floats representando el embedding de la pregunta.
    
    Ejemplo:
      >>> embedding = vectorizar_pregunta("¿Cómo contacto al portero?")
      >>> print(len(embedding))
      1536
    """
    try:
        # Se realiza la llamada a la API de OpenAI para generar el embedding
        response = openai.Embedding.create(
            input=[pregunta],
            model=EMBEDDING_MODEL
        )
        embedding = response["data"][0]["embedding"]
        logger.debug("Embedding generado correctamente para la pregunta.")
        return embedding
    except Exception as e:
        logger.error(f"Error vectorizando pregunta: {e}")
        raise

# =============================================================================
# SANITIZACIÓN DE PREGUNTA
# =============================================================================

def sanitizar_pregunta(pregunta: str, max_length: int = 500) -> str:
    """
    Limpia la pregunta eliminando caracteres no permitidos, manteniendo letras, números,
    espacios, acentos, la letra ñ y signos de interrogación.
    
    Además, limita la longitud de la pregunta a max_length caracteres para evitar 
    solicitudes excesivamente largas.
    
    Args:
      pregunta (str): Pregunta original a sanitizar.
      max_length (int, opcional): Longitud máxima permitida (default: 500).
    
    Returns:
      str: Pregunta sanitizada y truncada si es necesario.
    
    Ejemplo:
      >>> sanitizar_pregunta("¿Cómo puedo contactar al portero?!@#$")
      "Cómo puedo contactar al portero"
    """
    # Elimina caracteres que no sean alfanuméricos, espacios o signos permitidos.
    pregunta = re.sub(r'[^\w\sáéíóúñ¿?]', '', pregunta.strip())
    # Se trunca la pregunta a la longitud máxima permitida
    return pregunta[:max_length]

# =============================================================================
# CÁLCULO DE SIMILITUD COSENO
# =============================================================================

def similitud_coseno(v1: List[float], v2: List[float]) -> float:
    """
    Calcula la similitud coseno entre dos vectores (listas de floats).
    
    La similitud coseno es una medida que varía entre -1 y 1, donde 1 indica que
    los vectores son idénticos en dirección.
    
    Args:
      v1 (List[float]): Primer vector.
      v2 (List[float]): Segundo vector.
    
    Returns:
      float: Valor de similitud coseno. Retorna 0.0 si alguno de los vectores tiene norma 0.
    
    Ejemplo:
      >>> similitud = similitud_coseno([1, 0], [0, 1])
      >>> print(similitud)
      0.0
    """
    v1 = np.array(v1)
    v2 = np.array(v2)
    norm_v1 = np.linalg.norm(v1)
    norm_v2 = np.linalg.norm(v2)
    if norm_v1 == 0 or norm_v2 == 0:
        return 0.0
    score = np.dot(v1, v2) / (norm_v1 * norm_v2)
    return score

# =============================================================================
# TRUNCAR CONTEXTO
# =============================================================================

def truncar_contexto(contexto: str, max_palabras: int = 1500) -> str:
    """
    Limita el contexto concatenado a un número máximo de palabras para evitar sobrecargar
    la entrada de ChatGPT.
    
    Si el contexto excede el número máximo de palabras, se trunca y se registra una advertencia.
    Se observa la longitud del contexto en palabras a través de Prometheus.
    
    Args:
      contexto (str): Texto del contexto completo.
      max_palabras (int, opcional): Número máximo de palabras permitidas (default: 1500).
    
    Returns:
      str: Contexto truncado si es necesario, o el contexto original.
    
    Ejemplo:
      >>> truncar_contexto("palabra " * 1600)
      "palabra palabra ... (hasta 1500 palabras)"
    """
    palabras = contexto.split()
    if len(palabras) > max_palabras:
        logger.warning("Contexto truncado por exceso de longitud.")
        return " ".join(palabras[:max_palabras])
    # Registrar la longitud del contexto en la métrica de Prometheus
    CONTEXTO_LENGTH.observe(len(palabras))
    return contexto

# =============================================================================
# BÚSQUEDA DE CONTEXTO CON RPC VECTORIAL EN SUPABASE
# =============================================================================

def obtener_contexto_relevante(pregunta: str, supabase, k: int = TOP_K) -> str:
    """
    Realiza una búsqueda de contexto relevante en la base de datos de Supabase mediante una función RPC.

    La función RPC 'vector_search' se invoca con el embedding de la pregunta y se solicitan 2*k registros
    para filtrar y seleccionar los top k documentos en función de la similitud coseno.
    
    Se concatenan los textos de los documentos relevantes y se trunca el resultado si excede la cantidad
    máxima de palabras permitidas.
    
    Args:
      pregunta (str): Pregunta en texto que se utiliza para generar el embedding y buscar contexto.
      supabase: Cliente de Supabase previamente conectado.
      k (int, opcional): Número de documentos relevantes a seleccionar (default: TOP_K).
    
    Returns:
      str: Contexto concatenado de los top k documentos, o una cadena vacía si no se encuentra contexto.
    
    Ejemplo:
      >>> contexto = obtener_contexto_relevante("¿Cómo contactar al portero?", supabase)
      >>> print(contexto)
    """
    try:
        logger.info("Recuperando contexto relevante para la pregunta.")
        # Generar el embedding de la pregunta
        pregunta_vector = vectorizar_pregunta(pregunta)

        # Invocar la función RPC 'vector_search' en Supabase
        response = supabase.rpc('vector_search', {
            'query_embedding': pregunta_vector,
            'match_count': k * 2  # Se solicitan 2k registros para un filtrado posterior
        }).execute()

        logger.debug(f"Respuesta RPC de 'vector_search': {response.data}")

        # Si no se obtienen registros, se informa y se retorna cadena vacía
        if not response.data:
            logger.warning("No se encontraron documentos relevantes en la búsqueda.")
            return ""

        resultados = []
        # Procesar cada registro devuelto por la consulta RPC
        for doc in response.data:
            embedding = doc.get("embedding_vector")
            contenido = doc.get("contenido", "")
            # Verificar que el embedding es una lista válida
            if isinstance(embedding, list) and embedding:
                # Calcular el score de similitud coseno entre el embedding de la pregunta y el del documento
                score = similitud_coseno(pregunta_vector, embedding)
                SIMILITUD_SCORE.observe(score)
                resultados.append((score, contenido))
            else:
                logger.debug(f"Documento sin embedding válido: {doc}")

        # Si después de procesar no hay documentos válidos, se retorna cadena vacía
        if not resultados:
            logger.warning("Ningún documento contenía un embedding válido.")
            return ""

        # Ordenar los documentos por score en orden descendente y seleccionar los top k
        resultados.sort(key=lambda x: x[0], reverse=True)
        top_k_docs = [doc_text for _, doc_text in resultados[:k]]
        logger.info(f"Top-{k} documentos seleccionados como contexto.")

        # Concatenar los documentos seleccionados y truncar el contexto si es necesario
        contexto_concatenado = "\n\n".join(top_k_docs)
        return truncar_contexto(contexto_concatenado)

    except Exception as e:
        logger.error(f"Error al recuperar contexto: {e}")
        raise

# =============================================================================
# RESPUESTA CON GPT USANDO CHATCOMPLETION (CON PROMETHEUS Y CIRCUIT BREAKER)
# =============================================================================

@circuit(failure_threshold=3, recovery_timeout=60)
@GPT_RESPONSE_LATENCY.time()
def responder_con_gpt(pregunta: str, contexto: str) -> str:
    """
    Genera una respuesta utilizando el modelo ChatCompletion de OpenAI (gpt-3.5-turbo).

    La función arma un prompt de sistema que describe el contexto de administración de fincas y
    luego envía el mensaje del usuario junto con el contexto. Se implementa un circuito breaker
    para evitar múltiples intentos en fallos y se mide la latencia mediante Prometheus.

    Args:
      pregunta (str): Pregunta del usuario.
      contexto (str): Contexto relevante obtenido de Supabase basado en embeddings.
    
    Returns:
      str: Respuesta generada por el modelo de IA.
    
    Raises:
      OpenAIError: Si ocurre un error durante la llamada a la API de OpenAI.
    
    Ejemplo:
      >>> respuesta = responder_con_gpt("¿Cómo contacto al portero?", contexto)
      >>> print(respuesta)
    """
    try:
        # Definición de un prompt de sistema que orienta la respuesta de GPT
        system_prompt = (
            "Eres un asistente experto en administración de fincas. Tu función es responder únicamente basándote en la "
            "información disponible en el contexto proporcionado. Utiliza el contenido recuperado de documentos embeddings, "
            "que integran información de administraciones, fincas, usuarios e incidencias. Si el contexto es insuficiente, "
            "indícalo amablemente al usuario. Emplea un lenguaje claro y preciso, adaptado a las necesidades de administración "
            "de fincas. No inventes datos; responde solo con lo que se te proporciona."
        )
        # Construir la lista de mensajes que se enviará a OpenAI, iniciando con el mensaje de sistema
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"Contexto:\n{contexto}\n\nPregunta: {pregunta}"}
        ]
        # Realizar la llamada a la API de OpenAI para obtener la respuesta de GPT
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

# Diccionario para almacenar respuestas previamente calculadas y evitar recomputación
_respuestas_cache = {}

def responder_pregunta(pregunta: str, user_id: str = None) -> str:
    """
    Orquesta la obtención del contexto relevante y la generación final de la respuesta.

    Implementa un cache local para almacenar respuestas de preguntas previamente procesadas,
    lo que mejora el rendimiento al evitar llamadas redundantes a la API de OpenAI y a la búsqueda en Supabase.
    
    El proceso es el siguiente:
      1. Sanitiza la pregunta.
      2. Si la pregunta es vacía, se solicita que se formule una pregunta válida.
      3. Si la pregunta ya existe en el cache, se retorna la respuesta almacenada.
      4. Se conecta a Supabase y se obtiene el contexto relevante basado en embeddings.
      5. Se genera la respuesta usando GPT y se almacena en el cache.
    
    Args:
      pregunta (str): Pregunta formulada por el usuario.
      user_id (str, opcional): Identificador del usuario, para logging.
    
    Returns:
      str: Respuesta generada o mensaje de error en caso de fallo.
    
    Ejemplo:
      >>> respuesta = responder_pregunta("¿Cómo contacto al portero?", user_id="12345")
      >>> print(respuesta)
    """
    # Generar un identificador de sesión para seguimiento en los logs
    session_id = str(uuid.uuid4())[:8]
    # Sanitizar la pregunta para asegurar que cumple con los requisitos de formato y longitud
    pregunta = sanitizar_pregunta(pregunta)
    if not pregunta:
        return "Por favor, formula una pregunta válida."

    logger.info(f"Sesión {session_id} (Usuario: {user_id}) iniciada. Procesando la pregunta.")
    
    # Verificar si la respuesta para esta pregunta ya está en el cache
    if pregunta in _respuestas_cache:
        logger.info(f"Sesión {session_id}: Respuesta recuperada del cache.")
        return _respuestas_cache[pregunta]

    try:
        # Conectar a Supabase para obtener el contexto relevante
        supabase = conectar_supabase()
        contexto = obtener_contexto_relevante(pregunta, supabase)
        
        # Generar la respuesta utilizando el modelo GPT, basándose en la pregunta y el contexto
        respuesta = responder_con_gpt(pregunta, contexto)
        logger.info(f"Sesión {session_id}: Respuesta generada correctamente.")
        
        # Almacenar la respuesta en el cache local
        _respuestas_cache[pregunta] = respuesta
        return respuesta
    except Exception as e:
        logger.error(f"Sesión {session_id}: Error durante el procesamiento de la pregunta: {e}")
        return "Hubo un problema al procesar tu solicitud. Intenta más tarde."

# =============================================================================
# PRUEBA MANUAL (EJECUCIÓN DIRECTA)
# =============================================================================

if __name__ == "__main__":
    try:
        # Iniciar el servidor de métricas de Prometheus en el puerto 8010 para monitorización
        start_http_server(8010)
        # Solicitar al usuario que ingrese una pregunta en la consola
        pregunta = input("Escribe tu pregunta: ")
        # Procesar la pregunta utilizando la función responder_pregunta
        respuesta = responder_pregunta(pregunta)
        # Imprimir la respuesta generada en la consola
        print(f"Respuesta:\n{respuesta}")
    except Exception as e:
        logger.error(f"Error en ejecución directa: {e}")
