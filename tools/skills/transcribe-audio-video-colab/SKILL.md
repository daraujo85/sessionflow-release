---
name: transcribe-audio-video-colab
description: >-
  Transcrição de áudio/vídeo multilíngue com Whisper large-v3 + Qwen2-VL 2B
  rodando na T4 do Google Colab (host colab2-pro via SessionFlow). Combina
  transcrição (Whisper) com extração de frames-chave (ffmpeg) e visão
  computacional (Qwen2-VL) pra entender vídeo + tela no mesmo agente. Use
  quando precisar transcrever um vídeo longo (1h+), extrair legendas com
  contexto visual, ou analisar um tutorial gravado de uma ferramenta web/mobile.
---

# Transcribe-Audio-Video na T4 do Colab

Pipeline GPU que junta 3 modelos na mesma T4 (16GB):

| Etapa | Ferramenta | VRAM | Tempo (1h vídeo) |
|---|---|---|---|
| Transcrição | Whisper large-v3 | ~3GB | 5-10min |
| Frame extraction | ffmpeg | 0GB (CPU) | ~30s |
| Vision analysis | Qwen2-VL 2B Instruct | ~4GB | 1-2min (20 frames) |

Roda offline (sem API). Tudo em `/tmp` (perdido no hibernar — copia resultado pro Drive ou Mac).

## Quando usar

- Vídeo de reunião/palestra gravada → transcrição + legendas
- Tutorial em vídeo (ChatGPT, Figma, curso online) → transcrição + descrição da tela
- Análise de UX a partir de gravações de usuário
- Extrair SRT/VTT pra vídeo do YouTube/curso

## Setup (1× por sessão Colab)

Pré-requisito: `tools/colab-bootstrap-whisper.sh colab2-pro <host-id>` (instala whisper + qwen-vl).

```bash
# do Mac, antes de delegar
./tools/colab-bootstrap-whisper.sh colab2-pro 02151543-4ff8-44c2-b607-b1dfc8625067
# espera ver: ✅ bootstrap transcribe OK
```

## Uso direto no Colab (SSH ou tmux)

```bash
# 1) Transcrição + extração de frames com skill transcribe-audio-video
python3 /root/sessionflow/tools/skills/transcribe-audio/transcribe_audio.py \
  /content/aula.mp4 --frames 12 --frames-every 20 \
  --output-file /tmp/aula.json --format json

# 2) Vision analysis dos frames extraídos
VISION_MODEL="Qwen/Qwen2-VL-2B-Instruct" \
python3 /root/sessionflow/tools/vision_analyze_frames.py \
  /tmp/transcribe-frames-aula/ \
  --prompt "O que aparece na tela? Liste elementos de UI, ações, plataforma (web/mobile/desktop) e contexto técnico." \
  --out /tmp/aula-vision.json

# 3) Combinar: o JSON final tem timestamps (whisper) + descrições (qwen2-vl)
python3 -c "
import json
ts = json.load(open('/tmp/aula.json'))
vis = json.load(open('/tmp/aula-vision.json'))
# mapear por nome de frame
vmap = {Path(v['frame']).name: v['description'] for v in vis}
for kf in ts.get('key_frames', []):
    print(f\"{kf['time']}  {vmap.get(Path(kf['path']).name, 'no vision')}  (transcript: {kf.get('text','')[:100]})\")
"
```

## Via SessionFlow (delegar do Mac)

```bash
SF=~/.claude/skills/sf-delegate/sf
$SF delegate --host colab2-pro --provider claude --dir /root/sessionflow \
  --task "Transcrever e analisar frames do vídeo /content/aula.mp4
    com skill transcribe-audio-video e vision_analyze_frames.py.
    Saída: /content/aula-final.json (transcrição + descrições visuais).
    Reportar: total de minutos transcritos, número de frames com pista visual,
    e os 5 momentos mais importantes (técnico + negócio)." \
  --name transcribe-aula-foo
# colhendo o resultado
$SF check transcribe-aula-foo
# baixar o JSON final
ssh colab2-pro "cat /content/aula-final.json" > /tmp/aula-final.json
```

## Idiomas

Whisper large-v3 transcreve em 90+ idiomas. PT/EN/ES/FR/DE/IT são nativos.
Qwen2-VL entende a tela em qualquer idioma (multimodal nativo).

## Limitações conhecidas

- **Colab hiberna em 12h ocioso** — modelo some (Whisper ~3GB, Qwen2-VL ~4GB; tempo de re-download ~30s)
- **T4 = 16GB VRAM** — Whisper+Qwen2-VL juntos = ~7GB; sobra pra rodar em paralelo
- **Qwen2-VL 7B NÃO cabe com Whisper** (16GB cheio). Pra melhor visão, baixar Whisper antes
- **ffmpeg** roda CPU (barato). Vídeos de 1h processa em <1min

## Files

- `vision_analyze_frames.py` — wrapper Qwen2-VL
- `tools/colab-bootstrap-whisper.sh` — bootstrap do host T4
- `~/.claude/skills/transcribe-audio-video/` — skill original (Whisper + ffmpeg, sem vision)
