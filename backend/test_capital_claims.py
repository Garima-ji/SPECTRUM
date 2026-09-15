"""Quick end-to-end test with extended timeout for CPU-only Ollama."""
import json
import threading
from app.services.verification_service import verify_single_claim

CLAIMS = [
    "India's capital is New Delhi.",
    "\u092d\u093e\u0930\u0924 \u0915\u0940 \u0930\u093e\u091c\u0927\u093e\u0928\u0940 \u0928\u0908 \u0926\u093f\u0932\u094d\u0932\u0940 \u0939\u0948\u0964",
]

if __name__ == "__main__":
    sem = threading.Semaphore(1)
    for claim in CLAIMS:
        print(f"=== TESTING: {claim} ===", flush=True)
        result = verify_single_claim(
            claim,
            generate_query=False,
            live_mode=True,
            qwen_semaphore=sem,
            retrieval_timeout_seconds=20,
            qwen_timeout_seconds=300,
        )
        print(
            json.dumps(
                {
                    "claim": claim,
                    "query": result.get("query"),
                    "verdict": result.get("verdict"),
                    "confidence": result.get("confidence"),
                    "sources": [
                        (s.get("title", "")[:50], s.get("url", "")[:80])
                        for s in result.get("sources", [])
                    ],
                    "reasoning": (result.get("reasoning") or "")[:300],
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
        print(flush=True)
