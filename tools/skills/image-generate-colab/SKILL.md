---
name: image-generate-colab
description: >-
  Geração de imagem local (Stable Diffusion / FLUX) rodando na T4 do Google Colab
  (host colab2-pro ou colab1-pro via SessionFlow). 7 modelos suportados, do
  SD-Turbo (1-2s, iteração rápida) ao FLUX.1-dev (60-90s, melhor qualidade
  open-source atual). 100% offline, sem API, sem custo por imagem. Use pra
  ilustrações de aula, capas de vídeo, mockups, thumbnails, fotos de blog,
  assets visuais em geral.
---

# Image Generate na T4 do Colab

Wrapper Python (`image_generate.py`) sobre `diffusers` rodando na Tesla T4 (16GB VRAM).
Suporta 7 modelos open-source — selecione por trade-off qualidade vs velocidade.

## Modelos disponíveis (na T4, 16GB)

| Modelo | Resolução | Steps | Tempo (1 img) | VRAM | Qualidade | Caso de uso |
|---|---|---|---|---|---|---|
| `sd-turbo` | 512x512 | 1 | 1-2s | ~3GB | ⭐⭐⭐ | Iteração rápida de conceito |
| `sdxl-turbo` | 512x512 | 1 | 1-2s | ~6GB | ⭐⭐⭐⭐ | Thumbnail, mockup rápido |
| `sdxl` | 1024x1024 | 30 | 15-30s | ~8GB | ⭐⭐⭐⭐ | Sweet-spot padrão |
| `sdxl-lightning` | 1024x1024 | 4 | 3-8s | ~8GB | ⭐⭐⭐⭐ | SDXL em 4 steps |
| `sd3-medium` | 1024x1024 | 28 | 30-60s | ~12GB | ⭐⭐⭐⭐⭐ | Top qualidade single-stage |
| `flux-schnell` | 1024x1024 | 4 | 30-60s | ~16GB (CPU offload) | ⭐⭐⭐⭐⭐ | FLUX distilled — top open-source |
| `flux-dev` | 1024x1024 | 28 | 60-90s | ~16GB (CPU offload) | ⭐⭐⭐⭐⭐ | Melhor geral open-source |

**Recomendação:**
- Thumbnail/capa rápida: `sdxl-turbo` (1-2s)
- Slide de aula: `sdxl` (15-30s, qualidade consistente)
- Hero image do blog: `flux-schnell` (30-60s, top qualidade)
- Capa de vídeo importante: `flux-dev` (60-90s)

## Setup (1× por sessão Colab)

Pré-requisito: `tools/colab-bootstrap-imagegen.sh colab2-pro <host-id>` (instala diffusers + modelos).

```bash
# do Mac
./tools/colab-bootstrap-imagegen.sh colab2-pro 02151543-4ff8-44c2-b607-b1dfc8625067
# espera ver: ✅ bootstrap imagegen OK
```

## Uso direto no Colab (SSH)

```bash
# 1 imagem com sdxl-turbo (mais rápido)
python3 /root/sessionflow/tools/image_generate.py \
  --prompt "a corgi wearing a space suit, digital art, 16:9" \
  --model sdxl-turbo --out /tmp/corgi.png

# 1 imagem com flux-schnell (melhor qualidade)
python3 /root/sessionflow/tools/image_generate.py \
  --prompt "uma astronauta brasileira em uma base lunar, foto realista, 16:9" \
  --model flux-schnell --out /tmp/astronauta.png

# batch via JSONL
cat > /tmp/jobs.jsonl <<'EOF'
{"prompt":"a futuristic city at sunset, cyberpunk, 16:9","model":"sdxl","out":"/tmp/imgs/city.png"}
{"prompt":"a friendly robot teaching a class, digital art, 16:9","model":"sdxl-turbo","out":"/tmp/imgs/robot.png"}
{"prompt":"the moon with earth in the background, photorealistic, 16:9","model":"flux-schnell","out":"/tmp/imgs/moon.png"}
EOF
python3 /root/sessionflow/tools/image_generate.py --batch /tmp/jobs.jsonl --out-dir /tmp/imgs
```

## Via SessionFlow (delegar do Mac)

```bash
SF=~/.claude/skills/sf-delegate/sf
$SF delegate --host colab2-pro --provider claude --dir /root/sessionflow \
  --task "Gerar 3 capas de blog (16:9, 1024x1024) com modelo sdxl-turbo.
    Temas:
    1. 'ia gerando código em telas holográficas'
    2. 'agente autônomo trabalhando de casa'
    3. 'tutorial de python com gradiente azul'
    Salvar em /content/capas/ e listar os arquivos gerados." \
  --name gerar-capas-blog
$SF check gerar-capas-blog
# baixar resultados
ssh colab2-pro "ls -la /content/capas/"
scp colab2-pro:/content/capas/*.png /Users/diegoaraujo/Desktop/
```

## Limitações conhecidas

- **Colab hiberna em 12h ocioso** — modelo some (FLUX-dev = 24GB, re-download = 1-2min)
- **FLUX com CPU offload** é ~2× mais lento que GPU direta. Trade-off pra caber na T4
- **SD3-medium** precisa de token HuggingFace (gated model). `huggingface-cli login` antes
- **Primeira execução** baixa o modelo (~3-24GB dependendo do escolhido) — subsequentes usam cache HF
- **512x512 com SDXL-Turbo** perde qualidade comparado a 1024x1024. Use SDXL real pra alta resolução
- **Texto em imagens** continua ruim em todos os modelos. Pra textos precisos, use HTML+Playwright screenshot

## Files

- `tools/image_generate.py` — wrapper Python (CLI + batch)
- `tools/colab-bootstrap-imagegen.sh` — bootstrap do host T4 (criar)

## Diferença vs `azap-image` (Gemini)

| Aspecto | `image_generate` (T4) | `azap-image` (Gemini) |
|---|---|---|
| Custo | R$ 0 (offline) | R$ ~0,04/img |
| Privacidade | 100% local | Vai pra Google |
| Velocidade | 1-90s | 5-15s |
| Qualidade | ⭐⭐⭐⭐ (FLUX) | ⭐⭐⭐⭐ |
| Texto em imagens | ruim | ruim (mesma coisa) |
| Rate limit | nenhum (modelo local) | 60/min na API |
| Modelos | 7 open-source | Gemini 2.5 Flash Image |

**Use `image_generate` quando:** privacidade, custo zero, ou precisa de FLUX/SD3 quality.
**Use `azap-image` quando:** precisa de iteração rápida com modelo Google (sem T4 disponível).
