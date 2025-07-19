# Dockerfile
# Python'ın resmi hafif bir sürümünü temel görüntü olarak kullan
FROM python:3.10-slim-buster 

# Çalışma dizinini ayarla
WORKDIR /app

# Bağımlılıkları kopyala ve yükle
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Tüm proje dosyalarını kopyala (backend ve fonts klasörleri vb.)
# Frontend klasörünü kopyalamaya gerek yok, çünkü Fly.io backend'i frontend'i sunmayacak.
COPY . . 

# Redis bağlantı URL'sini ortam değişkeni olarak ayarla
ENV REDIS_BROKER_URL="redis://[fdaa:22:212f:a7b:4d8:af8d:3fe:2]:6379/0"

# Uygulamanın çalışacağı portu belirt (Gunicorn için)
EXPOSE 8080

# Varsayılan CMD komutu (Fly.io'nun processes tanımı tarafından geçersiz kılınacak)
CMD ["gunicorn", "-b", "0.0.0.0:8080", "--timeout", "120", "--access-logfile", "-", "--error-logfile", "-", "--log-level", "info", "backend.app:app"]
