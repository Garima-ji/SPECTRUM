"""
Test script to run inside the backend container.
Tests: Ollama connectivity, claim extraction prompt format, and the full claim extraction pipeline.
"""
import json
import sys
import requests

OLLAMA_URL = "http://ollama:11434/api/generate"
MODEL = "qwen2.5:latest"

print("=" * 60)
print("TEST 1: Basic Ollama connectivity")
print("=" * 60)
try:
    r = requests.post(OLLAMA_URL, json={
        "model": MODEL,
        "prompt": "Return JSON with a field called status set to the string ok",
        "format": "json",
        "stream": False,
        "options": {"temperature": 0.1}
    }, timeout=60)
    r.raise_for_status()
    d = r.json()
    response = d.get("response", "")
    print(f"Status: {r.status_code}")
    print(f"Response: {response[:300]}")
    parsed = json.loads(response)
    print(f"Parsed JSON: {parsed}")
    print("PASS: Ollama reachable and returns valid JSON object")
except Exception as e:
    print(f"FAIL: {e}")
    sys.exit(1)

print()
print("=" * 60)
print("TEST 2: Claim extraction prompt format")
print("=" * 60)
CLAIM_PROMPT = """You extract only check-worthy factual claims from a Hindi, English, or Hindi-English transcript. Do not translate the transcript. Preserve Hindi in Devanagari and preserve code-switched English words naturally.

Extract a statement only when it is externally verifiable, including statistics, dates, government announcements, historical or scientific facts, political claims, economic claims, and health claims. Exclude opinions, predictions, questions, slogans, preferences, rhetorical statements, and vague assertions.

Use the timestamp shown in square brackets for the start of the statement. If no speaker is explicitly named, set speaker to null. Return an empty claims array when no factual claims are present.

Return only valid JSON with this exact schema:
{
  "claims": [
    {
      "statement": "A concise factual statement in the original language",
      "speaker": "Speaker name or null",
      "timestamp": "MM:SS or null"
    }
  ]
}

Transcript:
[00:00] \u092d\u093e\u0930\u0924 \u0915\u0940 \u091c\u0928\u0938\u0902\u0916\u094d\u092f\u093e 140 \u0915\u0930\u094b\u0921\u093c \u0939\u0948\u0964
[00:05] \u0938\u0930\u0915\u093e\u0930 \u0928\u0947 \u0905\u0917\u0932\u0947 \u0938\u093e\u0932 GDP growth 7% \u0915\u093e \u0932\u0915\u094d\u0937\u094d\u092f \u0930\u0916\u093e \u0939\u0948\u0964
"""

try:
    r = requests.post(OLLAMA_URL, json={
        "model": MODEL,
        "prompt": CLAIM_PROMPT,
        "format": "json",
        "stream": False,
        "options": {"temperature": 0.1}
    }, timeout=120)
    r.raise_for_status()
    d = r.json()
    response = d.get("response", "")
    print(f"Raw response ({len(response)} chars):")
    print(response[:500])
    parsed = json.loads(response)
    print(f"\nParsed type: {type(parsed)}")
    claims = parsed.get("claims", []) if isinstance(parsed, dict) else parsed
    print(f"Claims found: {len(claims)}")
    for i, c in enumerate(claims):
        print(f"  Claim {i+1}: {c}")
    print("PASS: Claim extraction format works correctly")
except Exception as e:
    print(f"FAIL: {e}")
    import traceback
    traceback.print_exc()

print()
print("=" * 60)
print("TEST 3: Verify prompts file is updated")
print("=" * 60)
try:
    with open("/app/app/prompts/claim_prompt.txt", "r", encoding="utf-8") as f:
        content = f.read()
    if '"claims"' in content and '[' not in content[:content.find('"claims"')]:
        print("PASS: claim_prompt.txt correctly uses {'claims':[...]} format")
    else:
        print("Content of claim_prompt.txt:")
        print(content)
except Exception as e:
    print(f"FAIL reading prompt: {e}")

print()
print("All tests done.")
