import sys
import time

print("=== SPECTRUM CLAIM EXTRACTION PIPELINE VALIDATION ===")

# 1. Test is_checkworthy filter
from app.services.claim_service import is_checkworthy, extract_claims
from app.api.live import _is_trailing_continuation, _group_should_flush

print("\n--- 1. Testing Checkworthiness Heuristic ---")
cases = [
    # True claims (must be accepted)
    ("The attack occurred near when the former British Prime Minister Boris Johnson was traveling by train.", True),
    ("India won the ICC Men T20 World Cup in 2024 against South Africa.", True),
    ("The capital of France is Paris.", True),
    ("भारत की राजधानी नई दिल्ली है।", True),
    ("सरकार ने नई नीति लागू की है।", True),
    ("Russia launched 50 ballistic missiles targeting energy infrastructure in Kyiv.", True),
    ("The act includes sanctions on the top purchasers of Russian oil and gas.", True),
    # Non-claims / filler / fragments (must be rejected)
    ("train in the long running war between.", False),
    ("The World, Anna Cross UK this is the context", False),
    ("We will take a short break and back in just a moment around the world.", False),
    ("नमस्कार, आप देख रहे हैं विशेष बुलेटिन। आज हम इस मुद्दे पर चर्चा करेंगे। आपका स्वागत है।", False),
    ("hello how are you", False),
    ("okay thank you", False),
    ("I think that Putin is sensing Western weakness.", False),
    ("can actually strike down a lot of the ballistic missiles that Russia is using to attack Ukraine.", False),
    ("in the drones, tanks and armored vehicles that are being used to invade.", False),
    ("What do you think the White House is going to do?", False),
]

all_cw_ok = True
for text, expected in cases:
    res = is_checkworthy(text)
    status = "PASS" if res == expected else "FAIL"
    if res != expected:
        all_cw_ok = False
    print(f"[{status}] expected={expected:5} got={res:5} | {text[:60]}")

print("Checkworthiness Suite Passed:", all_cw_ok)
assert all_cw_ok, "Checkworthiness tests failed!"

print("\n--- 2. Testing Trailing Continuation Detection ---")
trailing_cases = [
    ("train in the long running war between", True),
    ("train in the long running war between.", True),
    ("weapons that are being used to", True),
    ("close to the time that", True),
    ("भारत सरकार ने", True),
    ("The capital of France is Paris.", False),
    ("Boris Johnson was traveling on a train.", False),
    ("भारत की राजधानी नई दिल्ली है।", False),
]

all_tr_ok = True
for text, expected in trailing_cases:
    res = _is_trailing_continuation(text)
    status = "PASS" if res == expected else "FAIL"
    if res != expected:
        all_tr_ok = False
    print(f"[{status}] expected={expected:5} got={res:5} | {text}")

print("Trailing Continuation Suite Passed:", all_tr_ok)
assert all_tr_ok, "Trailing continuation tests failed!"

print("\n--- 3. Testing Ollama Claim Extraction ---")
ollama_tests = [
    (
        "Boris Johnson train visit",
        "The attack happened close to the time that the former British Prime Minister Boris Johnson was traveling on a train there at same time.",
        True  # Expect 1 or more claims
    ),
    (
        "Commercial break & anchor banter (previously caused Claim 5 & 6)",
        "Because thank you so much for being with us and discussing what is happening. We will take a short break and back in just a moment around the world.",
        False  # Expect 0 claims
    ),
    (
        "Hindi factual claim",
        "भारत सरकार ने नई दिल्ली में 50000 करोड़ रुपये की बुनियादी ढांचा परियोजना की घोषणा की है।",
        True  # Expect 1 or more claims
    )
]

for name, text, expect_claims in ollama_tests:
    t0 = time.perf_counter()
    claims = extract_claims(text)
    elapsed = time.perf_counter() - t0
    print(f"\nTest '{name}' (in {elapsed:.2f}s):")
    print("Input:", text)
    print("Extracted claims count:", len(claims))
    for c in claims:
        print(f" -> '{c.get('claim_text')}'")
    if expect_claims:
        assert len(claims) > 0, f"Expected claims for '{name}', got 0"
    else:
        assert len(claims) == 0, f"Expected 0 claims for '{name}', got {len(claims)}"

print("\n=== ALL PIPELINE TESTS PASSED SUCCESSFULLY! ===")
