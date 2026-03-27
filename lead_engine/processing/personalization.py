# lead_engine/processing/personalization.py

import asyncio
import json
from transformers import AutoModelForCausalLM, AutoTokenizer
from torch import float16

from lead_engine.core.cache import get_cache, set_cache
from lead_engine.core.retry import retry
from lead_engine.core.performance import timer

MODEL_NAME = "tiiuae/falcon-7b-instruct"

# 🔥 Lazy-loaded globals
model = None
tokenizer = None


# -----------------------------
# 🔥 LOAD MODEL ONLY WHEN NEEDED
# -----------------------------
def load_model():
    global model, tokenizer

    if model is None:
        print("⚠️ Loading AI model... (first time only)")

        tokenizer = AutoTokenizer.from_pretrained(MODEL_NAME)

        model = AutoModelForCausalLM.from_pretrained(
            MODEL_NAME,
            dtype=float16,   # ✅ FIXED
            device_map="auto"
        )


# -----------------------------
# 🤖 PERSONALIZATION
# -----------------------------
@timer("Generate Personalization")
@retry
async def generate_personalization(lead: dict) -> dict:
    if not lead:
        return lead

    cache_key = f"personalization:{lead.get('email') or lead.get('company')}"

    cached = get_cache(cache_key)
    if cached:
        return cached

    # 🔥 LOAD MODEL ONLY HERE
    load_model()

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
            max_new_tokens=150,
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