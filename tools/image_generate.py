#!/usr/bin/env python3
"""
Geração de imagem local com Stable Diffusion / FLUX rodando na T4 do Colab.

Modelos suportados (qualidade ↑, tempo ↑):
  - sd-turbo     (512x512, 1-2s,  ~3GB) — iteração rápida
  - sdxl-turbo   (512x512, 1-2s,  ~6GB) — SDXL em 1 step
  - sdxl         (1024x1024, 15-30s, ~8GB) — sweet-spot qualidade/custo
  - sdxl-lightning (1024x1024, 3-8s, ~8GB) — SDXL em 4 steps
  - sd3-medium   (1024x1024, 30-60s, ~12GB) — top qualidade
  - flux-schnell (1024x1024, 30-60s, ~16GB CPU offload) — top open-source
  - flux-dev     (1024x1024, 60-90s, ~16GB CPU offload) — melhor geral

Uso:
  python3 image_generate.py --prompt "a corgi in a spacesuit, digital art" --model sdxl --out /tmp/corgi.png
  python3 image_generate.py --prompt "..." --model flux-schnell --steps 4 --out /tmp/img.png
  python3 image_generate.py --batch jobs.jsonl --out-dir /tmp/imgs/
"""
from __future__ import annotations
import argparse, json, os, sys, time
from pathlib import Path

import torch
from PIL import Image

MODEL_REGISTRY = {
    "sd-turbo":      {"hf": "stabilityai/sd-turbo", "steps": 1,   "size": (512, 512)},
    "sdxl-turbo":    {"hf": "stabilityai/sdxl-turbo", "steps": 1, "size": (512, 512)},
    "sdxl":          {"hf": "stabilityai/stable-diffusion-xl-base-1.0", "steps": 30, "size": (1024, 1024)},
    "sdxl-lightning":{"hf": "ByteDance/SDXL-Lightning", "steps": 4, "size": (1024, 1024)},
    "sd3-medium":    {"hf": "stabilityai/stable-diffusion-3-medium-diffusers", "steps": 28, "size": (1024, 1024)},
    "flux-schnell":  {"hf": "black-forest-labs/FLUX.1-schnell", "steps": 4, "size": (1024, 1024), "offload": True},
    "flux-dev":      {"hf": "black-forest-labs/FLUX.1-dev", "steps": 28, "size": (1024, 1024), "offload": True},
}


def load_pipeline(model_key: str):
    """Carrega pipeline. Aplica CPU offload automático pra modelos >12GB."""
    if model_key not in MODEL_REGISTRY:
        raise ValueError(f"modelo desconhecido: {model_key}. Opções: {list(MODEL_REGISTRY)}")
    info = MODEL_REGISTRY[model_key]
    hf_id = info["hf"]
    t0 = time.time()
    dtype = torch.bfloat16
    
    if "flux" in model_key:
        from diffusers import FluxPipeline
        pipe = FluxPipeline.from_pretrained(hf_id, torch_dtype=dtype)
    elif "sd3" in model_key:
        from diffusers import StableDiffusion3Pipeline
        pipe = StableDiffusion3Pipeline.from_pretrained(hf_id, torch_dtype=dtype)
    elif "sdxl" in model_key or "xl" in model_key.lower():
        from diffusers import StableDiffusionXLPipeline
        pipe = StableDiffusionXLPipeline.from_pretrained(hf_id, torch_dtype=dtype, variant="fp16")
    else:
        from diffusers import StableDiffusionPipeline
        pipe = StableDiffusionPipeline.from_pretrained(hf_id, torch_dtype=dtype)
    
    if info.get("offload") or not torch.cuda.is_available():
        pipe.enable_sequential_cpu_offload()
    else:
        pipe = pipe.to("cuda")
    
    print(f"loaded {hf_id} in {time.time()-t0:.1f}s", file=sys.stderr)
    return pipe, info


def generate(pipe, info, prompt: str, negative: str = "", steps: int = None,
             out: str = "/tmp/img.png", seed: int = None) -> str:
    steps = steps or info["steps"]
    size = info["size"]
    generator = None
    if seed is not None:
        generator = torch.Generator("cpu").manual_seed(seed)
    
    t0 = time.time()
    result = pipe(
        prompt=prompt,
        negative_prompt=negative or None,
        num_inference_steps=steps,
        height=size[0],
        width=size[1],
        generator=generator,
    )
    elapsed = time.time() - t0
    
    img: Image.Image = result.images[0]
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    img.save(out)
    size_kb = Path(out).stat().st_size // 1024
    print(f"  {Path(out).name:30s}  {elapsed:5.1f}s  {size[0]}x{size[1]}  {size_kb:>5}KB  {out}", file=sys.stderr)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--prompt", help="texto descritivo da imagem")
    ap.add_argument("--negative", default="", help="negative prompt")
    ap.add_argument("--model", default="sdxl-turbo", choices=list(MODEL_REGISTRY.keys()))
    ap.add_argument("--steps", type=int, help="override do número de inference steps")
    ap.add_argument("--seed", type=int, help="random seed (reprodutibilidade)")
    ap.add_argument("--out", default="/tmp/img.png")
    ap.add_argument("--batch", help="JSONL com {prompt, out, [model], [steps]} por linha")
    ap.add_argument("--out-dir", default="/tmp/imgs")
    args = ap.parse_args()
    
    if not torch.cuda.is_available():
        print("⚠ sem CUDA — geração será MUITO lenta (5-10min por imagem)", file=sys.stderr)
    
    pipe, info = load_pipeline(args.model)
    
    if args.batch:
        Path(args.out_dir).mkdir(parents=True, exist_ok=True)
        with open(args.batch) as f:
            for ln, line in enumerate(f, 1):
                line = line.strip()
                if not line or line.startswith("#"): continue
                job = json.loads(line)
                out = job.pop("out", None) or f"{args.out_dir}/{job.get('model', args.model)}-{ln:03d}.png"
                # se job tem model diferente, recarrega (caro)
                if "model" in job and job["model"] != args.model:
                    print(f"⚠ job {ln} tem model={job['model']}, ignora (recarregamento custoso)", file=sys.stderr)
                generate(pipe, info, prompt=job["prompt"], negative=job.get("negative", args.negative),
                         steps=job.get("steps"), out=out, seed=job.get("seed"))
    else:
        if not args.prompt:
            ap.error("--prompt ou --batch obrigatório")
        generate(pipe, info, args.prompt, args.negative, args.steps, args.out, args.seed)


if __name__ == "__main__":
    main()
