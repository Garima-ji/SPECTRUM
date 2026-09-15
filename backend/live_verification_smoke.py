"""Run real Hindi/English live-verification checks inside the backend container."""

import json
import threading

from app.services.verification_service import verify_single_claim


CLAIMS = [
    "\u092d\u093e\u0930\u0924 \u0915\u0940 \u0930\u093e\u091c\u0927\u093e\u0928\u0940 \u0928\u0908 \u0926\u093f\u0932\u094d\u0932\u0940 \u0939\u0948\u0964",
    "\u092d\u093e\u0930\u0924 \u0915\u093e \u0938\u0902\u0935\u093f\u0927\u093e\u0928 26 \u091c\u0928\u0935\u0930\u0940 1950 \u0915\u094b \u0932\u093e\u0917\u0942 \u0939\u0941\u0906 \u0925\u093e\u0964",
    "\u091a\u0902\u0926\u094d\u0930\u092f\u093e\u0928-3 \u0928\u0947 2023 \u092e\u0947\u0902 \u091a\u0902\u0926\u094d\u0930\u092e\u093e \u0915\u0940 \u0938\u0924\u0939 \u092a\u0930 \u0938\u092b\u0932 \u0938\u0949\u092b\u094d\u091f \u0932\u0948\u0902\u0921\u093f\u0902\u0917 \u0915\u0940 \u0925\u0940\u0964",
    "India has 28 states.",
]


if __name__ == "__main__":
    semaphore = threading.Semaphore(1)
    for claim in CLAIMS:
        result = verify_single_claim(
            claim,
            generate_query=False,
            live_mode=True,
            qwen_semaphore=semaphore,
            retrieval_timeout_seconds=15,
            qwen_timeout_seconds=120,
        )
        print(json.dumps({"claim": claim, "result": result}, ensure_ascii=False), flush=True)
