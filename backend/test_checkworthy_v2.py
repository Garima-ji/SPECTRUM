import re
import json

# Comprehensive News/Broadcast filler phrases to reject
BROADCAST_PATTERNS = [
    r"\b(take\s+(a\s+)?(short\s+)?break)\b",
    r"\b(back\s+in\s+just\s+a\s+moment)\b",
    r"\b(stay\s+with\s+us|stay\s+tuned)\b",
    r"\b(welcome\s+back|welcome\s+to\s+the\s+show)\b",
    r"\b(thank\s+you\s+(so\s+much\s+)?for\s+(being\s+with\s+us|joining\s+us|watching))\b",
    r"\b(thanks\s+for\s+having\s+me)\b",
    r"\b(good\s+(morning|evening|afternoon|night))\b",
    r"\b(this\s+is\s+the\s+context)\b",
    r"\b(coming\s+up(\s+next)?)\b",
    r"\b(reporting\s+live(\s+from)?)\b",
    r"\b(let('s|\s+us)\s+turn\s+(now\s+)?to)\b",
    r"\b(what\s+do\s+you\s+think)\b",
    r"\b(we('ll|\s+will)\s+be\s+right\s+back)\b",
    r"\b(briefly,\s+what\s+do\s+you\s+think)\b",
    # Hindi broadcast patterns
    r"(स्वागत\s+है|देख\s+रहे\s+हैं|विशेष\s+बुलेटिन|नमस्कार|धन्यवाद|ब्रेक\s+के\s+बाद|चर्चा\s+करेंगे)",
]

# Opinion / Speculation starters that aren't factual assertions
OPINION_STARTERS = [
    r"^(i\s+think(\s+that)?|i\s+believe(\s+that)?|in\s+my\s+opinion|it\s+seems\s+to\s+me|i\s+feel\s+that)\b",
    r"^(we\s+hope(\s+that)?|my\s+view\s+is|personally\s+speaking)\b",
    r"^(मुझे\s+लगता\s+है|मेरी\s+राय\s+में|हमारा\s+मानना\s+है)\b",
]

# Incomplete fragment starters (subordinate clauses without main clause)
FRAGMENT_STARTERS = [
    r"^(that\s+are|which\s+are|who\s+are|who\s+had|that\s+were|which\s+were|who\s+were)\b",
    r"^(and\s+was\s+being|and\s+were\s+being|and\s+it's\s+also)\b",
    r"^(of\s+opportunity|on\s+a\s+structure|in\s+the\s+territories)\b",
    r"^(those\s+of|that\s+like\s+i\s+said)\b",
    r"^(can\s+actually|will\s+supply|could\s+actually)\b",
]

def is_checkworthy_v2(text: str) -> bool:
    clean = re.sub(r"\[\d{2}:\d{2}\]", "", text).strip()
    words = clean.split()
    
    # Needs at least 5 words to form a meaningful standalone factual assertion
    if len(words) < 5:
        return False
        
    # Questions are not factual assertions
    if clean.endswith("?"):
        return False
        
    # Broadcast / filler patterns
    clean_lower = clean.lower()
    for bp in BROADCAST_PATTERNS:
        if re.search(bp, clean_lower):
            return False
            
    # Opinion starters
    for op in OPINION_STARTERS:
        if re.search(op, clean_lower):
            return False
            
    # Fragment starters
    for fp in FRAGMENT_STARTERS:
        if re.search(fp, clean_lower):
            return False

    # Check for trailing continuation words
    last_word = re.sub(r"[^\w\u0900-\u097F]", "", words[-1]).casefold()
    if last_word in {
        "between", "among", "about", "against", "under", "over", "through",
        "after", "before", "during", "since", "while", "until", "and", "or",
        "but", "with", "of", "in", "to", "for", "at", "by", "from", "the", "a", "an",
        "this", "these", "those", "my", "your", "his", "her", "its", "our", "their",
    }:
        return False

    # Must contain at least one verb / assertion predicate in English or Hindi
    has_verb = bool(re.search(
        r"\b(is|are|was|were|has|have|had|been|announced|approved|signed|passed|launched|"
        r"built|created|killed|died|increased|decreased|grew|fell|won|lost|elected|ruled|"
        r"ordered|stated|reported|imposed|sanctioned|invaded|attacked|struck|destroyed|"
        r"traveled|traveling|visited|spending|allocated|invested|reached|declared|"
        r"है|हैं|था|थी|थे|किया|किए|गया|गए|घोषित|शुरू|जीता|मारा|लागू)\b",
        clean_lower
    ))
    if not has_verb:
        return False

    # Check for factual anchor: entities, numbers, dates, or specific factual topics
    has_factual_anchor = bool(re.search(
        r"(\d+|"
        r"\b(government|president|minister|prime|parliament|congress|senate|court|"
        r"russia|ukraine|putin|biden|johnson|modi|india|china|us|united states|uk|"
        r"act|law|treaty|sanction|sanctions|policy|budget|crore|lakh|million|billion|trillion|dollar|dollars|rupee|rupees|percent|"
        r"missile|missiles|drone|drones|tank|tanks|military|army|war|weapon|weapons|nuclear|"
        r"capital|population|census|economy|gdp|inflation|oil|gas|energy|infrastructure)\b|"
        r"[0-9\u0966-\u096F]|"
        r"(सरकार|प्रधानमंत्री|राष्ट्रपति|संसद|कानून|योजना|बजट|करोड़|लाख|रुपये|प्रतिशत|हमला|युद्ध|संधि|मिसाइल|सेना|राजधानी|आबादी))",
        clean_lower
    ))

    return has_factual_anchor

# Test against the 27 groups from our real session
from test_grouping_v2 import groups

print("Evaluating 27 groups with is_checkworthy_v2:")
checkworthy_count = 0
for idx, (count, g) in enumerate(groups):
    cw = is_checkworthy_v2(g)
    if cw:
        checkworthy_count += 1
        print(f"\n[CHECKWORTHY] Group {idx+1}: {g}")
    else:
        print(f"[SKIPPED] Group {idx+1}: {g[:50]}...")

print(f"\nTotal checkworthy groups: {checkworthy_count} out of {len(groups)}")
