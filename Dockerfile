# Dockerfile
FROM python:3.9-slim

# WORKDIR sets the working directory for subsequent commands like COPY and CMD
WORKDIR /app_root # Use a different name for WORKDIR to avoid confusion with package name

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE 1
ENV PYTHONUNBUFFERED 1
ENV PYTHONPATH="/app_root"

COPY requirements.txt requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Copy your 'app' package directory into '/app_root/app'
COPY ./app /app_root/app/
# Copy quizzes_data.json if it's inside your local 'app' directory to the correct place
# The path in load_quizzes_data is relative to main.py (BASE_DIR)
# BASE_DIR = Path(__file__).resolve().parent -> /app_root/app
# QUIZZES_DATA_FILE = BASE_DIR / "quizzes_data.json" -> /app_root/app/quizzes_data.json
# So, quizzes_data.json should be inside the 'app' package next to main.py
# The `COPY ./app /app_root/app/` already handles this if quizzes_data.json is in your local `app` folder.

EXPOSE 8000

# Production command for Render
# Uvicorn will look for the 'app' package (now at /app_root/app),
# then 'main.py' inside it, then the 'app' instance.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
