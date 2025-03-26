"""
auth.py - Módulo de Autenticación y Autorización para el sistema RAG.

Este módulo se encarga de gestionar la autenticación y autorización en el sistema.
Incluye las siguientes funcionalidades:
  - Creación de tokens de acceso (JWT) que permiten identificar a los usuarios de forma segura.
  - Verificación y decodificación de tokens para asegurar que la información es válida.
  - Recuperación de la información del usuario desde la base de datos (utilizando Supabase).
  - Decorador para restringir el acceso a funciones en función del rol del usuario.
  - Función de autenticación (login) que valida las credenciales del usuario contra la base de datos.

El módulo está pensado para entornos de producción, con manejo robusto de errores,
validaciones estrictas, y logging detallado que facilita la auditoría y la depuración.
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

# Validamos que las variables de conexión a Supabase existan. Esto es fundamental para el funcionamiento
# de la autenticación ya que se requiere conectarse a la base de datos para recuperar la información del usuario.
if not SUPABASE_URL or not SUPABASE_KEY:
    logger.error("Las credenciales de Supabase (SUPABASE_URL y SUPABASE_KEY) son obligatorias para el funcionamiento de auth.py.")
    raise EnvironmentError("Faltan variables de entorno críticas para Supabase. Verifique la configuración de su entorno.")

# Inicialización del cliente de Supabase. Este cliente se utiliza en todo el módulo para realizar consultas
# y operaciones en la base de datos, particularmente en la tabla de 'usuarios'.
supabase_client: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
logger.info("Cliente Supabase inicializado correctamente en auth.py. La conexión a la base de datos ha sido establecida.")

def create_access_token(data: dict, expires_delta: datetime.timedelta = None) -> str:
    """
    Genera y retorna un token JWT que encapsula la información del usuario.

    Este token se utiliza para identificar y autorizar al usuario en solicitudes posteriores.
    La función permite definir un tiempo de expiración personalizado; en caso de no proporcionarlo,
    se utiliza el valor por defecto especificado en ACCESS_TOKEN_EXPIRE_MINUTES.

    Args:
      data (dict): Diccionario con la información que se incluirá en el token, 
                   por ejemplo: {"sub": "identificador_usuario", "rol": "admin"}.
      expires_delta (datetime.timedelta, opcional): Duración de la validez del token.
    
    Returns:
      str: Token JWT codificado que puede ser enviado al cliente.
    
    Ejemplo:
      >>> token = create_access_token({"sub": "user123", "rol": "admin"})
      >>> print(token)
    """
    # Copiamos el diccionario de datos para no modificar el original
    to_encode = data.copy()
    
    # Calculamos el tiempo de expiración del token basado en el parámetro proporcionado o en el valor por defecto.
    if expires_delta:
        expire = datetime.datetime.utcnow() + expires_delta
    else:
        expire = datetime.datetime.utcnow() + datetime.timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    
    # Se añade la fecha de expiración al payload del token.
    to_encode.update({"exp": expire})
    
    # Se genera el token utilizando la clave secreta y el algoritmo especificado.
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=JWT_ALGORITHM)
    
    # Se registra en el log la generación exitosa del token con su fecha de expiración.
    logger.debug(f"Token generado con expiración {expire.isoformat()}. Payload: {to_encode}")
    
    return encoded_jwt

def verify_token(token: str) -> dict:
    """
    Verifica y decodifica un token JWT para asegurarse de que es válido y no ha expirado.

    Esta función decodifica el token utilizando la clave secreta y el algoritmo definido,
    y retorna el payload si la verificación es exitosa. Si el token es inválido o ha expirado,
    se lanza una excepción con un mensaje de error apropiado.

    Args:
      token (str): Token JWT que se desea verificar.

    Returns:
      dict: Diccionario con el payload contenido en el token.

    Raises:
      ValueError: Si el token es inválido o ha expirado.
    
    Ejemplo:
      >>> payload = verify_token(token)
      >>> print(payload)
    """
    try:
        # Intentamos decodificar el token utilizando la clave y el algoritmo especificados.
        payload = jwt.decode(token, SECRET_KEY, algorithms=[JWT_ALGORITHM])
        logger.debug("Token verificado correctamente. Payload decodificado: {}".format(payload))
        return payload
    except JWTError as e:
        # Se captura cualquier error durante la decodificación y se registra el error.
        logger.error(f"Fallo en la verificación del token: {e}. El token puede ser inválido o haber expirado.")
        raise ValueError("Token no válido o expirado") from e

def get_user_from_token(token: str) -> dict:
    """
    Recupera la información del usuario a partir de un token JWT válido.

    Se espera que el token contenga el identificador del usuario en el campo "sub".
    La función realiza una consulta a la tabla "usuarios" en la base de datos de Supabase para
    obtener la información completa del usuario correspondiente.

    Args:
      token (str): Token JWT que contiene la información de identificación del usuario.

    Returns:
      dict: Diccionario con la información del usuario recuperado desde la base de datos.

    Raises:
      ValueError: Si el token no contiene el identificador 'sub' o si no se encuentra el usuario.
    
    Ejemplo:
      >>> user_info = get_user_from_token(token)
      >>> print(user_info)
    """
    # Verificamos y decodificamos el token para obtener el payload.
    payload = verify_token(token)
    
    # Extraemos el identificador del usuario (se asume que se encuentra en la clave 'sub').
    user_id = payload.get("sub")
    if not user_id:
        logger.error("El token proporcionado no contiene el identificador de usuario ('sub').")
        raise ValueError("Token sin identificador de usuario")
    
    # Realizamos una consulta a la tabla "usuarios" en Supabase para recuperar la información del usuario.
    response = supabase_client.table("usuarios").select("*").eq("id", user_id).execute()
    
    # Verificamos si la consulta generó algún error.
    if response.error:
        logger.error(f"Error al recuperar el usuario desde Supabase: {response.error}")
        raise ValueError("Error al recuperar usuario")
    
    # Verificamos si se encontró algún usuario con el identificador proporcionado.
    if not response.data:
        logger.error(f"Usuario con id '{user_id}' no fue encontrado en Supabase.")
        raise ValueError("Usuario no encontrado")
    
    # Se obtiene el primer (y se asume único) registro de la respuesta.
    user = response.data[0]
    logger.debug(f"Usuario recuperado exitosamente: {user}")
    
    return user

def require_role(required_roles: list):
    """
    Decorador para restringir el acceso a funciones en función del rol del usuario.

    Este decorador espera que la función decorada reciba un parámetro nombrado 'token'
    que contenga el token JWT del usuario. Tras verificar y decodificar el token, se recupera
    la información del usuario y se valida si su rol se encuentra dentro de los roles permitidos.
    Si la verificación es exitosa, la información del usuario se inyecta en la función decorada
    mediante el parámetro 'user'.

    Ejemplo de uso:
        @require_role(["admin", "moderator"])
        def mi_funcion(*args, **kwargs):
            user = kwargs.get("user")
            # Lógica de la función
            ...

    Args:
      required_roles (list): Lista de roles permitidos para acceder a la función decorada.

    Raises:
      ValueError: Si no se proporciona el token requerido.
      PermissionError: Si el usuario no posee uno de los roles permitidos.
    
    Returns:
      function: La función decorada, que sólo se ejecutará si la validación del rol es exitosa.
    """
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            # Se espera que el token se pase como argumento nombrado 'token'
            token = kwargs.get("token")
            if not token:
                logger.error("El token de acceso es requerido para utilizar este recurso.")
                raise ValueError("Token de acceso requerido")
            
            # Recuperamos la información del usuario utilizando el token proporcionado.
            user = get_user_from_token(token)
            user_role = user.get("rol")
            
            # Se verifica si el rol del usuario está entre los roles permitidos.
            if user_role not in required_roles:
                logger.warning(f"Acceso denegado para usuario con rol '{user_role}'. Roles requeridos: {required_roles}")
                raise PermissionError("No tiene permiso para acceder a este recurso")
            
            # Inyectamos la información del usuario en los argumentos de la función decorada.
            kwargs["user"] = user
            
            # Se registra el acceso correcto y se continúa con la ejecución de la función.
            logger.debug(f"Acceso concedido para el usuario con rol '{user_role}'. Procediendo con la ejecución de la función decorada.")
            return func(*args, **kwargs)
        return wrapper
    return decorator

def authenticate(nombre_usuario: str, password: str) -> (str, dict):
    """
    Función de autenticación que valida las credenciales del usuario contra la base de datos.

    Se asume que la tabla "usuarios" de Supabase contiene al menos los campos 'nombre_usuario' y 'password'.
    Para entornos de producción se recomienda almacenar las contraseñas de forma segura (usando hashing y sal)
    en lugar de texto claro.

    Args:
      nombre_usuario (str): Nombre de usuario.
      password (str): Contraseña en texto claro (nota: en producción, se debe utilizar un mecanismo de verificación seguro).

    Returns:
      tuple: Una tupla (access_token, user) donde:
             - access_token (str): Token JWT generado que autoriza al usuario.
             - user (dict): Diccionario con la información del usuario autenticado.

    Raises:
      ValueError: Si ocurre algún error durante la autenticación o si las credenciales son inválidas.
    
    Ejemplo:
      >>> token, user_info = authenticate("usuario_test", "contraseña_segura")
      >>> print(token)
      >>> print(user_info)
    """
    logger.info(f"Iniciando el proceso de autenticación para el usuario: {nombre_usuario}")
    
    # NOTA IMPORTANTE: En producción, la contraseña debe ser verificada contra un hash almacenado de forma segura.
    response = supabase_client.table("usuarios").select("*") \
        .eq("nombre_usuario", nombre_usuario) \
        .eq("password", password) \
        .execute()
    
    # Se registra si ocurre algún error durante la consulta.
    if response.error:
        logger.error(f"Error durante la autenticación para el usuario {nombre_usuario}: {response.error}")
        raise ValueError("Error durante la autenticación")
    
    # Se verifica que se haya encontrado un usuario con las credenciales proporcionadas.
    if not response.data:
        logger.warning(f"Credenciales inválidas para el usuario {nombre_usuario}.")
        raise ValueError("Credenciales inválidas")
    
    # Se obtiene el usuario (se asume que es el primer registro devuelto).
    user = response.data[0]
    
    # Se prepara el payload para el token con el identificador y rol del usuario.
    token_data = {"sub": user["id"], "rol": user.get("rol", "user")}
    
    # Se genera el token de acceso utilizando la función create_access_token.
    access_token = create_access_token(data=token_data)
    logger.info(f"Usuario '{nombre_usuario}' autenticado correctamente. Token generado exitosamente.")
    
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
        
        # PRUEBA 2: Simulación de autenticación (requiere que exista un usuario de prueba en la base de datos).
        # Descomente y configure los siguientes valores según su entorno para probar la autenticación real.
        # nombre_usuario, password = "test_user", "test_password"
        # token, user = authenticate(nombre_usuario, password)
        # logger.info(f"Usuario autenticado: {user}")
        
        # PRUEBA 3: Uso del decorador require_role para restringir acceso.
        @require_role(["admin"])
        def recurso_protegido(*args, **kwargs):
            # Se espera que el decorador inyecte la información del usuario en 'kwargs'
            user = kwargs.get("user")
            # Se utiliza el campo 'nombre_usuario' si está disponible; de lo contrario, se usa un valor predeterminado.
            return f"Acceso concedido a {user.get('nombre_usuario', 'desconocido')}"
        
        # Se ejecuta la función protegida pasando el token generado anteriormente.
        resultado = recurso_protegido(token=token)
        logger.info(f"Resultado del recurso protegido (acceso concedido): {resultado}")
        
    except Exception as e:
        logger.error(f"Error durante las pruebas en auth.py: {e}")
