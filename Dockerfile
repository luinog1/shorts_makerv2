FROM python:3.12.8-slim

# Instala dependências do sistema (FFmpeg, etc)
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

# Copia arquivos de dependência
COPY pyproject.toml uv.lock ./

# Sincroniza dependências (CPU)
RUN uv sync --extra cpu

# ADICIONADO: Instala as bibliotecas necessárias para Scrape.do e Apify
RUN uv pip install requests apify-client

# Copia o resto do código
COPY . .

# Cria o arquivo setup.yml padrão se não existir
RUN if [ ! -f setup.yml ]; then cp example.setup.yml setup.yml; fi

# Comando padrão para rodar no Render
CMD ["uv", "run", "example.py"]
