"""
numero.py - Módulo para identificar a un usuario a partir de su número de teléfono.

Este script consulta la tabla "usuarios" en Supabase utilizando el número de teléfono recibido
y devuelve la información del usuario si se encuentra registrado. Es útil para realizar una 
autenticación automática en canales como WhatsApp donde el webhook envía el número del remitente.
"""

from conexion import conectar_supabase
from logger import get_logger

# Configurar logger para este módulo
logger = get_logger("numero")

def identificar_usuario_por_numero(numero: str) -> dict:
    """
    Consulta la tabla "usuarios" en Supabase buscando un usuario cuyo campo "telefono_movil"
    coincida con el número proporcionado.

    Args:
        numero (str): Número de teléfono a buscar.

    Returns:
        dict: Información del usuario si se encuentra, o un diccionario vacío si no se encuentra.
    """
    try:
        logger.info(f"Buscando usuario con número: {numero}")
        supabase = conectar_supabase()
        # Realiza la consulta en la tabla "usuarios" buscando coincidencia exacta en el campo "telefono_movil"
        response = supabase.table("usuarios").select("*").eq("telefono_movil", numero).execute()
        
        if response.error:
            logger.error(f"Error en la consulta: {response.error}")
            return {}

        if response.data and len(response.data) > 0:
            usuario = response.data[0]
            logger.info(f"Usuario encontrado: {usuario.get('nombre', 'Desconocido')}")
            return usuario
        else:
            logger.warning("No se encontró usuario con ese número.")
            return {}
    except Exception as e:
        logger.error(f"Excepción al identificar usuario: {e}")
        return {}

if __name__ == "__main__":
    # Para realizar pruebas de forma interactiva
    numero_input = input("Introduce tu número de teléfono (formato almacenado en la BD): ").strip()
    usuario = identificar_usuario_por_numero(numero_input)
    if usuario:
        print(f"Hola {usuario.get('nombre', 'usuario')}, ¡bienvenido!")
    else:
        print("No se encontró ningún usuario registrado con ese número.")
