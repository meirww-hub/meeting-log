FROM python:3.11-slim

WORKDIR /app

# ffmpeg נדרש כדי לחתוך קטע שמע (m4a) לקטע PCM גולמי לבדיקת שקט אקוסטית -
# ראה app/pipeline/audio_silence.py (הגנה מפני תמלול מדומיין על הקלטה
# שקטה/פגומה, ב-transcription.py).
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

CMD exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8080}
