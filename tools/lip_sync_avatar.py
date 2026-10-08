#!/usr/bin/env python3
"""
Talking head / lip sync: foto + áudio → vídeo com a boca mexendo em sync.

Modelos suportados (qualidade ↑, VRAM ↑, tempo ↑):
  - wav2lip     (foto + audio → mp4, ~2GB, 30-60s pra 10s de áudio) — leve
  - sadtalker   (foto + audio → mp4, ~5GB, 60-120s pra 10s) — sweet-spot, expressivo
  - musetalk    (foto + audio → mp4, ~6GB, 30-60s) — melhor qualidade
  - hallo2      (foto + audio → mp4, ~12GB, 60-180s) — top, emoções

Uso:
  # básico
  python3 lip_sync_avatar.py --image /content/diego.jpg --audio /content/tts.wav --out /content/talk.mp4
  python3 lip_sync_avatar.py --image /content/diego.jpg --audio /content/tts.wav --model sadtalker --out /content/talk.mp4
  
  # com crop 512x512
  python3 lip_sync_avatar.py --image /content/diego.jpg --audio /content/tts.wav --size 512 --out /content/talk.mp4
"""
from __future__ import annotations
import argparse, json, os, subprocess, sys, time
from pathlib import Path

MODELS = {
    "wav2lip":   {"hf": "Rudrabha/Wav2Lip",                "type": "lipsync-basic"},
    "sadtalker": {"hf": "OpenTalker/SadTalker",            "type": "lipsync-expressive"},
    "musetalk":  {"hf": "TMElyralab/MuseTalk",              "type": "lipsync-quality"},
    "hallo2":    {"hf": "fudan-generative-ai/hallo2",       "type": "lipsync-emo"},
}


def run_wav2lip(image: str, audio: str, out: str) -> str:
    """Wav2Lip básico. Leve, qualidade média."""
    t0 = time.time()
    print(f"→ Wav2Lip: processando...", file=sys.stderr)
    out_dir = Path(out).parent
    out_dir.mkdir(parents=True, exist_ok=True)
    
    # Clonar repo (cached em /content)
    repo_dir = "/content/Wav2Lip"
    if not Path(repo_dir).exists():
        subprocess.run(["git", "clone", "https://github.com/Rudrabha/Wav2Lip.git", repo_dir], check=True, capture_output=True)
    
    # Pré-processar imagem
    subprocess.run([
        "python3", f"{repo_dir}/preprocess.py",
        "--type", "2d", "--face", image,
        "--img_size", "96", "--out", f"{out_dir}/wav2lip-prep",
    ], check=True, capture_output=True)
    
    # Inferência
    checkpoint = "/content/Wav2Lip/checkpoints/wav2lip.pth"
    if not Path(checkpoint).exists():
        # baixar checkpoint
        ckpt_dir = Path(checkpoint).parent
        ckpt_dir.mkdir(parents=True, exist_ok=True)
        subprocess.run([
            "gdown", "1dwH5J_CqB1T7lPn1PQYq6uZ2o0kr1w3Q", "-O", checkpoint
        ], check=False, capture_output=True)
    
    result_path = f"{out_dir}/wav2lip-result.mp4"
    subprocess.run([
        "python3", f"{repo_dir}/inference.py",
        "--checkpoint_path", checkpoint,
        "--face", image,
        "--audio", audio,
        "--outfile", result_path,
        "--resize_factor", "2",
    ], check=True, capture_output=True)
    
    Path(out).write_bytes(Path(result_path).read_bytes())
    print(f"  ✓ {time.time()-t0:.1f}s → {out}", file=sys.stderr)
    return out


def run_sadtalker(image: str, audio: str, out: str, size: int = 512) -> str:
    """SadTalker: 1 foto + audio → vídeo com cabeça animada (não só boca)."""
    t0 = time.time()
    print(f"→ SadTalker: processando ({size}x{size})...", file=sys.stderr)
    out_dir = Path(out).parent
    out_dir.mkdir(parents=True, exist_ok=True)
    
    repo_dir = "/content/SadTalker"
    if not Path(repo_dir).exists():
        subprocess.run(["git", "clone", "https://github.com/OpenTalker/SadTalker.git", repo_dir], check=True, capture_output=True)
    
    # Baixar checkpoints (vários arquivos, demora)
    ckpt_dir = f"{repo_dir}/checkpoints"
    if not Path(ckpt_dir, "SadTalker_V0.0.2_*.safetensors").exists():
        print("  baixando checkpoints (pode demorar 1-2min)...", file=sys.stderr)
        subprocess.run(["bash", f"{repo_dir}/download.sh"], cwd=repo_dir, capture_output=True)
    
    result_path = f"{out_dir}/sadtalker-result.mp4"
    cmd = [
        "python3", f"{repo_dir}/inference.py",
        "--driven_audio", audio,
        "--source_image", image,
        "--result_dir", out_dir,
        "--enhancer", "gfpgan",
        "--size", str(size),
        "--expression_scale", "1.0",
        "--pose_style", "0",  # pode ser 0-45; 0 = natural
    ]
    subprocess.run(cmd, check=True, capture_output=True)
    
    # SadTalker salva em <result_dir>/<image_name>__<audio_name>.mp4
    img_name = Path(image).stem
    audio_name = Path(audio).stem
    actual = f"{out_dir}/{img_name}__{audio_name}.mp4"
    if Path(actual).exists():
        Path(out).write_bytes(Path(actual).read_bytes())
    
    print(f"  ✓ {time.time()-t0:.1f}s → {out}", file=sys.stderr)
    return out


