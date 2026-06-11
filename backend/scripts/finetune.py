#!/usr/bin/env python3
"""LoRA fine-tuning script for Argus Catalog metadata generation model.

Trains a LoRA adapter on Qwen2.5-7B-Instruct using catalog metadata
training data (ChatML format JSONL).

Usage:
    python finetune.py
    python finetune.py --base-model /path/to/model --epochs 5
"""

import argparse
import os

import torch
from datasets import load_dataset
from peft import LoraConfig, get_peft_model
from transformers import AutoModelForCausalLM, AutoTokenizer
from trl import SFTConfig, SFTTrainer


def main():
    parser = argparse.ArgumentParser(description="LoRA fine-tuning for catalog metadata")
    parser.add_argument(
        "--base-model", default="Qwen/Qwen2.5-7B-Instruct",
        help="Base model name or path",
    )
    parser.add_argument("--train-file", default="./training_data/train.jsonl")
    parser.add_argument("--eval-file", default="./training_data/eval.jsonl")
    parser.add_argument("--output-dir", default="./output/argus-metadata-7b-lora")
    parser.add_argument("--epochs", type=int, default=5)
    parser.add_argument("--batch-size", type=int, default=2)
    parser.add_argument("--grad-accum", type=int, default=8)
    parser.add_argument("--lr", type=float, default=2e-4)
    parser.add_argument("--max-seq-length", type=int, default=2048)
    parser.add_argument("--lora-r", type=int, default=16)
    parser.add_argument("--lora-alpha", type=int, default=32)
    args = parser.parse_args()

    print("=" * 60)
    print("  Argus Catalog Metadata Model - LoRA Fine-tuning")
    print("=" * 60)
    print(f"  Base model:  {args.base_model}")
    print(f"  Train file:  {args.train_file}")
    print(f"  Eval file:   {args.eval_file}")
    print(f"  Output:      {args.output_dir}")
    print(f"  Epochs:      {args.epochs}")
    print(f"  Batch:       {args.batch_size} x {args.grad_accum} = {args.batch_size * args.grad_accum}")
    print(f"  LoRA:        r={args.lora_r}, alpha={args.lora_alpha}")
    print(f"  GPU:         {torch.cuda.get_device_name(0) if torch.cuda.is_available() else 'CPU'}")
    if torch.cuda.is_available():
        mem = torch.cuda.get_device_properties(0).total_memory / 1024**3
        print(f"  VRAM:        {mem:.1f} GB")
    print("=" * 60)

    # ── 1. Load tokenizer ──
    print("\n[1/5] Loading tokenizer...")
    tokenizer = AutoTokenizer.from_pretrained(args.base_model)
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token
    tokenizer.padding_side = "right"

    # ── 2. Load model (FP16, no quantization needed with 120GB VRAM) ──
    print("[2/5] Loading model...")
    model = AutoModelForCausalLM.from_pretrained(
        args.base_model,
        dtype=torch.bfloat16,
        device_map="auto",
        attn_implementation="eager",
    )
    model.config.use_cache = False

    # ── 3. Apply LoRA ──
    print("[3/5] Applying LoRA adapter...")
    lora_config = LoraConfig(
        r=args.lora_r,
        lora_alpha=args.lora_alpha,
        target_modules=[
            "q_proj", "k_proj", "v_proj", "o_proj",
            "gate_proj", "up_proj", "down_proj",
        ],
        lora_dropout=0.05,
        bias="none",
        task_type="CAUSAL_LM",
    )
    model = get_peft_model(model, lora_config)
    model.print_trainable_parameters()

    # ── 4. Load data ──
    print("[4/5] Loading training data...")
    dataset = load_dataset("json", data_files={
        "train": args.train_file,
        "eval": args.eval_file,
    })
    print(f"  Train: {len(dataset['train'])} samples")
    print(f"  Eval:  {len(dataset['eval'])} samples")

    def format_chat(example):
        text = tokenizer.apply_chat_template(
            example["messages"],
            tokenize=False,
            add_generation_prompt=False,
        )
        return {"text": text}

    dataset = dataset.map(format_chat)

    # ── 5. Train ──
    print("[5/5] Starting training...")

    training_args = SFTConfig(
        output_dir=args.output_dir,
        num_train_epochs=args.epochs,
        per_device_train_batch_size=args.batch_size,
        gradient_accumulation_steps=args.grad_accum,
        learning_rate=args.lr,
        lr_scheduler_type="cosine",
        warmup_steps=5,
        weight_decay=0.01,
        bf16=True,
        logging_steps=5,
        eval_strategy="epoch",
        save_strategy="epoch",
        save_total_limit=2,
        load_best_model_at_end=True,
        metric_for_best_model="eval_loss",
        max_length=args.max_seq_length,
        dataset_text_field="text",
        report_to="none",
        seed=42,
    )

    trainer = SFTTrainer(
        model=model,
        args=training_args,
        train_dataset=dataset["train"],
        eval_dataset=dataset["eval"],
        processing_class=tokenizer,
    )

    train_result = trainer.train()

    print("\n" + "=" * 60)
    print("  Training Complete!")
    print("=" * 60)
    print(f"  Train loss:  {train_result.training_loss:.4f}")
    print(f"  Train time:  {train_result.metrics.get('train_runtime', 0):.1f}s")

    # Save
    trainer.save_model(args.output_dir)
    tokenizer.save_pretrained(args.output_dir)
    print(f"  Saved to:    {args.output_dir}")

    # Eval
    eval_results = trainer.evaluate()
    print(f"  Eval loss:   {eval_results.get('eval_loss', 'N/A')}")
    print("=" * 60)


if __name__ == "__main__":
    main()
