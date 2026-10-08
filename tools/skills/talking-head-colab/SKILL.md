---
name: talking-head-colab
description: >-
  Talking head / lip sync + composição YouTube na T4 do Colab. Pipeline
  completo: texto → TTS (Qwen3-TTS) → foto + áudio → vídeo com boca mexendo
  (SadTalker/MuseTalk/Hallo2/Wav2Lip) → composição final estilo YouTube
  (avatar circular no canto + slide de fundo passando + áudio). Use pra
  gerar vídeos curtos do YouTube com avatar 3D, shorts, ou vinhetas.
---

# Talking Head na T4 — Foto + Áudio → Vídeo + Composição YouTube

Pipeline completo em 3 etapas (3 scripts):

```
TEXTO  →  [qwen_tts_generate.py]  →  ÁUDIO WAV
                                    ↓
FOTO   →  [lip_sync_avatar.py]    →  VÍDEO MP4 (boca mexendo)
                                    ↓
AUDIO  →  [compose_youtube_scene.py]  →  VÍDEO FINAL (avatar no canto + fundo)
```

## 1. TTS (Qwen3-TTS) — voz multilíngue na T4

```bash
ssh colab1-pro 'python3 /root/sessionflow/tools/qwen_tts_generate.py \
  --text "Bem-vindo à aula 13 do curso de Garagem. Hoje vamos falar sobre Qwen3." \
  --lang portuguese --speaker Vivian --out /tmp/audio.wav'
```

Voice clone (com sample do Diego):
```bash
ssh colab1-pro 'python3 /root/sessionflow/tools/qwen_tts_generate.py \
  --ref /tmp/diego-voice-sample.wav --ref-text "trecho original" \
  --text "novo texto com voz do Diego" --lang portuguese --out /tmp/audio-diego.wav'
```

## 2. Lip Sync (foto + áudio → vídeo) — escolha modelo

| Modelo | VRAM | Tempo (10s áudio) | Qualidade | Quando |
|---|---|---|---|---|
| `wav2lip` | ~2GB | 30-60s | ⭐⭐ | iteração rápida |
| `sadtalker` | ~5GB | 60-120s | ⭐⭐⭐⭐ | **sweet-spot** (recomendado) |
| `musetalk` | ~6GB | 30-60s | ⭐⭐⭐⭐⭐ | melhor qualidade |
| `hallo2` | ~12GB | 60-180s | ⭐⭐⭐⭐⭐ +emoções | top com emoções |

```bash
ssh colab1-pro 'python3 /root/sessionflow/tools/lip_sync_avatar.py \
  --image /content/diego.jpg --audio /tmp/audio.wav \
  --model sadtalker --size 512 --out /tmp/talk.mp4'
```

> ⚠ Trocar o `colab1-pro` por `colab2-pro` se já estiver com TTS rodando.
> Use `./tools/colab-switch-notebook.sh col2 avatar3d` ou configure outro host.

## 3. Composição YouTube (avatar circular + slide de fundo + áudio)

```bash
ssh colab2-pro 'python3 /root/sessionflow/tools/compose_youtube_scene.py \
  --avatar-video /tmp/talk.mp4 --audio /tmp/audio.wav \
  --background-slides /content/slide-001.png /content/slide-002.png /content/slide-003.png \
  --slide-duration 5 \
  --avatar-position bottom-right --avatar-size 320 --avatar-margin 40 \
  --width 1920 --height 1080 --fps 30 \
  --out /tmp/youtube-scene.mp4'
```

Posições do avatar:
- `bottom-right` (padrão YouTube)
- `bottom-left`
- `top-right`
- `top-left`

Backgrounds suportados:
- `--background` (imagem única, estática)
- `--background-video` (vídeo animado)
- `--background-slides` (sequência PNG, passa a cada `--slide-duration` segundos)

## Pipeline ONE-SHOT (delegar via SF)

```bash
SF=~/.claude/skills/sf-delegate/sf
$SF delegate --host col2-pro --provider claude --dir /root/sessionflow \
  --task "Pipeline talking head YouTube:
    1) TTS: 'Bem-vindo à aula 13 do curso de Garagem.' em /tmp/audio.wav
    2) Lip sync: photo=/content/diego.jpg, model=sadtalker → /tmp/talk.mp4
    3) Compor: avatar bottom-right, fundo slide, out=/tmp/scene.mp4
    Reportar: durações de cada etapa e tamanho final." \
  --name yt-scene-aula-13
$SF check yt-scene-aula-13
scp colab2-pro:/tmp/scene.mp4 /Users/diegoaraujo/Desktop/
```

## Pré-requisito

- Host T4 com notebook `colab-t4-talking-head.ipynb` aberto e conectado
- Foto do Diego (cadeira gamer, boa iluminação, rosto visível) — `diego.jpg`
- Para voz clonada: sample WAV de 6-30s da voz do Diego

## Limitações

- **Colab hiberna em 12h ocioso** — modelo Wav2Lip ~100MB, SadTalker ~2GB, MuseTalk ~4GB, Hallo2 ~12GB (re-baixam a cada hibernação)
- **T4 = 16GB VRAM** — SadTalker + MuseTalk cabem; Hallo2 aperta
- **Foto precisa ter boa iluminação** e rosto centralizado pra lip sync ter qualidade
- **SadTalker NÃO suporta voz clonada do Diego** se a voz tiver emoção muito diferente do neutro
- **ffmpeg** roda CPU (~5-15s pra 1 min de vídeo)
- **Composição com slide estático** parece artificial — use `--background-video` ou slides de verdade

## Files

- `tools/qwen_tts_generate.py` — TTS multilíngue (commit cf52f3f)
- `tools/lip_sync_avatar.py` — wrapper Wav2Lip/SadTalker/MuseTalk/Hallo2
- `tools/compose_youtube_scene.py` — composição YouTube (ffmpeg + PIL)
- `colab-t4-talking-head.ipynb` no Drive
