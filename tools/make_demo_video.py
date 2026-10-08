#!/usr/bin/env python3
"""
Demo light: avatar 2D + slides + áudio TTS.
Não precisa GPU. Funciona em qualquer máquina com ffmpeg + Pillow.

Uso:
  # 1) Gera o avatar 2D
  python3 make_demo_video.py --avatar-text "DIEGO" --avatar-img /tmp/avatar.jpg
  
  # 2) Cria slides simples (texto)
  python3 make_demo_video.py --slides "Título 1:Subtítulo" "Título 2:Subtítulo" "Título 3:Subtítulo"
  
  # 3) Compõe tudo com áudio TTS
  python3 make_demo_video.py \
    --audio /tmp/audio.wav \
    --avatar-img /tmp/avatar.jpg \
    --slides "Bem-vindo:Qwen3-TTS" "10 idiomas:PT/EN/ES/FR/DE/IT/JA/KO/ZH/RU" \
    --out /tmp/youtube-scene.mp4
"""
from __future__ import annotations
import argparse, os, subprocess, sys, tempfile
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont


def make_avatar(out: str, text: str = "DIEGO", size: int = 1024):
    """Avatar 2D cyberpunk: cabeça com fone, óculos, sorriso."""
    W = size
    img = Image.new('RGB', (W, W), (15, 23, 42))
    draw = ImageDraw.Draw(img)
    
    # Gradiente
    for r in range(W//2, 0, -2):
        intensity = int(80 * (r / (W//2)) ** 2)
        color = (15 + intensity//3, 23 + intensity//2, 42 + intensity)
        draw.ellipse([W//2-r, W//2-r, W//2+r, W//2+r], fill=color)
    
    # Cadeira (sombra)
    draw.ellipse([W//2-300, W//2+50, W//2+300, W*1.0], fill=(20, 20, 30))
    
    # Corpo/ombros
    draw.ellipse([W//2-220, W//2+50, W//2+220, W//2+400], fill=(45, 55, 72))
    # Pescoço
    draw.rectangle([W//2-60, W//2-20, W//2+60, W//2+80], fill=(70, 80, 100))
    
    # Cabeça
    draw.ellipse([W//2-130, W//2-260, W//2+130, W//2+0], fill=(180, 150, 120))
    # Cabelo
    draw.ellipse([W//2-150, W//2-290, W//2+150, W//2-30], fill=(30, 20, 20))
    # Olhos
    draw.ellipse([W//2-60, W//2-160, W//2-30, W//2-130], fill=(255, 255, 255))
    draw.ellipse([W//2+30, W//2-160, W//2+60, W//2-130], fill=(255, 255, 255))
    draw.ellipse([W//2-50, W//2-150, W//2-40, W//2-140], fill=(0, 0, 0))
    draw.ellipse([W//2+40, W//2-150, W//2+50, W//2-140], fill=(0, 0, 0))
    # Sorriso
    draw.arc([W//2-40, W//2-90, W//2+40, W//2-50], 0, 180, fill=(120, 60, 60), width=8)
    # Óculos
    draw.rectangle([W//2-100, W//2-170, W//2-10, W//2-110], outline=(0, 255, 200), width=4)
    draw.rectangle([W//2+10, W//2-170, W//2+100, W//2-110], outline=(0, 255, 200), width=4)
    draw.line([W//2-10, W//2-140, W//2+10, W//2-140], fill=(0, 255, 200), width=4)
    # Fone de ouvido
    draw.ellipse([W//2-180, W//2-180, W//2-100, W//2-100], fill=(20, 20, 20))
    draw.ellipse([W//2+100, W//2-180, W//2+180, W//2-100], fill=(20, 20, 20))
    draw.ellipse([W//2-170, W//2-170, W//2-110, W//2-110], fill=(0, 200, 150))
    draw.ellipse([W//2+110, W//2-170, W//2+170, W//2-110], fill=(0, 200, 150))
    # Texto
    draw.text((W//2-100, W-150), text, fill=(0, 255, 200))
    
    img.save(out, 'JPEG', quality=92)
    return out


def make_slide(out: str, title: str, subtitle: str, color: tuple = (15, 23, 42),
               accent: tuple = (0, 255, 200), W: int = 1920, H: int = 1080):
    """Slide simples: gradiente + título + subtítulo."""
    img = Image.new('RGB', (W, H), color)
    draw = ImageDraw.Draw(img)
    for y in range(0, H, 4):
        intensity = int(40 * (y / H))
        draw.line([(0, y), (W, y)], fill=(color[0]+intensity//2, color[1]+intensity//2, color[2]+intensity))
    draw.rectangle([100, 200, W-100, 210], fill=accent)
    draw.text((100, 280), title, fill=(255, 255, 255))
    draw.text((100, 420), subtitle, fill=(200, 200, 220))
    draw.text((100, H-80), "Garagem Academy · YouTube", fill=(150, 150, 170))
    img.save(out, 'PNG')
    return out


def get_audio_duration(audio: str) -> float:
    import wave
    try:
        with wave.open(audio, 'rb') as w:
            return w.getnframes() / w.getframerate()
    except:
        # fallback ffprobe
        r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                            "-of", "default=noprint_wrappers=1:nokey=1", audio], capture_output=True, text=True)
        return float(r.stdout.strip() or "10.0")


def compose(audio: str, avatar_img: str, slides: list, out: str,
            avatar_size: int = 320, avatar_margin: int = 40,
            position: str = "bottom-right", W: int = 1920, H: int = 1080,
            fps: int = 30):
    """Pipeline: 1) gera slides em vídeo 2) compõe avatar circular + bg + áudio."""
    if not os.path.isfile(audio):
        die(f"áudio não existe: {audio}")
    if not os.path.isfile(avatar_img):
        die(f"avatar não existe: {avatar_img}")
    
    audio_dur = get_audio_duration(audio)
    slide_dur = audio_dur / len(slides)
    print(f"audio: {audio_dur:.1f}s, {len(slides)} slides × {slide_dur:.1f}s", file=sys.stderr)
    
    tmp = tempfile.mkdtemp(prefix="demo-composition-")
    Path(tmp, "slides").mkdir()
    
    # 1) Gerar slides PNG (se algum não existir)
    slide_paths = []
    for i, s in enumerate(slides):
        if ":" in s:
            title, subtitle = s.split(":", 1)
        else:
            title, subtitle = s, ""
        sp = f"{tmp}/slides/slide-{i+1:03d}.png"
        make_slide(sp, title, subtitle)
        slide_paths.append(sp)
    
    # 2) Cada slide → MP4 de slide_dur
    bg_videos = []
    for i, sp in enumerate(slide_paths):
        bg = f"{tmp}/bg-{i+1:03d}.mp4"
        subprocess.run([
            "ffmpeg", "-y", "-loop", "1", "-i", sp, "-t", str(slide_dur),
            "-vf", f"scale={W}:{H},setsar=1", "-r", str(fps), "-pix_fmt", "yuv420p",
            "-c:v", "libx264", "-preset", "ultrafast", bg
        ], check=True, capture_output=True)
        bg_videos.append(bg)
    
    # 3) Concatenar
    concat = f"{tmp}/concat.txt"
    with open(concat, "w") as f:
        for bg in bg_videos:
            f.write(f"file '{bg}'\n")
    bg_full = f"{tmp}/background.mp4"
    subprocess.run([
        "ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", concat, "-c", "copy", bg_full
    ], check=True, capture_output=True)
    
    # 4) Avatar circular (estático)
    av_video = f"{tmp}/avatar-circular.mp4"
    subprocess.run([
        "ffmpeg", "-y", "-loop", "1", "-i", avatar_img, "-t", str(audio_dur),
        "-vf", f"scale={avatar_size}:{avatar_size}:force_original_aspect_ratio=decrease,"
               f"pad={avatar_size}:{avatar_size}:(ow-iw)/2:(oh-ih)/2:color=black@0,"
               f"format=yuva420p,"
               f"geq=lum='if(lt(hypot(X-{avatar_size//2},Y-{avatar_size//2}),{avatar_size//2}),255,0)':"
               f"cb='128':cr='128',setpts=PTS-STARTPTS",
        "-r", str(fps), "-pix_fmt", "yuv420p", "-c:v", "libx264", "-preset", "ultrafast", av_video
    ], check=True, capture_output=True)
    
    # 5) Posição
    positions = {
        "bottom-right": f"W-w-{avatar_margin}:H-h-{avatar_margin}",
        "bottom-left":  f"{avatar_margin}:H-h-{avatar_margin}",
        "top-right":    f"W-w-{avatar_margin}:{avatar_margin}",
        "top-left":     f"{avatar_margin}:{avatar_margin}",
    }
    pos = positions.get(position, positions["bottom-right"])
    
    # 6) Compor final
    subprocess.run([
        "ffmpeg", "-y",
        "-i", bg_full,
        "-i", av_video,
        "-i", audio,
        "-filter_complex", f"[1:v]setpts=PTS-STARTPTS[v];[0:v][v]overlay={pos}[out]",
        "-map", "[out]", "-map", "2:a",
        "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k",
        "-shortest", out,
    ], check=True, capture_output=True)
    
    print(f"✓ vídeo demo: {out}", file=sys.stderr)
    print(f"  duração: {audio_dur:.1f}s", file=sys.stderr)
    print(f"  resolução: {W}x{H}", file=sys.stderr)


def die(msg):
    print(f"erro: {msg}", file=sys.stderr)
    sys.exit(1)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--audio", help="áudio TTS (WAV/MP3)")
    ap.add_argument("--avatar-img", help="imagem do avatar (JPG/PNG)")
    ap.add_argument("--avatar-text", default="DIEGO", help="texto embaixo do avatar")
    ap.add_argument("--avatar-out", default="/tmp/avatar-default.jpg", help="salvar avatar aqui se avatar-img não fornecido")
    ap.add_argument("--slides", nargs="*", default=[
        "BEM-VINDO:Qwen3-TTS Multilíngue",
        "10 IDIOMAS:PT EN ES FR DE IT JA KO ZH RU",
        "16GB VRAM:Whisper + Qwen2-VL + FLUX",
    ], help="slides no formato 'Título:Subtítulo'")
    ap.add_argument("--out", default="/tmp/youtube-scene.mp4")
    ap.add_argument("--avatar-size", type=int, default=320)
    ap.add_argument("--avatar-margin", type=int, default=40)
    ap.add_argument("--avatar-position", default="bottom-right", choices=list(["bottom-right","bottom-left","top-right","top-left"]))
    ap.add_argument("--width", type=int, default=1920)
    ap.add_argument("--height", type=int, default=1080)
    ap.add_argument("--fps", type=int, default=30)
    args = ap.parse_args()
    
    # Se não tem avatar, gera um
    if not args.avatar_img:
        make_avatar(args.avatar_out, args.avatar_text)
        args.avatar_img = args.avatar_out
    
    if not args.audio:
        die("--audio obrigatório")
    
    compose(
        audio=args.audio,
        avatar_img=args.avatar_img,
        slides=args.slides,
        out=args.out,
        avatar_size=args.avatar_size,
        avatar_margin=args.avatar_margin,
        position=args.avatar_position,
        W=args.width, H=args.height, fps=args.fps,
    )


if __name__ == "__main__":
    main()