def run_musetalk(image: str, audio: str, out: str) -> str:
    """MuseTalk: real-time, alta qualidade."""
    t0 = time.time()
    print(f"→ MuseTalk: processando...", file=sys.stderr)
    out_dir = Path(out).parent
    out_dir.mkdir(parents=True, exist_ok=True)
    
    repo_dir = "/content/MuseTalk"
    if not Path(repo_dir).exists():
        subprocess.run(["git", "clone", "https://github.com/TMElyralab/MuseTalk.git", repo_dir], check=True, capture_output=True)
        subprocess.run(["pip", "install", "-q", "-r", f"{repo_dir}/requirements.txt"], check=True, capture_output=True)
    
    # Baixar modelos (~4GB)
    ckpt_dir = f"{repo_dir}/checkpoints"
    if not Path(ckpt_dir, "musetalk.safetensors").exists():
        print("  baixando modelos (pode demorar 3-5min)...", file=sys.stderr)
        subprocess.run(["bash", f"{repo_dir}/download_ckpts.sh"], cwd=repo_dir, capture_output=True)
    
    result_path = f"{out_dir}/musetalk-result.mp4"
    cmd = [
        "python3", f"{repo_dir}/scripts/inference.py",
        "--inference_config", f"{repo_dir}/configs/inference/test.yaml",
        "--ckpt_path", f"{ckpt_dir}/musetalk.safetensors",
        "--result_dir", out_dir,
        "--source_image", image,
        "--driven_audio", audio,
    ]
    subprocess.run(cmd, check=True, capture_output=True)
    
    print(f"  ✓ {time.time()-t0:.1f}s → {out}", file=sys.stderr)
    return out


def run_hallo2(image: str, audio: str, out: str) -> str:
    """Hallo2: top qualidade, com emoções."""
    t0 = time.time()
    print(f"→ Hallo2: processando (pode demorar)...", file=sys.stderr)
    out_dir = Path(out).parent
    out_dir.mkdir(parents=True, exist_ok=True)
    
    repo_dir = "/content/hallo2"
    if not Path(repo_dir).exists():
        subprocess.run(["git", "clone", "https://github.com/fudan-generative-ai/hallo2.git", repo_dir], check=True, capture_output=True)
    
    result_path = f"{out_dir}/hallo2-result.mp4"
    cmd = [
        "python3", f"{repo_dir}/scripts/inference.py",
        "--source_image", image,
        "--driving_audio", audio,
        "--output", out_dir,
    ]
    subprocess.run(cmd, check=True, capture_output=True)
    
    print(f"  ✓ {time.time()-t0:.1f}s → {out}", file=sys.stderr)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--image", required=True, help="foto de entrada (PNG/JPG)")
    ap.add_argument("--audio", required=True, help="áudio de entrada (WAV/MP3)")
    ap.add_argument("--model", default="sadtalker", choices=list(MODELS.keys()))
    ap.add_argument("--out", default="/tmp/talking.mp4")
    ap.add_argument("--size", type=int, default=512, help="resolução (256/512)")
    args = ap.parse_args()
    
    if not os.path.isfile(args.image):
        die(f"imagem não existe: {args.image}")
    if not os.path.isfile(args.audio):
        die(f"áudio não existe: {args.audio}")
    
    runners = {
        "wav2lip":   run_wav2lip,
        "sadtalker": run_sadtalker,
        "musetalk":  run_musetalk,
        "hallo2":    run_hallo2,
    }
    runners[args.model](args.image, args.audio, args.out, args.size)
    print(f"\n✓ vídeo de talking head pronto: {args.out}", file=sys.stderr)


def die(msg):
    print(f"erro: {msg}", file=sys.stderr)
    sys.exit(1)


if __name__ == "__main__":
    main()
