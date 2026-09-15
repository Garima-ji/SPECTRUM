"""
Smoke test — run verify_single_claim() for each of the 4 test claims inside Docker.
Prints [PASS] if verdict != UNVERIFIABLE or sources > 0, [FAIL] otherwise.
"""
import sys
import os

# Minimal env so imports work inside the container
os.environ.setdefault("DATABASE_URL", "postgresql://postgres:postgres@db:5432/spectrum")
os.environ.setdefault("OLLAMA_URL", "http://ollama:11434")
os.environ.setdefault("OLLAMA_MODEL", "qwen2.5:latest")
os.environ.setdefault("OLLAMA_TIMEOUT_SECONDS", "300")

from app.services.verification_service import verify_single_claim

CLAIMS = [
    "India has 28 states.",
    "The Taj Mahal is located in Mumbai.",
    "The earth has two moons.",
    "The sun revolves around the earth.",
]

overall_pass = True
for claim in CLAIMS:
    print(f"\n{'='*60}")
    print(f"CLAIM: {claim}")
    try:
        result = verify_single_claim(claim)
        verdict = result["verdict"]
        confidence = result["confidence"]
        sources = result["sources"]
        reasoning = result["reasoning"][:150]
        print(f"  VERDICT    : {verdict}")
        print(f"  CONFIDENCE : {confidence:.3f}")
        print(f"  SOURCES    : {len(sources)}")
        print(f"  REASONING  : {reasoning}...")
        if sources:
            for i, s in enumerate(sources[:3], 1):
                print(f"    Source[{i}]: {s.get('title','')[:60]} | {s.get('url','')[:60]}")
        # PASS if we got any sources OR verdict is not UNVERIFIABLE
        if len(sources) > 0 or verdict != "UNVERIFIABLE":
            print(f"  [PASS] evidence-backed result")
        else:
            print(f"  [FAIL] no sources and UNVERIFIABLE — still broken")
            overall_pass = False
    except Exception as exc:
        print(f"  [ERROR] {exc}")
        import traceback; traceback.print_exc()
        overall_pass = False

print(f"\n{'='*60}")
print("OVERALL:", "ALL PASS" if overall_pass else "SOME FAILURES — CHECK LOGS")
sys.exit(0 if overall_pass else 1)
