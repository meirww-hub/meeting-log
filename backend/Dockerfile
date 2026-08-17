FROM python:3.11-slim

WORKDIR /app

# ffmpeg נדרש כדי לחתוך קטע שמע (m4a) לקטע PCM גולמי לפני חישוב טביעת קול -
# ראה app/pipeline/speaker_embedding.py. sherpa-onnx מקבל מערך דגימות, לא
# קובץ, ואין ל-libsndfile פענוח AAC משלו.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# מודל טביעת-קול (wespeaker ResNet34, מכוונן large-margin) - קוד פתוח,
# משוקלל דרך sherpa-onnx (ONNX Runtime בלבד, בלי torch - הסיבה שדחתה את
# pyannote.audio בהערה שב-requirements.txt). ADD (לא RUN curl - python:slim
# לא כולל curl) בשכבה נפרדת מ-COPY app, כדי ששינוי בקוד לא יוריד אותו
# מחדש בכל build.
ADD https://github.com/k2-fsa/sherpa-onnx/releases/download/speaker-recongition-models/wespeaker_en_voxceleb_resnet34_LM.onnx \
    /app/models/speaker_embedding.onnx

COPY app ./app

CMD exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8080}
