import time
from app.services import claim_service

test_transcripts = [
    # 1. Real speech from our session: Boris Johnson travelling
    "The attack happened close to the time that the former British Prime Minister Boris Johnson was traveling on a train there at the same time.",
    # 2. Sanctions and 1 trillion
    "We need the Sanctioning Russia and Iran Act, which includes extremely strong and tough sanctions on countries that are the top purchasers of Russian oil and gas. That, like I said, have put $1 trillion into Russian revenue since 2022.",
    # 3. Filler / anchor talk from our session
    "Because thank you so much for being with us and discussing what is happening. We will take a short break and back in just a moment around the world.",
    # 4. Hindi factual claim
    "भारत सरकार ने नई दिल्ली में 50000 करोड़ रुपये की बुनियादी ढांचा परियोजना की घोषणा की है।",
    # 5. Hindi filler
    "नमस्कार, आप देख रहे हैं विशेष बुलेटिन। आज हम इस मुद्दे पर चर्चा करेंगे। आपका स्वागत है।"
]

for i, text in enumerate(test_transcripts):
    t0 = time.perf_counter()
    claims = claim_service.extract_claims(text)
    elapsed = time.perf_counter() - t0
    print(f"\n--- Test {i+1} (in {elapsed:.2f}s) ---")
    print("Input:", text)
    print("Extracted claims:", claims)
