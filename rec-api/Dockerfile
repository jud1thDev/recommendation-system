FROM python:3.12-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY server.py .
COPY index.html ./rec-api/index.html
COPY src ./rec-api/src

EXPOSE 8000
CMD ["python", "server.py"]
