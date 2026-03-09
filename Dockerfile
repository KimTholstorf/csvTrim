FROM python:3.12-slim

WORKDIR /app

# Install dependencies first — separate layer so it's cached on rebuilds
# unless requirements.txt changes
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the script and default presets
COPY csvTrim.py presets.json ./

# Mount your CSV files here at runtime: -v /your/data:/data
VOLUME ["/data"]

ENTRYPOINT ["python3", "csvTrim.py"]
