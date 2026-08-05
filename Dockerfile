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
# PATH deve incluir tanto o binario do uv quanto o .venv (depois de criado)
ENV PATH="/root/.local/bin:/app/.venv/bin:$PATH"

WORKDIR /app

# Copia arquivos de dependencia
COPY pyproject.toml uv.lock ./

# Sincroniza dependencias (CPU) - cria .venv em /app/.venv
RUN uv sync --extra cpu

# Instala bibliotecas extras para UI, Scrapers e diagnostico
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

# Valida que gunicorn esta acessivel ANTES de tentar rodar
# (falha rapido em vez de exit 128 misterioso)
RUN gunicorn --version

# Comando padrao usando shell form para poder expandir $PORT
# Render seta PORT automaticamente; default 10000 se nao definido
# Usamos .venv/bin/gunicorn explicitamente para garantir que encontra
# --timeout 120: tempo pra o worker subir (importar torch/whisperx e lento)
# --graceful-timeout 30: tempo pra desligar limpo
# --access-logfile -: logs de acesso vao pro stdout (visiveis no Render)
CMD .venv/bin/gunicorn \
      --bind 0.0.0.0:${PORT:-10000} \
      --workers 1 \
      --timeout 120 \
      --graceful-timeout 30 \
      --access-logfile - \
      --error-logfile - \
      example:app
