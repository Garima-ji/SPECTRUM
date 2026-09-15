import sys
import os
import logging

# Ensure app is in path
sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))

from app.services import whisper_service, claim_service, verification_service, retrieval_service, ranking_service

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("spectrum")

TEST_CLAIMS = [
    "भारत की राजधानी नई दिल्ली है।",
    "भारत का संविधान 26 जनवरी 1950 को लागू हुआ था।",
    "चंद्रयान-3 ने 2023 में चंद्रमा की सतह पर सफल सॉफ्ट लैंडिंग की थी。",
    "The capital of France is Paris."
]

def run_diagnostic():
    print("\n" + "="*80)
    print("      SPECTRUM HINDI LIVE PIPELINE DIAGNOSTIC LOG")
    print("="*80)

    for i, claim_text in enumerate(TEST_CLAIMS, 1):
        print(f"\n--- TEST #{i}: {claim_text} ---")
        
        # 1. RAW WHISPER / INPUT TEXT
        print(f"RAW WHISPER OUTPUT: {claim_text}")
        
        # 2. CLEAN TRANSCRIPT
        clean_transcript = claim_text.strip()
        print(f"CLEAN TRANSCRIPT: {clean_transcript}")
        
        # 3. CLAIM
        print(f"CLAIM: {clean_transcript}")
        
        # 4. LANGUAGE
        has_hindi = verification_service._contains_devanagari(clean_transcript)
        lang = "hi" if has_hindi else "en"
        print(f"LANGUAGE: {lang}")
        
        # 5. SEARCH QUERY & VERIFICATION
        result = verification_service.verify_single_claim(
            clean_transcript,
            generate_query=False,
            live_mode=True
        )
        
        search_query = result.get("query", "")
        print(f"SEARCH QUERY: {search_query}")
        
        # 6. CANDIDATE & ACCEPTED SOURCES
        sources = result.get("sources", [])
        print(f"CANDIDATE SOURCES: {len(sources)}")
        for idx, s in enumerate(sources, 1):
            print(f"  Source [{idx}]: {s.get('title')} -> {s.get('url')}")
            
        accepted_evidence = [s.get('snippet')[:100] + '...' for s in sources]
        print(f"ACCEPTED EVIDENCE: {accepted_evidence}")
        
        final_url = sources[0].get('url') if sources else "None"
        print(f"FINAL URL: {final_url}")
        
        # 9 & 10. VERDICT & REASONING
        print(f"QWEN INPUT: Claim: '{clean_transcript}' with {len(sources)} sources")
        print(f"QWEN OUTPUT / REASONING: {result.get('reasoning')}")
        print(f"FINAL VERDICT: {result.get('verdict')} (Confidence: {result.get('confidence')}%)")
        print("-" * 80)

if __name__ == "__main__":
    run_diagnostic()
