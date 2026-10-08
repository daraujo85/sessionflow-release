#!/usr/bin/env python3
"""
Pipeline completo: vídeo → transcrição (Whisper) + frames (ffmpeg) + descrição visual (Qwen2-VL).
Combina as skills transcribe-audio-video e vision_analyze_frames em 1 comando.

Uso:
  # básico
  python3 transcribe_video_pipeline.py /content/aula.mp4
  
  # com limite de frames
  python3 transcribe_video_pipeline.py /content/aula.mp4 --frames 20 --frames-every 30
  
  # batch
  python3 transcribe_video_pipeline.py /content/videos/ --batch

Saída: <input>.pipeline.json com {transcription, key_frames, frame_descriptions, summary}
"""
from __future__ import annotations
import argparse, json, os, subprocess, sys, time
from pathlib import Path

DEFAULT_FRAMES = 12
DEFAULT_FRAMES_EVERY = 0
DEFAULT_WHISPER_MODEL = os.environ.get("WHISPER_MODEL", "large-v3")
DEFAULT_VISION_MODEL = os.environ.get("VISION_MODEL", "Qwen/Qwen2-VL-2B-Instruct")


def transcribe(video_path: str, output_dir: str, whisper_model: str = DEFAULT_WHISPER_MODEL) -> dict:
    """Roda Whisper via skill transcribe-audio-video (extensão .json)."""
    SKILL = "/root/.claude/skills/transcribe-audio-video/transcribe_audio.py"
    if not Path(SKILL).exists():
        # tentar caminho local
        SKILL = os.path.expanduser("~/.claude/skills/transcribe-audio-video/transcribe_audio.py")
    if not Path(SKILL).exists():
        raise FileNotFoundError(f"skill transcribe-audio-video não encontrada: {SKILL}")
    
    out_json = f"{output_dir}/transcription.json"
    cmd = [
        "python3", SKILL,
        video_path,
        "--model", whisper_model,
        "--format", "json",
        "--output-file", out_json,
    ]
    print(f"→ transcrevendo com Whisper {whisper_model}...", file=sys.stderr)
    t0 = time.time()
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        print(r.stderr, file=sys.stderr)
        raise RuntimeError(f"transcribe falhou (exit {r.returncode})")
    print(f"  ✓ {time.time()-t0:.1f}s transcrição concluída → {out_json}", file=sys.stderr)
    return json.load(open(out_json))


