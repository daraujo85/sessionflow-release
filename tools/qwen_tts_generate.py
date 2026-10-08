#!/usr/bin/env python3
"""
Qwen3-TTS 0.1.1 — wrapper de produção pra Garagem.
GPU T4 do Colab. Carrega Qwen3-TTS-12Hz-1.7B-CustomVoice (speaker Vivian) ou Base (voice clone).
Cache em /root/.cache/huggingface. Idempotente.

Uso:
  python3 qwen_tts_generate.py --text "Olá mundo" --lang portuguese --out /tmp/ola.wav
  python3 qwen_tts_generate.py --text "Hello" --lang english --speaker Vivian --out /tmp/hi.wav
  python3 qwen_tts_generate.py --batch jobs.jsonl --out-dir /tmp/qwen-batch/
  python3 qwen_tts_generate.py --ref ref.wav --ref-text "sample" --text "cloned text" --out /tmp/clone.wav

jobs.jsonl: 1 linha por job — {"text":..., "lang":..., "speaker":..., "out":...}
"""
from __future__ import annotations
import argparse, json, os, sys, time
from pathlib import Path

import numpy as np
import scipy.io.wavfile as wavfile
import torch

QWEN_MODEL = os.environ.get("QWEN_TTS_MODEL", "Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice")
DEFAULT_SPEAKER = os.environ.get("QWEN_TTS_SPEAKER", "Vivian")


def load_model():
    """Carrega modelo. CustomVoice (preset speakers) ou Base (voice clone)."""
    from qwen_tts import Qwen3TTSModel
    t0 = time.time()
    m = Qwen3TTSModel.from_pretrained(
        QWEN_MODEL,
        device_map="cuda:0" if torch.cuda.is_available() else "cpu",
        dtype=torch.bfloat16 if torch.cuda.is_available() else torch.float32,
    )
    print(f"loaded {QWEN_MODEL} in {time.time()-t0:.1f}s", file=sys.stderr)
    return m


def synth(model, text: str, lang: str = "auto", speaker: str | None = None,
          out: str = "/tmp/tts.wav", ref_audio: str | None = None,
          ref_text: str | None = None) -> str:
    if speaker is None:
        speaker = DEFAULT_SPEAKER
    t0 = time.time()
    if ref_audio:
        if ref_text is None:
            ref_text = open(ref_audio).read() if ref_audio.endswith(".txt") else ""
        wavs, sr = model.generate_voice_clone(
            text=text, language=lang, ref_audio=ref_audio, ref_text=ref_text
        )
    else:
        wavs, sr = model.generate_custom_voice(text=text, language=lang, speaker=speaker)
    dur = time.time() - t0
    wav = np.concatenate([np.asarray(w, dtype=np.float32) for w in wavs])
    wav_int = (wav * 32767).clip(-32768, 32767).astype(np.int16)
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    wavfile.write(out, sr, wav_int)
    print(f"{lang:12s} text_len={len(text):4d}  gen={dur:5.1f}s  dur={wav.shape[0]/sr:5.2f}s  size={Path(out).stat().st_size:>7}  {out}", file=sys.stderr)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--text")
    ap.add_argument("--lang", default="portuguese")
    ap.add_argument("--speaker", default=DEFAULT_SPEAKER)
    ap.add_argument("--out", default="/tmp/tts.wav")
    ap.add_argument("--ref")
    ap.add_argument("--ref-text")
    ap.add_argument("--batch", help="JSONL com 1 job por linha")
    ap.add_argument("--out-dir", default="/tmp/qwen-batch")
    args = ap.parse_args()

    if not torch.cuda.is_available():
        print("⚠ sem CUDA — geração será muito lenta", file=sys.stderr)

    model = load_model()
    print(f"speakers: {model.get_supported_speakers()}", file=sys.stderr)
    print(f"languages: {model.get_supported_languages()}", file=sys.stderr)

    if args.batch:
        Path(args.out_dir).mkdir(parents=True, exist_ok=True)
        with open(args.batch) as f:
            for ln, line in enumerate(f, 1):
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                job = json.loads(line)
                out = job.pop("out", None) or f"{args.out_dir}/{job.get('lang','out')}-{ln:03d}.wav"
                synth(model, out=out, **job)
    else:
        if not args.text:
            ap.error("--text or --batch required")
        synth(model, text=args.text, lang=args.lang, speaker=args.speaker,
              out=args.out, ref_audio=args.ref, ref_text=args.ref_text)


if __name__ == "__main__":
    main()
