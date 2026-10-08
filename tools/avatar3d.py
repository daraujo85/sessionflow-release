#!/usr/bin/env python3
"""
Avatar 3D pra thumbnail. Gera mesh 3D a partir de TEXTO ou FOTO, depois renderiza PNG.

Modelos suportados (qualidade ↑, VRAM ↑, tempo ↑):
  - triposr        (foto → mesh, ~3GB, 2-5s) — rápido, mesh sem textura
  - hunyuan3d-2mini (text/foto → mesh com textura, ~8GB, 30-60s) — sweet-spot
  - hunyuan3d-2    (text/foto → mesh com textura, ~14GB, 60-120s CPU offload) — top

Render:
  - trimesh + pyrender (CPU, 5-10s por pose)
  - múltiplas poses: frente / 3/4 / perfil / costas

Uso:
  # 1) Texto → 3D avatar (Hunyuan3D-2 mini)
  python3 avatar3d.py --prompt "a friendly cyberpunk hacker, full body, 16:9 composition" --model hunyuan3d-2mini --out /tmp/avatar.glb
  
  # 2) Foto → 3D (TripoSR, super rápido)
  python3 avatar3d.py --image /tmp/diego.jpg --model triposr --out /tmp/diego.glb
  
  # 3) Renderizar PNG de uma pose específica
  python3 avatar3d.py --render /tmp/avatar.glb --pose front --bg white --out /tmp/thumb.png
  
  # 4) Pipeline completo: foto → 3D → 4 poses PNG (pra escolher thumb)
  python3 avatar3d.py --image /tmp/diego.jpg --model triposr --out /tmp/avatar.glb --render-all --out-dir /tmp/poses/
"""
from __future__ import annotations
import argparse, json, os, sys, time
from pathlib import Path

DEFAULT_TRI_POSES = {
    "front": (0, 0, 0),
    "3/4":   (0, -30, 0),
    "side":  (0, -90, 0),
    "back":  (0, 180, 0),
    "top":   (-30, 0, 0),
}

MODELS = {
    "triposr":         {"hf": "stabilityai/TripoSR",                  "type": "image-to-mesh"},
    "hunyuan3d-2mini": {"hf": "tencent/Hunyuan3D-2mini",             "type": "text-or-image-to-mesh"},
    "hunyuan3d-2":     {"hf": "tencent/Hunyuan3D-2",                 "type": "text-or-image-to-mesh", "offload": True},
}


def load_image(image_path: str):
    from PIL import Image
    return Image.open(image_path).convert("RGB")


def generate_mesh(model_key: str, prompt: str = None, image_path: str = None, out: str = "/tmp/avatar.glb") -> str:
    """Gera mesh 3D. model_key define a engine."""
    info = MODELS[model_key]
    t0 = time.time()
    print(f"→ carregando {info['hf']}...", file=sys.stderr)
    
    if model_key == "triposr":
        from tsr.system import TSR
        import torch
        if not image_path:
            raise ValueError("TripoSR requer --image (foto de entrada)")
        model = TSR.from_pretrained(info["hf"], device="cuda" if torch.cuda.is_available() else "cpu")
        image = load_image(image_path)
        print(f"  processando imagem ({image.size})...", file=sys.stderr)
        with torch.inference_mode():
            scene_codes = model([image], device="cuda" if torch.cuda.is_available() else "cpu")
        mesh = scene_codes.extract_mesh(i=0, simplify=0.95)
        mesh.export(out)
        print(f"  ✓ {time.time()-t0:.1f}s mesh salvo → {out}", file=sys.stderr)
        return out
    
    elif model_key.startswith("hunyuan3d"):
        from hy3dgen.shapegen import Hunyuan3DDiTFlowMatchingPipeline
        import torch
        if not prompt and not image_path:
            raise ValueError("Hunyuan3D requer --prompt OU --image")
        if info.get("offload"):
            pipeline = Hunyuan3DDiTFlowMatchingPipeline.from_pretrained(
                info["hf"], torch_dtype=torch.float16, device="cpu"
            )
            pipeline.enable_cpu_offload()
        else:
            pipeline = Hunyuan3DDiTFlowMatchingPipeline.from_pretrained(
                info["hf"], torch_dtype=torch.float16, device="cuda"
            )
        kwargs = {}
        if image_path:
            kwargs["image"] = load_image(image_path)
        if prompt:
            kwargs["prompt"] = prompt
        mesh = pipeline(**kwargs)[0]
        mesh.export(out)
        print(f"  ✓ {time.time()-t0:.1f}s mesh salvo → {out}", file=sys.stderr)
        return out
    
    raise ValueError(f"modelo não suportado: {model_key}")


