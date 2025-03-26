"""
auth.py - Módulo de Autenticación y Autorización para el sistema RAG.

Este módulo se encarga de gestionar la autenticación y autorización en el sistema.
En esta versión se actualiza la autenticación para que se efectúe escribiendo tan solo el número de teléfono.
Incluye las siguientes funcionalidades:
  - Creación de tokens de acceso (JWT) que permiten identificar a los usuarios de forma segura.
  - Verificación y decodificación de tokens para asegurar que la información es válida.
  - Recuperación de la información del usuario desde la base de datos (utilizando Supabase) filtrando por número de teléfono.
  - Decorador para restringir el acceso a funciones en función del rol del usuario.
"""

import datetime
from functools import wraps

# Se utiliza la librería python-jose para la codificación y decodificación de JWT.
from jose import jwt, JWTError  # Requiere: pip install python-jose

# Importamos el cliente de Supabase para la interacción con la base de datos.
from supabase import create_client, Client

# Importar variables de configuración y constantes definidas en el módulo central de configuración.
from config import (
    SECRET_KEY,               # Clave secreta para la firma de los tokens JWT.
    JWT_ALGORITHM,            # Algoritmo a utilizar en la codificación de los JWT.
    ACCESS_TOKEN_EXPIRE_MINUTES,  # Tiempo en minutos que durará la validez del token.
    SUPABASE_URL,             # URL de conexión a la instancia de Supabase.
    SUPABASE_KEY              # Clave de autenticación para acceder a Supabase.
)

# Importar el sistema de logging centralizado para asegurar un formato unificado en todas las salidas.
from logger import get_logger

# Crear un logger específico para este módulo con el nombre 'auth'
logger = get_logger("auth")

# Validamos que las variables de conexión a Supabase existan.
if not SUPABASE_URL or not SUPABASE_KEY:
    logger.error("Las credenciales de Supabase (SUPABASE_URL y SUPABASE_KEY) son obligatorias para el funcionamiento de auth.py.")
    raise EnvironmentError("Faltan variables de entorno críticas para Supabase. Verifique la configuración de su entorno.")

# Inicialización del cliente de Supabase.
supabase_client: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
logger.info("Cliente Supabase inicializado correctamente en auth.py. La conexión a la base de datos ha sido establecida.")

def create_access_token(data: dict, expires_delta: datetime.timedelta = None) -> str:
    """
    Genera y retorna un token JWT que encapsula la información del usuario.

    Args:
      data (dict): Diccionario con la información que se incluirá en el token, 
                   por ejemplo: {"sub": "identificador_usuario", "rol": "admin"}.
      expires_delta (datetime.timedelta, opcional): Duración de la validez del token.
    
    Returns:
      str: Token JWT codificado que puede ser enviado al cliente.
    """
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.datetime.utcnow() + expires_delta
    else:
        expire = datetime.datetime.utcnow() + datetime.timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=JWT_ALGORITHM)
    logger.debug(f"Token generado con expiración {expire.isoformat()}. Payload: {to_encode}")
    return encoded_jwt

def verify_token(token: str) -> dict:
    """
    Verifica y decodifica un token JWT para asegurarse de que es válido y no ha expirado.

    Args:
      token (str): Token JWT que se desea verificar.

    Returns:
      dict: Diccionario con el payload contenido en el token.

    Raises:
      ValueError: Si el token es inválido o ha expirado.
    """
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[JWT_ALGORITHM])
        logger.debug("Token verificado correctamente. Payload decodificado: {}".format(payload))
        return payload
    except JWTError as e:
        logger.error(f"Fallo en la verificación del token: {e}. El token puede ser inválido o haber expirado.")
        raise ValueError("Token no válido o expirado") from e

def get_user_from_token(token: str) -> dict:
    """
    Recupera la información del usuario a partir de un token JWT válido.

    Args:
      token (str): Token JWT que contiene la información de identificación del usuario.

    Returns:
      dict: Diccionario con la información del usuario recuperado desde la base de datos.

    Raises:
      ValueError: Si el token no contiene el identificador 'sub' o si no se encuentra el usuario.
    """
    payload = verify_token(token)
    user_id = payload.get("sub")
    if not user_id:
        logger.error("El token proporcionado no contiene el identificador de usuario ('sub').")
        raise ValueError("Token sin identificador de usuario")
    response = supabase_client.table("usuarios").select("*").eq("id", user_id).execute()
    if response.error:
        logger.error(f"Error al recuperar el usuario desde Supabase: {response.error}")
        raise ValueError("Error al recuperar usuario")
    if not response.data:
        logger.error(f"Usuario con id '{user_id}' no fue encontrado en Supabase.")
        raise ValueError("Usuario no encontrado")
    user = response.data[0]
    logger.debug(f"Usuario recuperado exitosamente: {user}")
    return user

