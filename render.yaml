services:
  - type: web
    name: shorts-makerv2
    runtime: docker
    plan: free
    # FORÇA rebuild sem cache — necessário porque atualizamos example.py
    # mas o Render estava usando cache do build antigo.
    dockerfilePath: ./Dockerfile
    # Render não tem opção direta de "no cache", mas a mudança do Dockerfile
    # invalida o cache automaticamente. Por garantia, bump de build:
    dockerContext: .
    # Variáveis de ambiente (definidas no painel do Render também):
    envVars:
      - key: SCRAPEDO_API_KEY
        sync: false
      - key: APIFY_API_TOKEN
        sync: false
      - key: HUGGING_FACE_HUB_TOKEN
        sync: false
      - key: PORT
        value: 10000
      - key: PYTHONUNBUFFERED
        value: "1"
      - key: HF_HOME
        value: /app/.cache/huggingface
    # Disco persistente para guardar cache de modelos HuggingFace entre redeploys
    # (sem isso, cada redeploy baixa 3GB de modelos de novo)
    disk:
      name: hf-cache
      mountPath: /app/.cache/huggingface
      sizeGB: 5
