import time
import json
from app.services import claim_service

improved_prompt_template = """You extract check-worthy factual claims from a spoken transcript. The transcript may be in English, Hindi (Devanagari), or mixed. Do not translate. Preserve the original language of the claim.

CRITICAL RULES:
- Extract ONLY concrete, independently verifiable factual assertions about the world (events, statistics, policies, dates, actions, history, science, economics).
- Each claim MUST be a complete, self-contained declarative sentence stating a specific fact (e.g., "Boris Johnson traveled to Ukraine by train during the war").
- DO NOT extract broadcast logistics, show announcements, anchor transitions, or commercial break notices (e.g., "we will take a short break", "welcome back", "this is the context", "thanks for joining us", "reporting live from").
- DO NOT extract personal opinions, speculations, feelings, or intentions (e.g., "I think Putin is sensing weakness", "we want to see", "I believe").
- DO NOT extract incomplete fragments, rhetorical questions, or conversational filler.
- If the transcript contains NO verifiable real-world factual claims, you MUST return {{"claims": []}}.

Return ONLY valid JSON:
{{
  "claims": [
    {{
      "statement": "Complete self-contained factual assertion in the original language",
      "timestamp": "MM:SS"
    }}
  ]
}}

Transcript:
{transcript}
"""

test_cases = [
    # 1. Real speech from our session: Boris Johnson travelling
    "[01:10] close to the time that the former British Prime Minister Boris Johnson was traveling on a train there at same time.",
    # 2. Sanctions and 1 trillion
    "[02:50] ram sanctioning Russia in Iran act, which includes extremely strong and tough sanctions that are the top purchasers of Russian oil and gas. That like I said, have put $1 trillion into Russian revenue since 2022.",
    # 3. Filler / anchor talk from our session (Claim 5 & 6)
    "[03:14] Because, Merseki thank you so much for being with us and discussing what's happening between train in the long running war between. We'll take short break back and just a moment around The World, Anna Cross UK this is the context",
    # 4. Hindi factual claim
    "[00:30] भारत सरकार ने नई दिल्ली में 50000 करोड़ रुपये की बुनियादी ढांचा परियोजना की घोषणा की है।",
    # 5. Hindi filler
    "[00:05] नमस्कार, आप देख रहे हैं विशेष बुलेटिन। आज हम इस मुद्दे पर चर्चा करेंगे। आपका स्वागत है।"
]

for i, transcript in enumerate(test_cases):
    prompt = improved_prompt_template.format(transcript=transcript)
    t0 = time.perf_counter()
    raw = claim_service.query_ollama(prompt, format_json=True)
    elapsed = time.perf_counter() - t0
    parsed = json.loads(raw)
    claims = parsed.get("claims", [])
    print(f"\n--- Test Case {i+1} ({elapsed:.2f}s) ---")
    print("Input:", transcript)
    print("Claims count:", len(claims))
    for c in claims:
        print(" ->", c.get("statement"))
