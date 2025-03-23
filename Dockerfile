FROM python:3.11-slim

# Instalar dependencias del sistema necesarias
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libssl-dev \
    libffi-dev \
    python3-dev \
  && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copiar primero los requisitos para aprovechar el cache de Docker en instalaciones posteriores
COPY requirements.txt ./
RUN pip install --upgrade pip && pip install --no-cache-dir -r requirements.txt

# Copiar el resto de los archivos del proyecto
COPY . .

EXPOSE 8000 8010

CMD ["python", "bot_telegram.py"]
