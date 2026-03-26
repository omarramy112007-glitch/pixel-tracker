# processing/llm.py

from transformers import AutoModelForCausalLM, AutoTokenizer
import torch
from lead_engine.core.performance import timer

print("🚀 Loading local LLM, please wait...")

@timer("Load Falcon-7B LLM")
def load_model():
    tokenizer = AutoTokenizer.from_pretrained("tiiuae/falcon-7b-instruct")
    model = AutoModelForCausalLM.from_pretrained(
        "tiiuae/falcon-7b-instruct",
        torch_dtype=torch.float16,
        device_map="auto"
    )
    return tokenizer, model

tokenizer, model = load_model()
print("✅ Local LLM ready")