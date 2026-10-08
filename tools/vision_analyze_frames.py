#!/usr/bin/env python3
"""
Vision model wrapper — analisa frames extraídos pela skill transcribe-audio-video
com Qwen2-VL 2B Instruct rodando na T4 do Colab. Roda offline (cache HF), sem API.

Uso:
  # analisar pasta de frames extraída pela skill transcribe
  python3 vision_analyze_frames.py /tmp/transcribe-frames-aula/ --prompt "O que aparece na tela?"

  # batch via JSONL
  python3 vision_analyze_frames.py /tmp/frames/ --batch jobs.jsonl

jobs.jsonl: 1 linha por frame — {"frame": "/path/to/frame.jpg", "prompt": "..."}
"""
from __future__ import annotations
import argparse, json, os, sys, time
from pathlib import Path

import torch
from PIL import Image

DEFAULT_MODEL = os.environ.get("VISION_MODEL", "Qwen/Qwen2-VL-2B-Instruct")


def load_model():
    from transformers import Qwen2VLForConditionalGeneration, AutoProcessor
    t0 = time.time()
    dtype = torch.bfloat16 if torch.cuda.is_available() else torch.float32
    model = Qwen2VLForConditionalGeneration.from_pretrained(
        DEFAULT_MODEL,
        torch_dtype=dtype,
        device_map="cuda" if torch.cuda.is_available() else "cpu",
    )
    processor = AutoProcessor.from_pretrained(DEFAULT_MODEL)
    print(f"loaded {DEFAULT_MODEL} in {time.time()-t0:.1f}s, vram_peak={torch.cuda.max_memory_allocated()/1e9:.1f}GB", file=sys.stderr)
    return model, processor


def analyze(model, processor, image_path: str, prompt: str = "Descreva o que aparece na tela com foco em elementos de UI e contexto técnico.") -> str:
    img = Image.open(image_path).convert("RGB")
    messages = [{
        "role": "user",
        "content": [
            {"type": "image"},
            {"type": "text", "text": prompt},
        ],
    }]
    text = processor.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
    inputs = processor(text=[text], images=[img], padding=True, return_tensors="pt").to(model.device)
    t0 = time.time()
    with torch.inference_mode():
        out = model.generate(**inputs, max_new_tokens=512, do_sample=False)
    gen = out[0][inputs.input_ids.shape[1]:]
    text = processor.decode(gen, skip_special_tokens=True)
    elapsed = time.time() - t0
    print(f"  {os.path.basename(image_path):40s}  {elapsed:5.1f}s  {len(text)} chars", file=sys.stderr)
    return text


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("frames_dir", help="pasta com os frames .jpg/.png extraídos")
    ap.add_argument("--prompt", default="Descreva o que aparece na tela com foco em elementos de UI, ações em andamento, plataforma (web/mobile/desktop) e contexto técnico de negócio.")
    ap.add_argument("--out", default=None, help="arquivo de saída JSON (default: stdout)")
    ap.add_argument("--batch", help="JSONL com {frame, prompt} por linha")
    ap.add_argument("--pattern", default="*.jpg", help="glob de frames (default: *.jpg)")
    args = ap.parse_args()

    if not torch.cuda.is_available():
        print("⚠ sem CUDA — geração será muito lenta (15-30s por frame)", file=sys.stderr)

    model, processor = load_model()

    results = []
    if args.batch:
        with open(args.batch) as f:
            for ln, line in enumerate(f, 1):
                line = line.strip()
                if not line or line.startswith("#"): continue
                job = json.loads(line)
                desc = analyze(model, processor, job["frame"], job.get("prompt", args.prompt))
                results.append({"frame": job["frame"], "description": desc})
    else:
        frames_dir = Path(args.frames_dir)
        frames = sorted(frames_dir.glob(args.pattern))
        if not frames:
            die(f"nenhum frame em {frames_dir} com padrão {args.pattern}")
        for frame_path in frames:
            desc = analyze(model, processor, str(frame_path), args.prompt)
            results.append({"frame": str(frame_path), "description": desc})

    out = json.dumps(results, ensure_ascii=False, indent=2)
    if args.out:
        Path(args.out).write_text(out, encoding="utf-8")
        print(f"✓ {len(results)} frames analisados → {args.out}", file=sys.stderr)
    else:
        print(out)


if __name__ == "__main__":
    main()
