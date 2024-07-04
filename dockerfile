FROM python:3.12
WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    && pip install opentelemetry-distro opentelemetry-exporter-otlp

COPY . .

EXPOSE 5005

CMD ["python", "app.py"]