def render_pose(mesh_path: str, pose: str = "front", bg: str = "white",
                resolution: tuple = (1024, 1024), out: str = "/tmp/thumb.png") -> str:
    """Renderiza mesh em PNG com pose específica. Funciona offline (CPU)."""
    import trimesh
    import numpy as np
    
    t0 = time.time()
    print(f"→ renderizando pose '{pose}' em {resolution}...", file=sys.stderr)
    mesh = trimesh.load(mesh_path, force="mesh")
    if isinstance(mesh, trimesh.Scene):
        mesh = mesh.dump(concatenate=True)
    
    # Centralizar e normalizar
    mesh.vertices -= mesh.centroid
    max_dim = np.max(np.abs(mesh.vertices))
    if max_dim > 0:
        mesh.vertices /= max_dim
    
    # Aplicar rotação
    if pose in DEFAULT_TRI_POSES:
        rx, ry, rz = DEFAULT_TRI_POSES[pose]
        rot = trimesh.transformations.euler_matrix(np.radians(rx), np.radians(ry), np.radians(rz))
        mesh.apply_transform(rot)
    
    # Tentar pyrender primeiro, fallback para matplotlib
    try:
        import pyrender, PIL.Image
        scene = pyrender.Scene(bg_color=bg_rgb(bg))
        material = pyrender.MetallicRoughnessMaterial(
            metallicFactor=0.0, roughnessFactor=0.6, baseColorFactor=[0.8, 0.8, 0.85, 1.0]
        )
        py_mesh = pyrender.Mesh.from_trimesh(mesh, material=material)
        scene.add(py_mesh)
        # Câmera
        camera = pyrender.PerspectiveCamera(yfov=np.pi / 3.0)
        cam_pos = np.array([0, 0, 3.0])
        scene.add(camera, pose=look_at(cam_pos, np.zeros(3)))
        # Luz
        light = pyrender.DirectionalLight(color=[1.0, 1.0, 1.0], intensity=3.0)
        scene.add(light, pose=look_at([1, 1, 1], np.zeros(3)))
        
        renderer = pyrender.OffscreenRenderer(*resolution)
        color, _ = renderer.render(scene)
        renderer.delete()
        Image.fromarray(color).save(out)
    except ImportError:
        # Fallback: matplotlib
        import matplotlib.pyplot as plt
        from mpl_toolkits.mplot3d.art3d import Poly3DCollection
        fig = plt.figure(figsize=(resolution[0]/100, resolution[1]/100), dpi=100)
        ax = fig.add_subplot(111, projection='3d')
        tris = mesh.triangles
        ax.add_collection3d(Poly3DCollection(tris, alpha=1.0, facecolor=(0.8, 0.8, 0.85)))
        lim = 1.2
        ax.set_xlim(-lim, lim); ax.set_ylim(-lim, lim); ax.set_zlim(-lim, lim)
        ax.axis('off')
        fig.patch.set_facecolor(bg)
        plt.savefig(out, dpi=100, bbox_inches='tight', pad_inches=0, facecolor=bg)
        plt.close()
    
    print(f"  ✓ {time.time()-t0:.1f}s render salvo → {out}", file=sys.stderr)
    return out


def bg_rgb(name: str):
    import numpy as np
    return {
        "white": [1, 1, 1, 1],
        "black": [0, 0, 0, 1],
        "blue":  [0.2, 0.5, 0.9, 1],
        "gray":  [0.9, 0.9, 0.9, 1],
    }.get(name, [1, 1, 1, 1])


def look_at(eye, target):
    import numpy as np
    eye, target = np.array(eye), np.array(target)
    forward = target - eye
    forward /= np.linalg.norm(forward)
    up = np.array([0, 1, 0])
    right = np.cross(forward, up)
    right /= np.linalg.norm(right)
    up = np.cross(right, forward)
    return np.array([
        [right[0], right[1], right[2], -np.dot(right, eye)],
        [up[0],    up[1],    up[2],    -np.dot(up, eye)],
        [-forward[0], -forward[1], -forward[2], np.dot(forward, eye)],
        [0, 0, 0, 1]
    ])


def render_all_poses(mesh_path: str, out_dir: str, bg: str = "white",
                      poses: list = None, resolution: tuple = (1024, 1024)) -> list:
    """Renderiza múltiplas poses. Útil pra escolher thumb."""
    poses = poses or ["front", "3/4", "side", "back"]
    Path(out_dir).mkdir(parents=True, exist_ok=True)
    outputs = []
    for p in poses:
        out = f"{out_dir}/pose-{p}.png"
        render_pose(mesh_path, p, bg, resolution, out)
        outputs.append(out)
    return outputs


def main():
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--prompt", help="texto descritivo (Hunyuan3D)")
    g.add_argument("--image", help="imagem de entrada (TripoSR ou Hunyuan3D)")
    g.add_argument("--render", help="apenas renderizar mesh existente (sem regenerar)")
    g.add_argument("--render-all", action="store_true", help="renderizar todas as poses")
    ap.add_argument("--model", default="triposr", choices=list(MODELS.keys()))
    ap.add_argument("--out", default="/tmp/avatar.glb")
    ap.add_argument("--out-dir", default="/tmp/avatar-poses")
    ap.add_argument("--pose", default="front", choices=list(DEFAULT_TRI_POSES.keys()))
    ap.add_argument("--bg", default="white", choices=["white", "black", "blue", "gray"])
    ap.add_argument("--resolution", default="1024x1024")
    args = ap.parse_args()
    
    w, h = map(int, args.resolution.split("x"))
    
    if args.render:
        # só render
        render_pose(args.render, args.pose, args.bg, (w, h), args.out)
    elif args.render_all:
        # pipeline: gera mesh + renderiza todas as poses
        if args.prompt:
            mesh = generate_mesh(args.model, prompt=args.prompt, out=args.out)
        elif args.image:
            mesh = generate_mesh(args.model, image_path=args.image, out=args.out)
        else:
            die("--render-all precisa de --prompt ou --image")
        outputs = render_all_poses(mesh, args.out_dir, args.bg, resolution=(w, h))
        # Salvar manifest
        Path(args.out_dir, "manifest.json").write_text(json.dumps({
            "mesh": mesh, "model": args.model, "poses": outputs, "background": args.bg
        }, indent=2))
        print(f"\n✓ {len(outputs)} poses renderizadas em {args.out_dir}/", file=sys.stderr)
    else:
        # só gera mesh
        if args.prompt:
            generate_mesh(args.model, prompt=args.prompt, out=args.out)
        elif args.image:
            generate_mesh(args.model, image_path=args.image, out=args.out)
        else:
            die("precisa de --prompt ou --image")


def die(msg):
    print(f"erro: {msg}", file=sys.stderr)
    sys.exit(1)


if __name__ == "__main__":
    main()
