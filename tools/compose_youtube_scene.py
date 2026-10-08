#!/usr/bin/env python3
"""
Composição estilo YouTube: avatar em círculo no canto + slide de fundo + áudio.

Uso:
  # 1) Com slide único (estático)
  python3 compose_youtube_scene.py --avatar-video /content/talk.mp4 \
    --background /content/slide.png --audio /content/tts.wav \
    --out /content/youtube-scene.mp4
  
  # 2) Com vídeo de fundo (animado)
  python3 compose_youtube_scene.py --avatar-video /content/talk.mp4 \
    --background-video /content/background.mp4 --audio /content/tts.wav \
    --out /content/youtube-scene.mp4
  
  # 3) Com sequência de slides (passando a cada 5s)
  python3 compose_youtube_scene.py --avatar-video /content/talk.mp4 \
    --background-slides /content/slide-001.png /content/slide-002.png /content/slide-003.png \
    --slide-duration 5 --audio /content/tts.wav --out /content/youtube-scene.mp4

Posição do avatar:
  --avatar-position bottom-right (padrão YouTube)
  --avatar-size 320 (pixels de diâmetro)
  --avatar-margin 40 (pixels da borda)
"""
from __future__ import annotations
import argparse, os, subprocess, sys, time
from pathlib import Path

def check_ffmpeg():
    r = subprocess.run(["ffmpeg", "-version"], capture_output=True, text=True)
    return r.returncode == 0

def get_video_info(path: str) -> tuple:
    """Retorna (duration, width, height, fps)"""
    cmd = [
        "ffprobe", "-v", "error",
        "-select_streams", "v:0",
        "-show_entries", "stream=width,height,r_frame_rate,duration",
        "-of", "default=noprint_wrappers=1",
        path,
    ]
    r = subprocess.run(cmd, capture_output=True, text=True)
    info = {}
    for line in r.stdout.strip().split("\n"):
        if "=" in line:
            k, v = line.split("=", 1)
            info[k.strip()] = v.strip()
    w = int(info.get("width", 1920))
    h = int(info.get("height", 1080))
    fps_str = info.get("r_frame_rate", "30/1")
    try:
        num, den = fps_str.split("/")
        fps = int(num) / int(den)
    except: fps = 30.0
    dur = float(info.get("duration", 10.0))
    return dur, w, h, fps

def get_audio_duration(path: str) -> float:
    cmd = [
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        path,
    ]
    r = subprocess.run(cmd, capture_output=True, text=True)
    try: return float(r.stdout.strip())
    except: return 10.0

def make_slide_video(slides: list, slide_duration: int, output: str, width: int = 1920, height: int = 1080, fps: int = 30):
    """Cria vídeo de fundo a partir de sequência de slides PNG."""
    if len(slides) == 1:
        # slide estático: usar direto
        cmd = [
            "ffmpeg", "-y", "-loop", "1", "-i", slides[0],
            "-t", str(slide_duration),
            "-vf", f"scale={width}:{height}:force_original_aspect_ratio=decrease,pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,setsar=1",
            "-r", str(fps),
            "-pix_fmt", "yuv420p",
            output,
        ]
        subprocess.run(cmd, check=True, capture_output=True)
    else:
        # Concatenar slides em vídeo
        # 1) Cada slide → vídeo de N segundos
        tmp = "/tmp/compose-slides"
        Path(tmp).mkdir(exist_ok=True)
        for i, slide in enumerate(slides):
            slide_video = f"{tmp}/slide-{i:03d}.mp4"
            cmd = [
                "ffmpeg", "-y", "-loop", "1", "-i", slide,
                "-t", str(slide_duration),
                "-vf", f"scale={width}:{height}:force_original_aspect_ratio=decrease,pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,setsar=1",
                "-r", str(fps),
                "-pix_fmt", "yuv420p",
                slide_video,
            ]
            subprocess.run(cmd, check=True, capture_output=True)
        # 2) Concatenar
        concat_file = f"{tmp}/concat.txt"
        with open(concat_file, "w") as f:
            for i in range(len(slides)):
                f.write(f"file 'slide-{i:03d}.mp4'\n")
        cmd = [
            "ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", concat_file,
            "-c", "copy", output,
        ]
        subprocess.run(cmd, check=True, capture_output=True)


