---
name: avatar-3d-colab
description: >-
  Geração de avatar 3D (mesh) na T4 do Google Colab, com renderização em PNG
  para usar como thumbnail. Texto OU foto → mesh 3D → poses diferentes
  (frente, 3/4, perfil, costas). 3 modelos: TripoSR (rápido), Hunyuan3D-2 mini
  (sweet-spot com textura), Hunyuan3D-2 (top com CPU offload). Use pra gerar
  thumbnails do YouTube, avatares do blog, capas de curso, ou mockups 3D.
---

# Avatar 3D na T4 — Mesh + Render pra Thumbnail

Pipeline completo: **texto OU foto → mesh 3D (.glb) → render PNG** em múltiplas poses.

3 modelos disponíveis, escolha por trade-off:

| Modelo | Entrada | Saída | VRAM | Tempo (1 mesh) | Textura | Quando usar |
|---|---|---|---|---|---|---|
| `triposr` | foto | mesh sem textura | ~3GB | 2-5s | ❌ só cor base | iteração rápida, foto real do Diego |
| `hunyuan3d-2mini` | texto OU foto | mesh + textura PBR | ~8GB | 30-60s | ✅ completa | sweet-spot, avatar estilizado |
| `hunyuan3d-2` | texto OU foto | mesh + textura PBR | ~14GB (offload) | 60-120s | ✅ top | top, vale a pena pra capa final |

**Render** (funciona offline, sem GPU):
- `trimesh` + `pyrender` (OpenGL) — alta qualidade
- Poses: `front` (padrão), `3/4`, `side`, `back`, `top`
- Backgrounds: `white`, `black`, `blue`, `gray`
- Resoluções: `512x512` (rápido), `1024x1024` (padrão), `2048x2048` (print)

## Setup (1× por sessão Colab)

Pré-requisito: `tools/colab-bootstrap-avatar3d.sh <host> <host-id>` (instala TripoSR + Hunyuan3D-2 + pyrender).

```bash
# do Mac
./tools/colab-bootstrap-avatar3d.sh colab1-pro c775ad00-58bb-44ae-9781-7e7e23b5404e
# espera ver: ✅ bootstrap avatar3d OK
```

## Uso direto no Colab (SSH)

```bash
# 1) Foto do Diego → mesh rápido (TripoSR)
python3 /root/sessionflow/tools/avatar3d.py \
  --image /content/diego.jpg \
  --model triposr \
  --out /tmp/diego.glb

# 2) Renderizar pose específica
python3 /root/sessionflow/tools/avatar3d.py \
  --render /tmp/diego.glb \
  --pose 3/4 \
  --bg white \
  --resolution 1024x1024 \
  --out /tmp/diego-thumb.png

# 3) Pipeline one-shot: foto → 4 poses PNG (pra escolher thumb)
python3 /root/sessionflow/tools/avatar3d.py \
  --image /content/diego.jpg \
  --model triposr \
  --render-all \
  --out-dir /tmp/avatar-poses

# 4) Avatar estilizado via texto (Hunyuan3D-2 mini)
python3 /root/sessionflow/tools/avatar3d.py \
  --prompt "a friendly Brazilian coder wearing a hoodie, full body, 16:9 composition" \
  --model hunyuan3d-2mini \
  --out /tmp/dev-avatar.glb

python3 /root/sessionflow/tools/avatar3d.py \
  --render /tmp/dev-avatar.glb \
  --pose front \
  --bg blue \
  --out /tmp/dev-thumb.png
```

## Via SessionFlow (delegar do Mac)

```bash
SF=~/.claude/skills/sf-delegate/sf
$SF delegate --host colab1-pro --provider claude --dir /root/sessionflow \
  --task "Gerar avatar 3D a partir de /content/diego.jpg com TripoSR.
    Renderizar 4 poses (frente, 3/4, side, back) em 1024x1024 com fundo branco.
    Salvar em /content/avatar-poses/ e listar arquivos.
    Reportar: tempo total + tamanho de cada PNG." \
  --name gerar-avatar-3d
$SF check gerar-avatar-3d

# baixar resultados
scp colab1-pro:/content/avatar-poses/*.png /Users/diegoaraujo/Desktop/avatar-poses/
```

## Comparativo vs outras técnicas de thumb

| Técnica | Tempo | Customização | Reuso | Custo |
|---|---|---|---|---|
| **SDXL-Turbo (imagem 2D)** | 1-2s | só estilo | baixa (cada prompt novo) | R$ 0 |
| **FLUX.1-dev (imagem 2D)** | 60-90s | muito | baixa | R$ 0 |
| **TripoSR (3D real do Diego)** | 2-5s | só pose + fundo | ✅ muito alta | R$ 0 |
| **Hunyuan3D-2 mini (3D estilizado)** | 30-60s | pose + fundo + textura | ✅ alta | R$ 0 |
| Foto real do Diego | 0s (já tem) | nenhuma | ❌ uma foto só | R$ 0 |

**TripoSR é a melhor opção pra thumb do YouTube**: você tem 1 foto, gera o mesh 3D em 5s, e pode mudar pose/fundo quantas vezes quiser sem re-gerar.

## Limitações conhecidas

- **TripoSR não tem textura elaborate** — só cor base. Pra visual realista, use Hunyuan3D-2
- **Hunyuan3D-2 full** (12GB) usa CPU offload — mais lento mas cabe na T4
- **Colab hiberna em 12h ocioso** — modelo precisa re-baixar (1-3min)
- **pyrender** precisa de OpenGL — instalei `libgl1-mesa-glx` no notebook
- **A foto precisa ser clara** (rosto visível, boa iluminação) pra TripoSR ter bom resultado
- **Não é "3D animado"** — pra vídeo do avatar (falando, se mexendo), use SadTalker/LivePortrait (próxima skill)

## Files

- `tools/avatar3d.py` — wrapper Python (CLI + pipeline one-shot)
- `tools/colab-bootstrap-avatar3d.sh` — bootstrap do host T4 (criar)
- `tools/skills/avatar-3d-colab/SKILL.md` — este doc
