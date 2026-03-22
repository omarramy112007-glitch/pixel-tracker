# processing/personalization.py

import asyncio
import json
import torch
from transformers import AutoModelForCausalLM, AutoTokenizer
from core.cache import get_cache, set_cache
from core.retry import retry
from core.performance import timer

MODEL_NAME = "tiiuae/falcon-7b-instruct"
tokenizer = AutoTokenizer.from_pretrained(MODEL_NAME)
model = AutoModelForCausalLM.from_pretrained(
    MODEL_NAME,
    torch_dtype=torch.float16,
    device_map="auto"
)

@timer("Generate Personalization")
@retry
async def generate_personalization(lead: dict) -> dict:
    if not lead:
        return lead

    cache_key = f"personalization:{lead.get('email') or lead.get('company')}"
    cached = get_cache(cache_key)
    if cached:
        return cached

    prompt = f"""
You are a marketing AI assistant.
Respond ONLY in valid JSON.

Lead info:
Company: {lead.get("company")}
Website: {lead.get("website")}
Tech detected: {lead.get("tech_detected", [])}
Pain signals: {lead.get("pain_signals", [])}
Title: {lead.get("title")}

Return JSON with keys:
first_line
website_summary
pain_hook
dynamic_offer
"""

    loop = asyncio.get_event_loop()

    def run_model():
        inputs = tokenizer(prompt, return_tensors="pt").to(model.device)
        outputs = model.generate(
            **inputs,
            max_new_tokens=180,
            do_sample=True,
            temperature=0.6,
        )
        return tokenizer.decode(outputs[0], skip_special_tokens=True)

    text = await loop.run_in_executor(None, run_model)

    try:
        start = text.find("{")
        end = text.rfind("}") + 1
        ai_data = json.loads(text[start:end])
    except Exception:
        ai_data = {
            "first_line": "",
            "website_summary": "",
            "pain_hook": "",
            "dynamic_offer": ""
        }

    lead.update(ai_data)
    set_cache(cache_key, lead)
    return lead