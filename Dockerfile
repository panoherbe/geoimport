FROM python:3.12-slim
# unzip: INKAR ships its CSV as Deflate64, which Python's zipfile cannot read
RUN apt-get update && apt-get install -y --no-install-recommends unzip && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY pyproject.toml ./
COPY geoimport ./geoimport
RUN pip install --no-cache-dir ".[test]"
COPY tests ./tests
COPY sources ./sources
ENV GEOIMPORT_BASIS=/app
ENTRYPOINT ["geoimport"]
CMD ["dienst"]
