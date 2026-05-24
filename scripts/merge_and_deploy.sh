#!/usr/bin/env bash
set -e

echo "=== OmniGemmaNome Merge and Deploy Pipeline ==="

# Define paths
BASE_MODEL="google/gemma-4-E4B-it"
ADAPTER_DIR="/opt/software/OmniGemmaNome/services/agent_backend/data/sft_adapters"
MERGED_DIR="/opt/software/OmniGemmaNome/services/agent_backend/data/merged_model"
MODELFILE_PATH="/opt/software/OmniGemmaNome/services/agent_backend/data/Modelfile"

# 1. Merge adapters with base model weights
echo "1. Merging fine-tuned PEFT adapters back into base model..."
python3 -c "
import torch
from transformers import AutoModelForCausalLM, AutoTokenizer
from peft import PeftModel

print('Loading base model...')
base = AutoModelForCausalLM.from_pretrained(
    '${BASE_MODEL}',
    torch_dtype=torch.float16,
    device_map='cpu'
)
tokenizer = AutoTokenizer.from_pretrained('${BASE_MODEL}')

print('Loading adapters...')
model = PeftModel.from_pretrained(base, '${ADAPTER_DIR}')

print('Merging weights...')
merged = model.merge_and_unload()

print('Saving merged model weights to ${MERGED_DIR}...')
merged.save_pretrained('${MERGED_DIR}')
tokenizer.save_pretrained('${MERGED_DIR}')
print('Weights merged and saved successfully!')
"

# 2. Write Ollama Modelfile
echo "2. Formatting Ollama Modelfile..."
cat <<EOF > "${MODELFILE_PATH}"
# Modelfile for fine-tuned OmniGemmaNome Agent
FROM ${MERGED_DIR}

# Set system parameters
PARAMETER num_ctx 4096
PARAMETER stop <end_of_turn>
PARAMETER stop <eos>

# Inject Agent System Prompt Template
SYSTEM """You are OmniGemmaNome, an expert genomic editing assistant. You help scientists plan reference sequence edits (SNVs, deletions, insertions, CTCF/SP1 motif ablation) using AlphaGenome simulation tools. Always think step-by-step inside <thought> tags before responding."""
EOF

# 3. Import and deploy into local Ollama instance
echo "3. Importing merged model into local Ollama service..."
ollama create gemma-4-omnigenomanome -f "${MODELFILE_PATH}"

echo "4. Fine-tuned model gemma-4-omnigenomanome is successfully deployed and active!"
ollama list
