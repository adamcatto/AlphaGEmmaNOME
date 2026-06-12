#!/usr/bin/env python
from __future__ import annotations

import os
import sys
from pathlib import Path

import torch
from datasets import Dataset
from peft import LoraConfig, prepare_model_for_kbit_training
from transformers import (
    AutoModelForCausalLM,
    AutoTokenizer,
    BitsAndBytesConfig,
    TrainingArguments,
)
from trl import DPOTrainer

MODEL_ID = "google/gemma-4-E4B-it"
DATA_PATH = Path("/opt/software/AlphaGEmmaNOME/services/agent_backend/data/dpo_preferences.jsonl")
OUTPUT_DIR = "/opt/software/AlphaGEmmaNOME/services/agent_backend/data/dpo_adapters"


def load_dpo_dataset() -> Dataset:
    if not DATA_PATH.exists():
        print(f"Error: Dataset {DATA_PATH} not found. Using dummy DPO dataset.")
        data = [{
            "prompt": "Silence track 0 at chr17:43125200-43125600",
            "chosen": "<thought>\nNeed to call optimize_edits.\n</thought>\nCalling optimize_edits with deletion mode.",
            "rejected": "I do not know how to do that or edit genome tracks."
        }]
    else:
        data = []
        with open(DATA_PATH, "r", encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    item = json.loads(line.strip())
                    data.append({
                        "prompt": item["prompt"],
                        "chosen": item["chosen"],
                        "rejected": item["rejected"],
                    })
    return Dataset.from_list(data)


def main():
    print(f"Starting QLoRA Direct Preference Optimization (DPO) for {MODEL_ID}...")
    dataset = load_dpo_dataset()

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

    # Reference model can be identical loaded in 4-bit with frozen weights
    ref_model = AutoModelForCausalLM.from_pretrained(
        MODEL_ID,
        quantization_config=bnb_config,
        device_map="auto",
        torch_dtype=torch.float16,
    )

    peft_config = LoraConfig(
        r=8,
        lora_alpha=16,
        target_modules=["q_proj", "o_proj", "k_proj", "v_proj", "gate_proj", "up_proj", "down_proj"],
        lora_dropout=0.05,
        bias="none",
        task_type="CAUSAL_LM",
    )

    training_args = TrainingArguments(
        output_dir=OUTPUT_DIR,
        per_device_train_batch_size=1,
        gradient_accumulation_steps=4,
        warmup_steps=10,
        max_steps=50,
        learning_rate=5e-5,
        fp16=True,
        logging_steps=1,
        save_strategy="no",
        report_to="none",
    )

    trainer = DPOTrainer(
        model=model,
        ref_model=ref_model,
        beta=0.1,
        train_dataset=dataset,
        tokenizer=tokenizer,
        args=training_args,
        peft_config=peft_config,
        max_prompt_length=512,
        max_length=1024,
    )

    trainer.train()
    print(f"DPO Training complete! Saving adapters to {OUTPUT_DIR}...")
    trainer.model.save_pretrained(OUTPUT_DIR)


if __name__ == "__main__":
    import json
    main()
