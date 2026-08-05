FROM python:3.12.8-slim

# Instala dependencias do sistema (FFmpeg, etc)
RUN apt-get update && apt-get install -y \
    ffmpeg \
    libsndfile1 \
    git \
    curl \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Instala o uv
RUN curl -LsSf https://astral.sh/uv/install.sh | sh
ENV PATH="/root/.local/bin:$PATH"

WORKDIR /app

# Copia arquivos de dependencia
COPY pyproject.toml uv.lock ./

# Sincroniza dependencias (CPU)
RUN uv sync --extra cpu

# ADICIONADO: Instala as bibliotecas necessarias para UI, Scrapers e diagnostico
# - requests: Scrape.do e Apify
# - apify-client: Apify scraper
# - flask: web UI
# - psutil: diagnostico de memoria (validacao de modelo Whisper)
# - gunicorn: servidor WSGI de producao (substitui Flask dev server)
RUN uv pip install requests apify-client flask psutil gunicorn

# Copia o resto do codigo
COPY . .

# SEMPRE recria setup.yml a partir do example.setup.yml
# (O setup.yml esta no .gitignore, entao nunca sobe para o repo.
#  Sempre recomecamos com defaults para garantir estado limpo.)
RUN rm -f setup.yml && cp example.setup.yml setup.yml

# Comando padrao para rodar no Render usando gunicorn (mais robusto que Flask dev server)
# -w 1: apenas 1 worker (pipeline usa muita memoria, nao paralelize)
# --timeout 120: tempo pra subir o processo (pode demorar pra importar libs)
CMD ["gunicorn", "--bind", "0.0.0.0:10000", "--workers", "1", "--timeout", "120", "example:app"]