def extract_keyframes(transcription: dict, video_path: str, output_dir: str,
                       n_frames: int = DEFAULT_FRAMES, frames_every: int = DEFAULT_FRAMES_EVERY) -> list:
    """Extrai frames nos momentos com pista visual (complementado por --frames-every)."""
    frames_dir = f"{output_dir}/frames"
    os.makedirs(frames_dir, exist_ok=True)
    SKILL = "/root/.claude/skills/transcribe-audio-video/transcribe_audio.py"
    if not Path(SKILL).exists():
        SKILL = os.path.expanduser("~/.claude/skills/transcribe-audio-video/transcribe_audio.py")
    
    cmd = [
        "python3", SKILL, video_path,
        "--format", "json",
        "--output-file", f"{output_dir}/transcription_with_frames.json",
    ]
    if n_frames:
        cmd += ["--frames", str(n_frames)]
    if frames_every:
        cmd += ["--frames-every", str(frames_every)]
    
    print(f"→ extraindo frames com pista visual (max {n_frames}, every {frames_every}s)...", file=sys.stderr)
    t0 = time.time()
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        print(r.stderr, file=sys.stderr)
        raise RuntimeError(f"frame extraction falhou (exit {r.returncode})")
    print(f"  ✓ {time.time()-t0:.1f}s frames extraídos → {frames_dir}/", file=sys.stderr)
    
    # Coletar os frames que foram criados
    frames = sorted(Path(frames_dir).glob("*.jpg"))
    return [{"path": str(f), "name": f.name, "size_kb": f.stat().st_size // 1024} for f in frames]


def describe_frames(frames: list, vision_model: str = DEFAULT_VISION_MODEL,
                    prompt: str = None) -> list:
    """Roda Qwen2-VL em cada frame extraído."""
    if not frames:
        return []
    if prompt is None:
        prompt = ("Descreva o que aparece na tela em 1-3 frases. "
                  "Foco em: elementos de UI visíveis (botões, tabelas, campos, menus), "
                  "plataforma (web/mobile/desktop), ação em andamento, e contexto técnico/negócio.")
    
    # Chama o wrapper de vision
    WRAPPER = os.path.join(os.path.dirname(__file__), "vision_analyze_frames.py")
    if not Path(WRAPPER).exists():
        raise FileNotFoundError(f"vision wrapper não encontrado: {WRAPPER}")
    
    frames_dir = str(Path(frames[0]["path"]).parent)
    cmd = [
        "python3", WRAPPER, frames_dir,
        "--prompt", prompt,
        "--pattern", "*.jpg",
        "--out", f"{frames_dir}/descriptions.json",
    ]
    print(f"→ analisando {len(frames)} frames com {vision_model}...", file=sys.stderr)
    t0 = time.time()
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        print(r.stderr, file=sys.stderr)
        raise RuntimeError(f"vision analysis falhou (exit {r.returncode})")
    print(f"  ✓ {time.time()-t0:.1f}s vision analysis concluída", file=sys.stderr)
    
    descs = json.load(open(f"{frames_dir}/descriptions.json"))
    # Mapear por nome de frame
    desc_map = {Path(d["frame"]).name: d["description"] for d in descs}
    for f in frames:
        f["description"] = desc_map.get(f["name"], "")
    return frames


def merge(video_path: str, transcription: dict, frames: list) -> dict:
    """Junta transcrição + descrições de frame no output final."""
    # Pegar segmentos do transcription (depende do schema da skill transcribe)
    segments = transcription.get("segments", [])
    
    # Mapear frame_time → descrição
    frame_by_time = {}
    for f in frames:
        # Extrair tempo do nome (formato típico: frame_01m23s.jpg ou frame_83s.jpg)
        name = f["name"]
        # Tentar extrair segundos do nome
        import re
        m = re.search(r'(\d+)m(\d+)s|(\d+)s', name)
        if m:
            if m.group(3):
                t = int(m.group(3))
            else:
                t = int(m.group(1)) * 60 + int(m.group(2))
            frame_by_time[t] = f["description"]
    
    return {
        "input": video_path,
        "transcription": {
            "language": transcription.get("language", "?"),
            "duration_seconds": transcription.get("duration", 0),
            "segments_count": len(segments),
            "text": transcription.get("text", ""),
        },
        "frames": frames,
        "frame_descriptions": [
            {"time_seconds": t, "description": d} for t, d in sorted(frame_by_time.items())
        ],
        "summary": {
            "total_segments": len(segments),
            "frames_with_visual_cue": len([f for f in frames if f.get("description")]),
            "frames_count": len(frames),
        },
    }


def process_one(video_path: str, output_dir: str, whisper_model: str, vision_model: str,
                n_frames: int, frames_every: int) -> dict:
    video_path = os.path.abspath(video_path)
    if not os.path.isfile(video_path):
        raise FileNotFoundError(video_path)
    
    out_dir = output_dir or f"{Path(video_path).stem}-pipeline"
    os.makedirs(out_dir, exist_ok=True)
    print(f"\n=== {video_path} ===", file=sys.stderr)
    
    transcription = transcribe(video_path, out_dir, whisper_model)
    frames = extract_keyframes(transcription, video_path, out_dir, n_frames, frames_every)
    describe_frames(frames, vision_model)
    
    result = merge(video_path, transcription, frames)
    out_path = f"{out_dir}/pipeline.json"
    json.dump(result, open(out_path, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print(f"\n✓ pipeline completo → {out_path}", file=sys.stderr)
    return result


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("input", help="arquivo de vídeo OU pasta (com --batch)")
    ap.add_argument("--out", help="pasta de saída (default: <input>-pipeline)")
    ap.add_argument("--batch", action="store_true", help="processar todos os vídeos da pasta")
    ap.add_argument("--whisper-model", default=DEFAULT_WHISPER_MODEL, help="Whisper (tiny/base/small/medium/large-v3)")
    ap.add_argument("--vision-model", default=DEFAULT_VISION_MODEL)
    ap.add_argument("--frames", type=int, default=DEFAULT_FRAMES, help="max frames com pista visual")
    ap.add_argument("--frames-every", type=int, default=DEFAULT_FRAMES_EVERY, help="amostrar 1 frame a cada N segundos")
    args = ap.parse_args()
    
    if args.batch:
        in_dir = Path(args.input)
        videos = sorted([str(p) for p in in_dir.glob("*.mp4")] + 
                        [str(p) for p in in_dir.glob("*.mov")] +
                        [str(p) for p in in_dir.glob("*.mkv")] +
                        [str(p) for p in in_dir.glob("*.webm")])
        if not videos:
            die(f"nenhum vídeo em {in_dir}")
        for v in videos:
            process_one(v, None, args.whisper_model, args.vision_model, args.frames, args.frames_every)
    else:
        process_one(args.input, args.out, args.whisper_model, args.vision_model, args.frames, args.frames_every)


def die(msg):
    print(f"erro: {msg}", file=sys.stderr)
    sys.exit(1)


if __name__ == "__main__":
    main()
