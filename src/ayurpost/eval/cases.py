"""All eval test fixtures — no API calls, pure data."""
from __future__ import annotations

# ── Retrieval Cases ────────────────────────────────────────────────────────────
# Each case: query + dosha filter → check returned chunks satisfy constraints.

RETRIEVAL_CASES = [
    {
        "id": "R01",
        "desc": "Monsoon vata query must return vata-tagged chunks, no pitta contamination",
        "query": "vata aggravation monsoon season joint pain stiffness digestion",
        "doshas": ["vata"],
        "top_k": 6,
        "must_contain_prefix": "sushruta-vol1-sutrasthana-ch46",  # known monsoon chapter
        "must_not_have_dosha": "pitta",
    },
    {
        "id": "R02",
        "desc": "Summer pitta query must return pitta-tagged chunks, no kapha contamination",
        "query": "pitta cooling herbs summer heat dehydration acidity skin rash",
        "doshas": ["pitta"],
        "top_k": 6,
        "must_contain_prefix": None,
        "must_not_have_dosha": "kapha",
    },
    {
        "id": "R03",
        "desc": "Kapha winter query must return kapha chunks, not vata-only chunks",
        "query": "kapha winter heavy sluggish immunity cold respiratory",
        "doshas": ["kapha"],
        "top_k": 6,
        "must_contain_prefix": None,
        "must_not_have_dosha": "vata",
    },
    {
        "id": "R04",
        "desc": "Service query without dosha filter must still return oil/massage content",
        "query": "abhyanga oil massage procedure benefits classical description",
        "doshas": None,
        "top_k": 6,
        "must_contain_prefix": None,
        "must_not_have_dosha": None,
        "must_contain_keyword": "oil",  # at least one result should mention oil
    },
]

# ── Compliance Cases ───────────────────────────────────────────────────────────
# Each case: voiceover text → expected compliance_ok verdict from auditor.

COMPLIANCE_CASES = [
    # PASS — general wellness / traditional language
    {
        "id": "C01",
        "text": "Ayurveda traditionally recommends warm sesame oil to support joint health during winter.",
        "expected": True,
        "desc": "Traditional recommendation phrasing — should pass",
    },
    {
        "id": "C02",
        "text": "Described in classical texts as beneficial for balancing vata dosha.",
        "expected": True,
        "desc": "Classical text reference — should pass",
    },
    {
        "id": "C03",
        "text": "This ancient practice may support digestive comfort when followed seasonally.",
        "expected": True,
        "desc": "Hedged wellness language — should pass",
    },
    # FAIL — regulated / cure / outcome language
    {
        "id": "C04",
        "text": "Reverse diabetes in 10 days with this Ayurvedic remedy, guaranteed.",
        "expected": False,
        "desc": "Cure + quantified outcome claim — must be flagged",
    },
    {
        "id": "C05",
        "text": "Clinically proven to cure arthritis and eliminate joint pain permanently.",
        "expected": False,
        "desc": "Cure claim — must be flagged",
    },
    {
        "id": "C06",
        "text": "Lose 10 kg in 30 days with our Ayurvedic weight loss treatment.",
        "expected": False,
        "desc": "Quantified outcome guarantee — must be flagged",
    },
    {
        "id": "C07",
        "text": "100% effective treatment for all skin diseases, scientifically proven.",
        "expected": False,
        "desc": "Absolute efficacy claim — must be flagged",
    },
]

# ── Groundedness Cases ─────────────────────────────────────────────────────────
# Each case: voiceover + chunk text(s) → expected grounded verdict.

GROUNDEDNESS_CASES = [
    {
        "id": "G01",
        "desc": "Faithful paraphrase of chunk content — should be grounded",
        "voiceover": "In monsoon, Ayurveda says the digestive fire weakens and light foods are recommended.",
        "chunks": [
            "The digestive fire (agni) is said to be weakened during the rainy season. "
            "Light, easily digestible food is therefore recommended by the classical texts."
        ],
        "expected": True,
    },
    {
        "id": "G02",
        "desc": "Fabricated superlative not in chunk — must be flagged as ungrounded",
        "voiceover": "Triphala is described as a cure for all diseases in the Sushruta Samhita.",
        "chunks": [
            "Triphala is described as useful in eye diseases and as a mild laxative "
            "in the classical texts. It is considered a rejuvenating compound."
        ],
        "expected": False,
    },
    {
        "id": "G03",
        "desc": "Accurate specific claim supported by chunk — should be grounded",
        "voiceover": "Sesame oil is recommended for massage to pacify vata and nourish the tissues.",
        "chunks": [
            "Sesame oil (tila taila) is described as the best among oils for vata conditions. "
            "Its application through massage is said to nourish the tissues and pacify vata."
        ],
        "expected": True,
    },
    {
        "id": "G04",
        "desc": "Exaggerated claim beyond what chunk supports — must be flagged",
        "voiceover": "Ashwagandha reverses ageing and fully restores youth according to Ayurveda.",
        "chunks": [
            "Ashwagandha is described as a rasayana herb that supports strength and vitality. "
            "It is used in classical formulations for general well-being."
        ],
        "expected": False,
    },
    {
        "id": "G05",
        "desc": "Minor stylistic framing ('Ayurveda recommends') is acceptable — should pass",
        "voiceover": "Ayurveda recommends bitter gourd and light soups to support weak digestion in monsoon.",
        "chunks": [
            "During the rainy season, bitter and pungent vegetables such as patola are "
            "considered appropriate. Light soups and easily digestible preparations are advised."
        ],
        "expected": True,
    },
]
