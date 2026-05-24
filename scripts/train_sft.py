#!/usr/bin/env python
from __future__ import annotations

import os
import sys
from pathlib import Path

import torch
from datasets import Dataset
from peft import LoraConfig, get_peft_model, prepare_model_for_kbit_training
from transformers import (
    AutoModelForCausalLM,
    AutoTokenizer,
    BitsAndBytesConfig,
    TrainingArguments,
)
from trl import SFTTrainer

# Base local/huggingface model chosen by user
MODEL_ID = "google/gemma-4-E4B-it"
DATA_PATH = Path("/opt/software/OmniGemmaNome/services/agent_backend/data/sft_corrections.jsonl")
OUTPUT_DIR = "/opt/software/OmniGemmaNome/services/agent_backend/data/sft_adapters"


def load_sft_dataset() -> Dataset:
    if not DATA_PATH.exists():
        print(f"Error: Dataset {DATA_PATH} not found. Using dummy SFT dataset.")
        data = [{
            "text": "<bos><start_of_turn>user\nSilence track 0 at chr17:43125200-43125600<end_of_turn>\n"
                    "<start_of_turn>model\n<thought>\nNeed to call optimize_edits.\n</thought>\n"
                    "Calling optimize_edits with deletion mode.<end_of_turn><eos>"
        }]
    else:
        data = []
        with open(DATA_PATH, "r", encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    item = json.loads(line.strip())
                    # Format as standard Gemma turn sequences
                    formatted_text = (
                        f"<bos><start_of_turn>user\n{item['prompt']}<end_of_turn>\n"
                        f"<start_of_turn>model\n{item['corrected']}<end_of_turn><eos>"
                    )
                    data.append({"text": formatted_text})
    return Dataset.from_list(data)


def main():
    print(f"Starting QLoRA Supervised Fine-Tuning for {MODEL_ID}...")
    dataset = load_sft_dataset()

    # 4-bit Quantization Config for QLoRA
    bnb_config = BitsAndBytesConfig(
        load_in_4bit=True,
        bnb_4bit_quant_type="nf4",
        bnb_4bit_compute_dtype=torch.float16,
        bnb_4bit_use_double_quant=True,
    )

    tokenizer = AutoTokenizer.from_pretrained(MODEL_ID)
    tokenizer.pad_token = tokenizer.eos_token

    model = AutoModelForCausalLM.from_pretrained(
        MODEL_ID,
        quantization_config=bnb_config,
        device_map="auto",
        torch_dtype=torch.float16,
    )

    model = prepare_model_for_kbit_training(model)

    # LoRA Target Modules for Gemma
    peft_config = LoraConfig(
        r=8,
        lora_alpha=16,
        target_modules=["q_proj", "o_proj", "k_proj", "v_proj", "gate_proj", "up_proj", "down_proj"],
        lora_dropout=0.05,
        bias="none",
        task_type="CAUSAL_LM",
    )

    model = get_peft_model(model, peft_config)

    training_args = TrainingArguments(
        output_dir=OUTPUT_DIR,
        per_device_train_batch_size=1,
        gradient_accumulation_steps=4,
        warmup_steps=10,
        max_steps=50,
        learning_rate=2e-4,
        fp16=True,
        logging_steps=1,
        save_strategy="no",
        report_to="none",
    )

    trainer = SFTTrainer(
        model=model,
        train_dataset=dataset,
        dataset_text_field="text",
        max_seq_length=1024,
        tokenizer=tokenizer,
        args=training_args,
    )

    trainer.train()
    print(f"SFT Training complete! Saving adapters to {OUTPUT_DIR}...")
    trainer.model.save_pretrained(OUTPUT_DIR)


if __name__ == "__main__":
    import json
    main()
