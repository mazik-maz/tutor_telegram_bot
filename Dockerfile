FROM python:3.11-slim
WORKDIR /app

# зависимости
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# исходники
COPY ./app ./app

# точка входа
CMD ["python", "-m", "app.main"]
