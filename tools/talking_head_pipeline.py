#!/usr/bin/env python3
"""
Talking head pipeline completo: foto → 3D mesh → talking head animado com TTS.
Tudo em 1 comando. Roda no Duck (RTX 3060, 12GB) ou qualquer GPU.

Uso:
  python3 talking_head_pipeline.py \
    --image /tmp/diego.jpg \
    --audio /tmp/audio.wav \
    --text "Texto pra TTS" \
    --out /tmp/scene.mp4

Dependências (instala se faltar):
  pip install torch torchvision trimesh pyrender av openai-whisper qwen-tts diffusers transformers accelerate safetensors

Modelos (baixa on-demand):
  - TripoSR ckpt: ~2GB em /root/triposr/
  - Qwen3-TTS 1.7B CustomVoice: ~4GB em ~/.cache/huggingface/
"""
from __future__ import annotations
import argparse, os, sys, subprocess, time
from pathlib import Path


def run(cmd, check=True, env=None):
    print(f"$ {cmd}", file=sys.stderr)
    return subprocess.run(cmd, shell=True, check=check, capture_output=True, text=True, env=env)


def step(msg):
    print(f"\n=== {msg} ===", file=sys.stderr)


def ensure_deps():
    """Instala dependências se faltar."""
    print("verificando dependências...", file=sys.stderr)
    base = [
        "torch", "torchvision", "trimesh", "pyrender", "Pillow",
        "numpy", "scipy", "av", "ffmpeg-python", "openai-whisper"
    ]
    tts = ["qwen-tts"]
    img = ["diffusers", "transformers", "accelerate", "safetensors"]
    all_pkgs = base + tts + img
    run(f"pip install -q {' '.join(all_pkgs)} 2>&1 | tail -3", check=False)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--image", required=True, help="foto de entrada (JPG/PNG)")
    ap.add_argument("--audio", help="áudio TTS pré-gerado (WAV/MP3)")
    ap.add_argument("--text", help="texto pra gerar TTS (se --audio não fornecido)")
    ap.add_argument("--lang", default="portuguese", help="idioma do TTS")
    ap.add_argument("--out", default="/tmp/scene.mp4", help="vídeo final")
    ap.add_argument("--bg-image", help="imagem de fundo (opcional)")
    ap.add_argument("--skip-3d", action="store_true", help="pular etapa 3D (usar imagem direto)")
    ap.add_argument("--skip-lipsync", action="store_true", help="pular SadTalker (avatar estático)")
    ap.add_argument("--skip-tts", action="store_true", help="usar --audio fornecido, sem TTS")
    ap.add_argument("--bg-color", default="0x1a1a2e", help="cor de fundo se sem bg-image (hex)")
    args = ap.parse_args()

    if not os.path.isfile(args.image):
        sys.exit(f"erro: imagem não existe: {args.image}")
    if not args.skip_tts and not args.audio and not args.text:
        sys.exit("erro: --audio ou --text obrigatório (ou --skip-tts)")

    ensure_deps()

    Path("/tmp/th").mkdir(exist_ok=True)
    out_image = "/tmp/th/face.jpg"
    out_audio = args.audio or "/tmp/th/audio.wav"
    out_mesh = "/tmp/th/face.glb"
    out_render = "/tmp/th/face-render.png"
    out_lipsync = "/tmp/th/lipsync.mp4"

    # 1) Crop da face
    step("PASSO 1: preparar imagem")
    if not args.skip_3d:
        run(f"python3 -c 'from PIL import Image; img = Image.open(\"{args.image}\").convert(\"RGB\"); w,h=img.size; s=min(w,h); img.crop(((w-s)//2,(h-s)//2,(w+s)//2,(h+s)//2)).resize((512,512)).save(\"{out_image}\")'")
    else:
        run(f"cp {args.image} {out_image}")

    # 2) TTS
    if not args.skip_tts and not args.audio:
        step("PASSO 2: gerar TTS (Qwen3-TTS 1.7B)")
        t0 = time.time()
        run(f'''python3 -c "
from qwen_tts import Qwen3TTSModel
import torch
print('loading...', flush=True)
model = Qwen3TTSModel.from_pretrained('Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice', device_map='cuda', dtype=torch.bfloat16)
print('generating...', flush=True)
wavs, sr = model.generate_custom_voice(text='{args.text}', language='{args.lang}', speaker='Vivian')
import numpy as np, scipy.io.wavfile as wf
wav = np.concatenate([np.asarray(w, dtype=np.float32) for w in wavs])
wav = (wav * 32767).clip(-32768, 32767).astype(np.int16)
wf.write('{out_audio}', sr, wav)
print(f'OK {{sr}}Hz, {{len(wav)/sr:.2f}}s')
"''')
        print(f"TTS em {time.time()-t0:.1f}s → {out_audio}", file=sys.stderr)

    # 3) Foto → 3D mesh (TripoSR)
    if not args.skip_3d:
        step("PASSO 3: TripoSR (foto → mesh 3D)")
        t0 = time.time()
        run("pip install -q tsr 2>&1 | tail -3", check=False)
        ckpt = "/root/triposr/TripoSR.ckpt"
        if not os.path.exists(ckpt):
            os.makedirs("/root/triposr", exist_ok=True)
            run(f"wget -q https://huggingface.co/spaces/stabilityai/TripoSR/resolve/main/TripoSR.ckpt -O {ckpt} 2>&1 | tail -3", check=False)
        run(f'''python3 -c "
import sys
sys.path.insert(0, '/root/triposr')
from tsr.system import TSR
from PIL import Image
import torch
print('loading TripoSR...', flush=True)
model = TSR.from_pretrained('/root/triposr', device='cuda')
img = Image.open('{out_image}').convert('RGB')
print('processing...', flush=True)
with torch.inference_mode():
    scene_codes = model([img], device='cuda')
mesh = scene_codes.extract_mesh(i=0, simplify=0.95)
mesh.export('{out_mesh}')
print('mesh salvo em {out_mesh}')
"''')
        print(f"TripoSR em {time.time()-t0:.1f}s → {out_mesh}", file=sys.stderr)

    # 4) Render mesh
    if not args.skip_3d and not args.skip_lipsync:
        step("PASSO 4: render mesh (frente)")
        t0 = time.time()
        run("apt-get install -y -qq libgl1-mesa-glx 2>&1 | tail -3", check=False)
        run("pip install -q trimesh pyrender 2>&1 | tail -3", check=False)
        run(f'''python3 -c "
import trimesh
import numpy as np
mesh = trimesh.load('{out_mesh}', force='mesh')
if isinstance(mesh, trimesh.Scene):
    mesh = mesh.dump(concatenate=True)
mesh.vertices -= mesh.centroid
mx = np.max(np.abs(mesh.vertices))
if mx > 0: mesh.vertices /= mx
import pyrender
from PIL import Image
scene = pyrender.Scene(bg_color=[0,0,0,1])
material = pyrender.MetallicRoughnessMaterial(metallicFactor=0.0, roughnessFactor=0.6, baseColorFactor=[0.85,0.78,0.7,1.0])
py_mesh = pyrender.Mesh.from_trimesh(mesh, material=material)
scene.add(py_mesh)
camera = pyrender.PerspectiveCamera(yfov=np.pi/3.0)
scene.add(camera, pose=[[1,0,0,0],[0,1,0,0],[0,0,1,3],[0,0,0,1]])
light = pyrender.DirectionalLight(color=[1,1,1], intensity=3.0)
scene.add(light, pose=[[1,0,0,-1],[0,1,0,1],[0,0,1,2],[0,0,0,1]])
renderer = pyrender.OffscreenRenderer(1024, 1024)
color, _ = renderer.render(scene)
renderer.delete()
Image.fromarray(color).save('{out_render}')
print('render salvo em {out_render}')
"''')
        print(f"render em {time.time()-t0:.1f}s → {out_render}", file=sys.stderr)

    # 5) Lip sync (SadTalker)
    if not args.skip_lipsync:
        step("PASSO 5: SadTalker (foto + audio → vídeo)")
        t0 = time.time()
        repo = "/root/SadTalker"
        if not os.path.exists(repo):
            run(f"git clone --depth 1 https://github.com/OpenTalker/SadTalker.git {repo} 2>&1 | tail -3")
        # Baixar checkpoints (skip se já tem)
        ckpt_dir = f"{repo}/checkpoints"
        if not os.path.exists(f"{ckpt_dir}/SadTalker_V0.0.2_256.safetensors"):
            print("  baixando checkpoints (1-2min)...", file=sys.stderr)
            run(f"cd {repo} && bash download.sh 2>&1 | tail -5", check=False)
        run(f"pip install -q -r {repo}/requirements.txt 2>&1 | tail -3", check=False)
        # Rodar
        run(f"cd {repo} && python3 inference.py --driven_audio {out_audio} --source_image {out_render if not args.skip_3d else out_image} --result_dir /tmp/th/ --enhancer gfpgan --size 256 --expression_scale 1.0 --pose_style 0 2>&1 | tail -10")
        # SadTalker salva em /tmp/th/<image_name>__<audio_name>.mp4
        candidates = list(Path("/tmp/th").glob("*.mp4"))
        if candidates:
            for c in candidates:
                if "lipsync" not in c.name:
                    run(f"mv {c} {out_lipsync}")
                    break
        print(f"SadTalker em {time.time()-t0:.1f}s → {out_lipsync}", file=sys.stderr)

    # 6) Compor cena YouTube
    step("PASSO 6: compor cena final (avatar no canto + fundo + áudio)")
    avatar_video = out_lipsync if not args.skip_lipsync else out_image
    if not os.path.exists(avatar_video):
        avatar_video = out_image
    bg = args.bg_image or out_image
    if not os.path.exists(bg):
        # Criar fundo simples
        run(f"python3 -c 'from PIL import Image; Image.new(\"RGB\",(1920,1080),(26,26,46)).save(\"{bg}\")'")
        bg = out_image
    run(f'''python3 -c "
import subprocess
cmd = [
    'python3', '/root/sessionflow/tools/make_demo_video.py',
    '--audio', '{out_audio}',
    '--avatar-img', '{out_image}',
    '--bg-image', '{bg}',
    '--avatar-size', '320',
    '--out', '{args.out}',
]
print(' '.join(cmd))
subprocess.run(cmd, check=True)
"''')
    print(f"\n✓ vídeo final: {args.out}", file=sys.stderr)


if __name__ == "__main__":
    main()
