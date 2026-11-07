#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Merge LoRA adapter into base model and export for Ollama.

LoRA 어댑터를 베이스 모델에 병합해 SafeTensors 로 저장한다(GGUF 변환은 아래 llama.cpp 명령으로 별도 수행).

Steps:
  1. Load base model (FP16)
  2. Load LoRA adapter
  3. Merge weights
  4. Save merged model (SafeTensors)

After running this script, use llama.cpp to convert to GGUF:
  python convert_hf_to_gguf.py ./output/argus-metadata-7b-merged --outfile argus-metadata-7b-f16.gguf --outtype f16
  ./llama-quantize argus-metadata-7b-f16.gguf argus-metadata-7b-Q4_K_M.gguf Q4_K_M

Usage:
    python merge_and_convert.py
    python merge_and_convert.py --base-model Qwen/Qwen2.5-7B-Instruct --lora-dir ./output/argus-metadata-7b-lora
"""

import argparse
import time

import torch
from peft import PeftModel
from transformers import AutoModelForCausalLM, AutoTokenizer


def main():
    """베이스 모델(FP16, CPU) 로드 → LoRA 어댑터 로드 → 병합 → 병합 모델·토크나이저 저장."""
    parser = argparse.ArgumentParser(description="Merge LoRA adapter into base model")
    parser.add_argument(
        "--base-model", default="Qwen/Qwen2.5-7B-Instruct",
        help="Base model name or path",
    )
    parser.add_argument(
        "--lora-dir", default="./output/argus-metadata-7b-lora",
        help="LoRA adapter directory",
    )
    parser.add_argument(
        "--output-dir", default="./output/argus-metadata-7b-merged",
        help="Output directory for merged model",
    )
    args = parser.parse_args()

    print("=" * 60)
    print("  LoRA Merge & Export")
    print("=" * 60)
    print(f"  Base:   {args.base_model}")
    print(f"  LoRA:   {args.lora_dir}")
    print(f"  Output: {args.output_dir}")

    start = time.time()

    # 1. Load base model
    print("\n[1/4] Loading base model (FP16)...")
    base_model = AutoModelForCausalLM.from_pretrained(
        args.base_model,
        dtype=torch.float16,
        device_map="cpu",
    )
    tokenizer = AutoTokenizer.from_pretrained(args.base_model)

    # 2. Load LoRA adapter
    print("[2/4] Loading LoRA adapter...")
    model = PeftModel.from_pretrained(base_model, args.lora_dir)

    # 3. Merge
    print("[3/4] Merging weights...")
    model = model.merge_and_unload()

    # 4. Save
    print("[4/4] Saving merged model...")
    model.save_pretrained(args.output_dir, safe_serialization=True)
    tokenizer.save_pretrained(args.output_dir)

    elapsed = time.time() - start
    print(f"\n  Merged model saved to: {args.output_dir}")
    print(f"  Elapsed: {elapsed:.1f}s")
    print("=" * 60)


if __name__ == "__main__":
    main()
