import os
from supabase import create_client

# Asegúrate de tener un archivo .env con SUPABASE_URL y SUPABASE_KEY,
# o bien exporta esas variables en tu entorno antes de ejecutar este script.

url = os.getenv("SUPABASE_URL")
key = os.getenv("SUPABASE_KEY")
supabase = create_client(url, key)

resp = supabase.table("usuarios").select("*").execute()
print("Resultado de la tabla usuarios:", resp.data)
