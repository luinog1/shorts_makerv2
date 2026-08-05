# 🎬 ShortsMaker v2 - Resumo do Projeto e Adaptações

Este documento resume as adaptações feitas no projeto original [ShortsMaker (ClipForge)](https://github.com/rajathjn/shorts_maker) para contornar a necessidade da API oficial do Reddit e possibilitar o deploy na nuvem (Render) com uma interface web própria.

## 📌 1. O Problema Inicial
O projeto original exigia credenciais oficiais do Reddit (`client_id` e `secret_id`) através da biblioteca `PRAW`. Como novas contas do Reddit não têm mais acesso fácil a essa API, o projeto travava na etapa de busca do post. Além disso, por ser um script de terminal, não rodava diretamente em plataformas de hospedagem como o Render, que exigem que uma porta web (HTTP) fique aberta.

## 🛠️ 2. A Solução (Scraping em vez de API)
Substituímos a biblioteca `PRAW` por serviços de web scraping. Agora o projeto suporta:
1. **Scrape.do**: Usa o endpoint oculto `.json` do Reddit para obter a estrutura de dados do post de forma limpa, sem precisar fazer parsing complexo de HTML.
2. **Apify**: Alternativa robusta usando o ator `apify/reddit-scraper`.

**Busca Automática**: Se o usuário não colar uma URL específica na interface, o scraper automaticamente busca o "Top Post do Dia" do subreddit `r/AskReddit`.

## 🌐 3. Interface Web (Flask UI)
Como o site oficial (ClipForge) é uma aplicação SaaS complexa, criamos uma UI minimalista mas funcional usando **Flask** (embutida no `example.py`). Ela oferece:
* **Editor de Configuração (`setup.yml`)**: Permite alterar vozes, vídeos de fundo e tokens (como o do Hugging Face) direto pelo navegador.
* **Gerador de Vídeo**: Campo para inserir a URL do Reddit (ou deixar em branco para busca automática).
* **Logs em Tempo Real**: Uma janela de terminal no navegador que mostra o progresso do WhisperX, MoviePy, etc.
* **Download Direto**: Botão para baixar o vídeo `.mp4` assim que a renderização termina.

## ⚙️ 4. Correções de Deploy no Render
Durante os testes no Render, enfrentamos e resolvemos dois problemas principais:
1. **Erro de "No open ports detected" / Timeout**: O Render exige que uma porta HTTP abra em poucos segundos. Como o `ShortsMaker` importa bibliotecas pesadas (like `torch` e `whisperx`), a inicialização demorava mais de 1 minuto.
   * **Solução**: Movemos os imports do `ShortsMaker` para dentro da função que roda em *background* (`run_pipeline`). Assim, o servidor Flask abre a porta imediatamente, e o Python só carrega o peso depois.
2. **Comando Docker Lento**: O comando `uv run` validava pacotes toda vez que o container reiniciava.
   * **Solução**: Alteramos o comando final no `Dockerfile` para chamar o Python direto da máquina virtual: `CMD [".venv/bin/python", "example.py"]`.

## 🔑 5. Variáveis de Ambiente Necessárias
No painel do Render (ou localmente), configure as seguintes variáveis:

| Variável | Descrição | Obrigatória? |
| :--- | :--- | :--- |
| `SCRAPEDO_API_KEY` | Seu token do Scrape.do para buscar o post. | Sim (ou Apify) |
| `APIFY_API_TOKEN` | Seu token do Apify (alternativa ao Scrape.do). | Não |
| `HUGGING_FACE_HUB_TOKEN` | Token do HuggingFace (ex: `hf_...`). **Crucial** para o WhisperX baixar os modelos de transcrição. Sem isso, o processo quebra. | Sim |
| `PORT` | Porta do servidor. O Render preenche automaticamente, mas o padrão no código é `10000`. | Automática |
| `DISCORD_WEBHOOK_URL` | Se quiser notificações no Discord. Coloque `None` se não usar. | Não |

*(Nota: O token do Hugging Face também pode ser colado diretamente na caixa de texto da UI no campo correspondente dentro do `setup.yml`)*.

## 📄 6. Estrutura do Dockerfile Atualizado
Para garantir que as dependências web e de scraping funcionem, o Dockerfile deve incluir a instalação do `flask`, `requests` e `apify-client`:

```dockerfile
# ... (início do Dockerfile original) ...

# Instala dependências do projeto
RUN uv sync --extra cpu

# ADICIONADO: Instala as bibliotecas necessárias para a Web UI e Scrapers
RUN uv pip install requests apify-client flask

# Copia o resto do código
COPY . .

# Cria o setup.yml padrão se não existir
RUN if [ ! -f setup.yml ]; then cp example.setup.yml setup.yml; fi

# Comando otimizado para abrir a porta rápido
CMD [".venv/bin/python", "example.py"]
```

## 🚀 7. Como usar a Aplicação
1. Acesse a URL gerada pelo Render (ex: `https://shorts-makerv2.onrender.com`).
2. Se quiser customizar vozes ou fundos, edite o texto na caixa "Configurações" e clique em **Salvar Config**.
3. Para gerar um vídeo, cole uma URL do Reddit no campo de texto (ou deixe em branco para pegar o top post de AskReddit).
4. Clique em **Iniciar Geração**.
5. Acompanhe os logs na tela preta. Quando terminar (100%), o botão **⬇ Baixar Vídeo MP4** aparecerá.

*** 

Espero que este resumo seja útil para documentar todo o trabalho que fizemos! Se precisar de mais alguma ajuste, é só falar.