def compose(args):
    """Pipeline: gerar bg → overlay avatar com máscara circular → mux audio."""
    if not check_ffmpeg():
        die("ffmpeg não instalado. apt-get install ffmpeg")
    
    # Duração final = duração do áudio (avatar deve estar em sync)
    audio_dur = get_audio_duration(args.audio)
    av_dur, av_w, av_h, av_fps = get_video_info(args.avatar_video)
    final_dur = max(audio_dur, av_dur)
    print(f"audio: {audio_dur:.1f}s, avatar: {av_dur:.1f}s, final: {final_dur:.1f}s", file=sys.stderr)
    
    # Resolução do output
    out_w = args.width
    out_h = args.height
    
    # Background
    tmp = "/tmp/compose-final"
    Path(tmp).mkdir(exist_ok=True)
    bg_video = f"{tmp}/00-background.mp4"
    
    if args.background_video:
        # Trim/copy vídeo de fundo
        cmd = [
            "ffmpeg", "-y", "-i", args.background_video,
            "-t", str(final_dur),
            "-vf", f"scale={out_w}:{out_h}:force_original_aspect_ratio=decrease,pad={out_w}:{out_h}:(ow-iw)/2:(oh-ih)/2,setsar=1",
            "-r", str(args.fps),
            "-pix_fmt", "yuv420p",
            bg_video,
        ]
        subprocess.run(cmd, check=True, capture_output=True)
    elif args.background_slides:
        # Sequência de slides
        total_slide_dur = (len(args.background_slides) - 1) * args.slide_duration
        if total_slide_dur < final_dur:
            # repetir último slide
            args.background_slides = args.background_slides + [args.background_slides[-1]] * 5
        make_slide_video(args.background_slides, args.slide_duration, bg_video, out_w, out_h, args.fps)
    elif args.background:
        # Slide estático único
        make_slide_video([args.background], int(final_dur), bg_video, out_w, out_h, args.fps)
    else:
        # Sem background, fundo preto
        cmd = [
            "ffmpeg", "-y", "-f", "lavfi", "-i", f"color=c=0x1a1a2e:s={out_w}x{out_h}:d={final_dur}:r={args.fps}",
            "-pix_fmt", "yuv420p",
            bg_video,
        ]
        subprocess.run(cmd, check=True, capture_output=True)
    
    print(f"background OK: {bg_video}", file=sys.stderr)
    
    # Overlay avatar com máscara circular
    # 1) Converter avatar para mp4 quadrado (resize)
    # 2) Aplicar máscara circular
    # 3) Posicionar no canto (bottom-right padrão)
    
    avatar_size = args.avatar_size
    margin = args.avatar_margin
    if args.avatar_position == "bottom-right":
        x_expr = f"main_w-overlay_w-{margin}"
        y_expr = f"main_h-overlay_h-{margin}"
    elif args.avatar_position == "bottom-left":
        x_expr = f"{margin}"
        y_expr = f"main_h-overlay_h-{margin}"
    elif args.avatar_position == "top-right":
        x_expr = f"main_w-overlay_w-{margin}"
        y_expr = f"{margin}"
    elif args.avatar_position == "top-left":
        x_expr = f"{margin}"
        y_expr = f"{margin}"
    else:
        die(f"posição inválida: {args.avatar_position}")
    
    # ffmpeg filter: redimensiona avatar, aplica máscara circular, sobrepõe
    # A máscara circular é um radial gradient com alpha
    overlay_video = f"{tmp}/01-overlay.mp4"
    cmd = [
        "ffmpeg", "-y",
        "-i", bg_video,
        "-i", args.avatar_video,
        "-filter_complex",
        # redimensiona avatar pra tamanho desejado
        f"[1:v]scale={avatar_size}:{avatar_size}:force_original_aspect_ratio=decrease,"
        f"pad={avatar_size}:{avatar_size}:(ow-iw)/2:(oh-ih)/2:color=black@0,"
        # máscara circular usando geq
        f"format=yuva420p,"
        f"geq=lum='if(lt(hypot(X-{avatar_size//2},Y-{avatar_size//2}),{avatar_size//2}),255,0)':"
        f"cb='128':cr='128',"
        f"setpts=PTS-STARTPTS[v];"
        # overlay
        f"[0:v][v]overlay={x_expr}:{y_expr}:shortest=0[out]",
        "-map", "[out]",
        "-map", "0:a?",  # áudio do background se tiver
        "-t", str(final_dur),
        "-r", str(args.fps),
        "-pix_fmt", "yuv420p",
        "-c:v", "libx264",
        "-preset", "medium",
        "-crf", "20",
        overlay_video,
    ]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        print("STDERR:", r.stderr[-2000:], file=sys.stderr)
        die("falha no overlay")
    
    print(f"overlay OK: {overlay_video}", file=sys.stderr)
    
    # Mux com o áudio TTS
    cmd = [
        "ffmpeg", "-y",
        "-i", overlay_video,
        "-i", args.audio,
        "-map", "0:v",
        "-map", "1:a",
        "-c:v", "copy",
        "-c:a", "aac",
        "-b:a", "192k",
        "-shortest",
        args.out,
    ]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        print("STDERR:", r.stderr[-2000:], file=sys.stderr)
        die("falha no mux")
    
    # Cleanup
    print(f"\n✓ vídeo final: {args.out}", file=sys.stderr)
    print(f"  duração: {final_dur:.1f}s", file=sys.stderr)
    print(f"  resolução: {out_w}x{out_h} @ {args.fps}fps", file=sys.stderr)
    print(f"  avatar: {avatar_size}x{avatar_size} em {args.avatar_position}", file=sys.stderr)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--avatar-video", required=True, help="vídeo do talking head (mp4)")
    ap.add_argument("--audio", required=True, help="áudio TTS (wav/mp3)")
    
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--background", help="imagem de fundo única (PNG/JPG)")
    g.add_argument("--background-video", help="vídeo de fundo (mp4)")
    g.add_argument("--background-slides", nargs="+", help="sequência de slides (PNG/JPG)")
    
    ap.add_argument("--slide-duration", type=int, default=5, help="segundos por slide")
    ap.add_argument("--avatar-position", default="bottom-right", 
                    choices=["bottom-right","bottom-left","top-right","top-left"])
    ap.add_argument("--avatar-size", type=int, default=320, help="diâmetro do círculo (px)")
    ap.add_argument("--avatar-margin", type=int, default=40, help="margem da borda (px)")
    ap.add_argument("--width", type=int, default=1920)
    ap.add_argument("--height", type=int, default=1080)
    ap.add_argument("--fps", type=int, default=30)
    ap.add_argument("--out", default="/tmp/youtube-scene.mp4")
    args = ap.parse_args()
    
    compose(args)


def die(msg):
    print(f"erro: {msg}", file=sys.stderr)
    sys.exit(1)


if __name__ == "__main__":
    main()
