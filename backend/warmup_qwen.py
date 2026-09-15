"""Warm up Qwen by sending a trivial request."""
from app.services.claim_service import query_ollama
print("Warming up Qwen...", flush=True)
result = query_ollama('Respond with JSON: {"status": "ok"}', format_json=True, timeout_seconds=300)
print(f"Warmup done: {result}", flush=True)