def require_role(required_roles: list):
    """
    Decorador para restringir el acceso a funciones en función del rol del usuario.

    Args:
      required_roles (list): Lista de roles permitidos para acceder a la función decorada.

    Returns:
      function: La función decorada, que solo se ejecutará si la validación del rol es exitosa.
    """
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            token = kwargs.get("token")
            if not token:
                logger.error("El token de acceso es requerido para utilizar este recurso.")
                raise ValueError("Token de acceso requerido")
            user = get_user_from_token(token)
            user_role = user.get("rol")
            if user_role not in required_roles:
                logger.warning(f"Acceso denegado para usuario con rol '{user_role}'. Roles requeridos: {required_roles}")
                raise PermissionError("No tiene permiso para acceder a este recurso")
            kwargs["user"] = user
            logger.debug(f"Acceso concedido para el usuario con rol '{user_role}'. Procediendo con la ejecución de la función decorada.")
            return func(*args, **kwargs)
        return wrapper
    return decorator

def authenticate_by_phone(phone_number: str) -> (str, dict):
    """
    Función de autenticación que valida el número de teléfono del usuario contra la base de datos.

    Se realiza una consulta filtrando únicamente por el campo "telefono_movil".
    Se asume que cada número de teléfono es único en la tabla "usuarios".

    Args:
      phone_number (str): Número de teléfono a validar.

    Returns:
      tuple: (access_token, user) donde:
             - access_token (str): Token JWT generado.
             - user (dict): Información del usuario autenticado.

    Raises:
      ValueError: Si ocurre algún error durante la autenticación o si el número no corresponde a ningún usuario.
    """
    logger.info(f"Iniciando el proceso de autenticación para el número: {phone_number}")
    
    response = supabase_client.table("usuarios").select("*").eq("telefono_movil", phone_number).execute()
    
    if response.error:
        logger.error(f"Error durante la autenticación para el número {phone_number}: {response.error}")
        raise ValueError("Error durante la autenticación")
    
    if not response.data:
        logger.warning(f"Número de teléfono no encontrado: {phone_number}")
        raise ValueError("Número de teléfono inválido")
    
    user = response.data[0]
    token_data = {"sub": user["id"], "rol": user.get("rol", "user")}
    access_token = create_access_token(data=token_data)
    logger.info(f"Usuario autenticado exitosamente: {user.get('nombre_usuario', user['id'])}. Token generado.")
    
    return access_token, user

# Se incluye una sección de pruebas y ejemplos que se ejecutan cuando se corre este módulo directamente.
if __name__ == "__main__":
    try:
        # PRUEBA 1: Generación y verificación de token de prueba.
        test_data = {"sub": "usuario_test", "rol": "admin"}
        token = create_access_token(test_data)
        logger.info(f"Token generado para pruebas: {token}")
        
        payload = verify_token(token)
        logger.info(f"Payload decodificado y verificado: {payload}")
        
        # PRUEBA 2: Simulación de autenticación utilizando un número de teléfono.
        # Nota: Asegúrate de que exista un usuario en la tabla "usuarios" con un campo 'telefono_movil' igual a '1234567890'
        phone = "1234567890"  # Reemplaza este valor con un número válido registrado en Supabase
        token, user = authenticate_by_phone(phone)
        logger.info(f"Usuario autenticado: {user}")
        
        # PRUEBA 3: Uso del decorador require_role para restringir acceso.
        @require_role(["admin"])
        def recurso_protegido(*args, **kwargs):
            user = kwargs.get("user")
            return f"Acceso concedido a {user.get('nombre_usuario', 'desconocido')}"
        
        resultado = recurso_protegido(token=token)
        logger.info(f"Resultado del recurso protegido (acceso concedido): {resultado}")
        
    except Exception as e:
        logger.error(f"Error durante las pruebas en auth.py: {e}")
