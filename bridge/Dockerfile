FROM python:3.12-slim
WORKDIR /srv
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app app
# Laeuft (noch) als root: data/ ist teils root-, teils benutzereigen und wird per Bind-Mount geteilt.
# Umstellung auf einen unprivilegierten USER: docs/wiki/Betrieb-und-Ueberwachung.md ("Container-Haertung").
CMD ["python", "-m", "app.main"]
