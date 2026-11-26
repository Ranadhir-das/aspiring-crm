"""AI Call Transcription (Hindi, English, Hinglish) & Structured Summarization Engine.

Supports an auto-cascading hybrid architecture:
  1. Google Gemini (GEMINI_API_KEY) — single-shot multimodal audio -> transcript + JSON summary/objections
  2. Groq (GROQ_API_KEY) — Whisper Large v3 Turbo + Llama 3.3 70B structured JSON analyzer
  3. OpenAI (OPENAI_API_KEY) — Whisper + GPT-4o-mini structured JSON analyzer
  4. 100% Free Self-Hosted Local CPU (default when no API key is configured) —
     `faster-whisper` (CTranslate2 INT8 with Silero VAD + Urdu->Hindi guard) +
     Bilingual Hindi/Devanagari/Hinglish/English Key-Points & Objections Analyzer.

Never blocks HTTP requests; intended to run inside `run_recording_ai_worker`.
"""
from __future__ import annotations

import base64
from dataclasses import dataclass, field
import gc
import json
import logging
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import threading
import time
from typing import Any
import wave

from django.conf import settings
from django.utils import timezone
import requests

logger = logging.getLogger('apps.calls')

# Domain prompt biasing speech recognition toward Indian education/consultancy Hindi, English & Hinglish vocabulary
DOMAIN_HINGLISH_PROMPT = (
    "Indian admissions counselling phone call in Hindi, English, and Hinglish. "
    "Common words: MBBS, BDS, BTech, MBA, BBA, BCA, BSc, Nursing, Pharmacy, NEET, "
    "admission, college, university, hostel, fees, budget, scholarship, loan, counselling, "
    "apostille, visa, abroad, Russia, Georgia, Kazakhstan, Nepal, Bangladesh, Philippines, "
    "WhatsApp, brochure, callback, office visit, sir, madam, haan, theek hai, baat, papa, mummy, parents."
)

# ---------------------------------------------------------------------------
# Devanagari -> Romanized Hinglish Transliteration (Pure Python, Zero Deps)
# ---------------------------------------------------------------------------

_DEVANAGARI_VOWELS = {
    'अ': 'a', 'आ': 'aa', 'इ': 'i', 'ई': 'ee', 'उ': 'u', 'ऊ': 'oo',
    'ऋ': 'ri', 'ए': 'e', 'ऐ': 'ai', 'ओ': 'o', 'औ': 'au', 'ऑ': 'o', 'ऍ': 'e',
}
_DEVANAGARI_MATRAS = {
    'ा': 'aa', 'ि': 'i', 'ी': 'ee', 'ु': 'u', 'ू': 'oo',
    'ृ': 'ri', 'े': 'e', 'ै': 'ai', 'ो': 'o', 'ौ': 'au', 'ॉ': 'o', 'ॅ': 'e',
}
_DEVANAGARI_CONSONANTS = {
    'क': 'k', 'ख': 'kh', 'ग': 'g', 'घ': 'gh', 'ङ': 'ng',
    'च': 'ch', 'छ': 'chh', 'ज': 'j', 'झ': 'jh', 'ञ': 'ny',
    'ट': 't', 'ठ': 'th', 'ड': 'd', 'ढ': 'dh', 'ण': 'n',
    'त': 't', 'थ': 'th', 'द': 'd', 'ध': 'dh', 'न': 'n',
    'प': 'p', 'फ': 'ph', 'ब': 'b', 'भ': 'bh', 'म': 'm',
    'य': 'y', 'र': 'r', 'ल': 'l', 'ळ': 'l', 'व': 'v',
    'श': 'sh', 'ष': 'sh', 'स': 's', 'ह': 'h',
    'क़': 'q', 'ख़': 'kh', 'ग़': 'gh', 'ज़': 'z', 'ड़': 'r', 'ढ़': 'rh', 'फ़': 'f',
}
_DEVANAGARI_DIGITS = str.maketrans('०१२३४५६७८९।', '0123456789.')
_HALANT = '्'
_NUKTA = '़'
_ANUSVARA = {'ं': 'n', 'ँ': 'n', 'ः': 'h'}


def devanagari_to_hinglish(text: str) -> str:
    """Lightweight Devanagari -> Latin Hinglish transliterator for unified bilingual analysis."""
    if not text:
        return ''
    text = text.translate(_DEVANAGARI_DIGITS)
    out: list[str] = []
    n = len(text)
    i = 0
    while i < n:
        ch = text[i]
        # Handle consonant + nukta combination
        if i + 1 < n and text[i + 1] == _NUKTA and (ch + _NUKTA) in _DEVANAGARI_CONSONANTS:
            base = _DEVANAGARI_CONSONANTS[ch + _NUKTA]
            i += 2
        elif ch in _DEVANAGARI_CONSONANTS:
            base = _DEVANAGARI_CONSONANTS[ch]
            i += 1
        elif ch in _DEVANAGARI_VOWELS:
            out.append(_DEVANAGARI_VOWELS[ch])
            i += 1
            continue
        elif ch in _ANUSVARA:
            out.append(_ANUSVARA[ch])
            i += 1
            continue
        elif ch in _DEVANAGARI_MATRAS:
            out.append(_DEVANAGARI_MATRAS[ch])
            i += 1
            continue
        elif ch == _HALANT:
            i += 1
            continue
        else:
            out.append(ch)
            i += 1
            continue

        # Attach following halant or matra, otherwise inherent 'a' (except word-final schwa deletion)
        if i < n and text[i] == _HALANT:
            out.append(base)
            i += 1
        elif i < n and text[i] in _DEVANAGARI_MATRAS:
            out.append(base + _DEVANAGARI_MATRAS[text[i]])
            i += 1
        else:
            next_is_dev = i < n and ('\u0900' <= text[i] <= '\u097f')
            out.append(base + ('a' if next_is_dev else ''))
    return ''.join(out)


# ---------------------------------------------------------------------------
# Language Classification (Hindi / English / Hinglish)
# ---------------------------------------------------------------------------

_DEVANAGARI_RE = re.compile(r'[\u0900-\u097f]')
_ARABIC_URDU_RE = re.compile(r'[\u0600-\u06ff]')
_LATIN_WORD_RE = re.compile(r'[a-zA-Z]{2,}')

_HINGLISH_WORDS = {
    'haan', 'han', 'ha', 'nahi', 'nahin', 'nai', 'theek', 'thik', 'accha', 'acha',
    'achha', 'bhaiya', 'bhai', 'didi', 'ji', 'namaste', 'namaskar', 'kaise', 'kaisa',
    'kya', 'kyun', 'kyu', 'kab', 'kahan', 'kaha', 'kitna', 'kitni', 'kaun', 'mein',
    'mai', 'main', 'aap', 'aapka', 'apka', 'mera', 'meri', 'hum', 'hamara', 'karna',
    'karo', 'karenge', 'kar', 'raha', 'rahi', 'rahe', 'hai', 'hain', 'tha', 'thi',
    'hoga', 'hogi', 'ho', 'gaya', 'gayi', 'chahiye', 'chahte', 'batao', 'bataiye',
    'batana', 'baat', 'bol', 'boli', 'boliye', 'abhi', 'baad', 'kal', 'aaj', 'shaam',
    'subah', 'papa', 'mummy', 'ghar', 'paise', 'paisa', 'mehenga', 'zyada', 'jyada',
    'kam', 'door', 'bahar', 'samajh', 'puchna', 'pooch', 'puch', 'dekh', 'dekhenge',
    'soch', 'bhej', 'bhejiye', 'bhejdo', 'zaroor', 'bilkul', 'lekin', 'magar', 'phir',
    'fir', 'toh', 'to', 'aur', 'par', 'pe', 'se', 'ko', 'ka', 'ki', 'ke', 'wala', 'wali',
}

_ENGLISH_COMMON_WORDS = {
    'hello', 'yes', 'no', 'okay', 'please', 'thank', 'thanks', 'sir', 'madam', 'call',
    'calling', 'admission', 'college', 'course', 'fees', 'fee', 'budget', 'hostel',
    'university', 'student', 'parents', 'father', 'mother', 'family', 'busy', 'later',
    'tomorrow', 'evening', 'morning', 'whatsapp', 'brochure', 'details', 'information',
    'interested', 'want', 'need', 'about', 'from', 'with', 'have', 'will', 'would',
    'could', 'can', 'this', 'that', 'what', 'when', 'where', 'which', 'how', 'much',
    'already', 'joined', 'score', 'marks', 'result', 'exam', 'document', 'documents',
    'office', 'visit', 'location', 'city', 'abroad', 'counselling', 'counseling',
    'scholarship', 'loan', 'year', 'intake', 'mbbs', 'btech', 'mba', 'bba', 'bca', 'bsc',
    'neet', 'nursing', 'pharmacy', 'apostille', 'visa',
}


def detect_call_language(text: str, whisper_lang: str | None = None) -> str:
    """Accurately classify an Indian CRM call as Hindi, English, or Hinglish."""
    if not text or not text.strip():
        if whisper_lang == 'hi':
            return 'Hindi'
        if whisper_lang == 'en':
            return 'English'
        return 'Unknown'

    dev_chars = len(_DEVANAGARI_RE.findall(text))
    latin_words = [w.lower() for w in _LATIN_WORD_RE.findall(text)]
    latin_count = len(latin_words)

    if dev_chars >= 6 and latin_count >= 3:
        return 'Hinglish'
    if dev_chars >= 6 and latin_count < 3:
        return 'Hindi'

    # All-Latin transcript: distinguish pure English vs Romanized Hinglish
    hinglish_hits = sum(1 for w in latin_words if w in _HINGLISH_WORDS)
    english_hits = sum(1 for w in latin_words if w in _ENGLISH_COMMON_WORDS)
    if hinglish_hits >= 2 and english_hits >= 2:
        return 'Hinglish'
    if hinglish_hits >= 3 and hinglish_hits >= english_hits:
        return 'Hinglish' if english_hits >= 1 else 'Hindi'
    if whisper_lang in ('hi', 'ur') and latin_count > 0:
        return 'Hinglish' if english_hits >= 2 else 'Hindi'
    return 'English'


# ---------------------------------------------------------------------------
# Bilingual Hindi / English / Hinglish Objections & Key Points Analyzer
# ---------------------------------------------------------------------------

OBJECTION_RULES: list[dict[str, Any]] = [
    {
        'category': 'FEES_BUDGET',
        'label': 'Fees / Budget Concern',
        'detail': 'Contact raised concerns about fees, affordability, total cost, or financing.',
        'next_step': 'Share itemized fee structure, scholarship options, or education loan guidance on WhatsApp.',
        'patterns': [
            r'\b(costly|expensive|unaffordable|mehenga|mahanga|mahnga|mahenga|loan|emi|scholarship|discount|concession|installment|instalment)\b',
            r'\b(not\s+afford|cant\s+afford|can\'t\s+afford|afford\s+nahi|paisa\s+nahi|paise\s+nahi|budget\s+se\s+upar|out\s+of\s+budget|over\s+budget|tight\s+budget|low\s+budget)\b',
            r'\b(zyada|jyada|jada|bahut|to\s+high|to\s+much|kam\s+kar|less|reduce|kam|tight|issue|problem|dikkat|manage|upar|exceed)\b.{0,32}\b(fee|fees|fe|fes|phis|phees|cost|budget|bajat|paisa|paise|amount|lakh|lakhs|charge|charges)\b',
            r'\b(fee|fees|fe|fes|phis|phees|cost|budget|bajat|paisa|paise|amount|lakh|lakhs|charge|charges)\b.{0,32}\b(zyada|jyada|jada|high|much|problem|issue|dikkat|tight|kam|upar|nahi|less|reduce|manage|heavy)\b',
        ],
    },
    {
        'category': 'FAMILY_APPROVAL',
        'label': 'Parents / Family Approval',
        'detail': 'Decision depends on consulting parents or family members before proceeding.',
        'next_step': 'Schedule a joint callback or counselling session including parents and send brochure on WhatsApp.',
        'patterns': [
            r'\b(parents|parent|father|mother|papa|mummy|mummi|dad|mom|pita|pitaji|mata|mataji|family|parivar|ghar\s+pe|ghar\s+par|ghar\s+mein|ghar\s+me|gharwale|bhai|bhaiya|didi|husband|wife|uncle)\b.{0,35}\b(ask|discuss|talk|baat|puch|pooch|consult|bata|decide|permission|confirm|speak)\b',
            r'\b(ask|discuss|talk|baat|puch|pooch|consult|decide|permission|confirm)\b.{0,35}\b(parents|parent|father|mother|papa|mummy|mummi|dad|mom|family|parivar|ghar|bhai|husband)\b',
            r'\b(papa\s+se|mummy\s+se|parents\s+se|ghar\s+pe\s+baat|family\s+se\s+baat|ghar\s+mein\s+baat|ask\s+my\s+father|ask\s+my\s+parents|talk\s+to\s+my\s+parents)\b',
        ],
    },
    {
        'category': 'TIMING_BUSY',
        'label': 'Busy / Call Later',
        'detail': 'Contact was unavailable/busy or requested to defer the discussion to a later time.',
        'next_step': 'Schedule a timely follow-up reminder and send a short summary message on WhatsApp.',
        'patterns': [
            r'\b(busy|biji|vyast|driving|drive|meeting|office\s+mein|class\s+mein|exam|exams|paper)\b',
            r'\b(call\s+later|call\s+me\s+later|call\s+back|baad\s+mein|baad\s+me|bad\s+mein|bad\s+me|abhi\s+nahi|abhi\s+time\s+nahi|time\s+nahi|free\s+nahi|kal\s+call|kal\s+baat|shaam\s+ko|sham\s+ko|evening\s+mein|after\s+result|result\s+ke\s+baad)\b',
            r'\b(not\s+a\s+good\s+time|can\s+we\s+talk\s+later|will\s+call\s+you|khud\s+call\s+kar)\b',
        ],
    },
    {
        'category': 'ALREADY_ENROLLED',
        'label': 'Already Enrolled / Not Interested',
        'detail': 'Contact indicated they already took admission elsewhere, changed plans, or are not interested.',
        'next_step': 'Verify disposition and avoid unnecessary repeat calls unless contact asks for alternatives.',
        'patterns': [
            r'\b(not\s+interested|no\s+interest|nahi\s+chahiye|nahin\s+chahiye|nahi\s+karna|nahin\s+karna|interest\s+nahi|interested\s+nahi|drop\s+kar|cancel\s+kar|plan\s+change|abhi\s+plan\s+nahi)\b',
            r'\b(already\s+admitted|already\s+joined|already\s+took|already\s+taken|taken\s+admission.{0,20}\b(yes|haan|han|ji)\b|where\s+have\s+you\s+taken\s+admission|admission\s+ho\s+gaya|admission\s+le\s+liya|ho\s+gaya\s+admission|dusre\s+college|other\s+consultancy|kisi\s+aur\s+se|already\s+enrolled)\b',
            r'\b(wrong\s+number|galat\s+number|dont\s+call|don\'t\s+call|mat\s+karo\s+call|call\s+mat\s+kar)\b',
        ],
    },
    {
        'category': 'LOCATION_DISTANCE',
        'label': 'Location / Distance / Travel',
        'detail': 'Contact expressed hesitation regarding campus location, distance from home, hostel, or studying abroad.',
        'next_step': 'Highlight safe hostel facilities, travel connectivity, or nearer campus alternatives.',
        'patterns': [
            r'\b(too\s+far|bahut\s+door|bohot\s+door|door\s+hai|dur\s+hai|distance|location\s+problem|relocate|out\s+of\s+state|bahar\s+nahi|abroad\s+nahi|foreign\s+nahi|safety|safe\s+hai|hostel\s+problem|local\s+college)\b',
        ],
    },
    {
        'category': 'ELIGIBILITY_DOCS',
        'label': 'Eligibility / Marks / Documents',
        'detail': 'Contact mentioned score/NEET eligibility, pending results, gap year, or document/passport readiness.',
        'next_step': 'Confirm eligibility criteria and guide the candidate on required documents or qualifying routes.',
        'patterns': [
            r'\b(net\s+nahi|net\s+kam|low\s+score|marks\s+kam|kam\s+marks|percentage\s+kam|not\s+qualified|qualify\s+nahi|fail|compartment|gap\s+year|result\s+pending|result\s+nahi\s+aya|pasport\s+nahi|passport\s+nahi|document\s+nahi|documents\s+pending|dokyument|eligibility)\b',
        ],
    },
    {
        'category': 'TRUST_VERIFICATION',
        'label': 'Trust / Verification / Proof',
        'detail': 'Contact asked for office address, accreditation proof, official website, or expressed skepticism.',
        'next_step': 'Share official office address, accreditation links, and invite candidate for in-person/video counselling.',
        'patterns': [
            r'\b(fake|fraud|genuine|trust|bharosa|guarantee|proof|approved|nmc\s+approved|ugc\s+approved|office\s+address|office\s+kahan|visit\s+karna|in\s+person|website|review|reviews)\b',
        ],
    },
]

_TOPIC_PATTERNS: list[tuple[str, str]] = [
    ('MBBS', r'\b(mbbs|mbabs|m\.b\.b\.s|medical|doctor)\b'),
    ('NEET', r'\b(neet|neet\s+ug|neet\s+pg)\b'),
    ('MBA / Management', r'\b(mba|pgdm|bba|management)\b'),
    ('BTech / Engineering', r'\b(b\.?tech|btech|engineering|computer\s+science|cse)\b'),
    ('BCA / MCA / IT', r'\b(bca|mca|data\s+science)\b'),
    ('Nursing / Pharmacy / Allied', r'\b(nursing|gnm|anm|b\.?pharm|d\.?pharm|pharmacy|bds|bams|bhms|paramedical|physiotherapy)\b'),
    ('Study Abroad', r'\b(abroad|russia|georgia|kazakhstan|uzbekistan|philippines|nepal|bangladesh|kyrgyzstan|egypt|foreign|visa)\b'),
    ('Apostille / Attestation', r'\b(apostille|attestation|mea|embassy|notary|hrd|certificate\s+attestation)\b'),
    ('Hostel / Accommodation', r'\b(hostel|accommodation|mess|food|stay)\b'),
    ('WhatsApp / Brochure Request', r'\b(whatsapp|watsapp|brochure|pdf|details\s+bhej|send\s+details|share\s+details|message\s+kar)\b'),
    ('Counselling / Office Visit', r'\b(counsel+ing|counsel+or|office\s+visit|visit\s+office|google\s+meet|video\s+call|appointment)\b'),
]

_POSITIVE_RE = re.compile(
    r'\b(interested|haan\s+ji|bilkul|zaroor|sure|definitely|want\s+to\s+join|admission\s+lena|'
    r'send\s+details|details\s+bhej|whatsapp\s+pe\s+bhej|visit\s+kar|office\s+aana|great|good|thank\s+you|thanks|theek\s+hai|okay\s+send)\b',
    re.IGNORECASE,
)
_NEGATIVE_RE = re.compile(
    r'\b(not\s+interested|nahi\s+chahiye|nahin\s+chahiye|wrong\s+number|galat\s+number|'
    r'already\s+admitted|admission\s+ho\s+gaya|dont\s+call|don\'t\s+call|mat\s+karo|cancel|drop)\b',
    re.IGNORECASE,
)
_FILLER_ONLY_RE = re.compile(
    r'^[\s.,?!\-]*(hello|hi|haan|han|yes|ji|okay|ok|hm+|hmm+|uh+|um+|namaste|sir|madam|boliye|aawaz|awaz|audible)[\s.,?!\-]*$',
    re.IGNORECASE,
)
_YEAR_RE = re.compile(r'\b(202[5-9]|2030)\b')
_FEE_AMOUNT_RE = re.compile(r'\b(\d+(?:\.\d+)?\s*(?:lakh|lakhs|lac|lacs|k|thousand|crore))\b', re.IGNORECASE)


def _split_sentences(text: str, segments: list[dict[str, Any]] | None = None) -> list[str]:
    if segments:
        raw = [str(s.get('text') or '').strip() for s in segments if str(s.get('text') or '').strip()]
        if raw:
            return raw
    parts = re.split(r'(?<=[.!?।])\s+|\n+', text.strip())
    return [p.strip() for p in parts if p.strip()]


def _clean_excerpt(text: str, max_len: int = 160) -> str:
    clean = re.sub(r'\s+', ' ', (text or '').strip())
    if len(clean) <= max_len:
        return clean
    return clean[: max_len - 1].rstrip() + '…'


def analyze_transcript_locally(
    transcript_text: str,
    *,
    segments: list[dict[str, Any]] | None = None,
    whisper_lang: str | None = None,
    call_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Deterministic zero-cost bilingual Hindi/English/Hinglish call intelligence analyzer."""
    ctx = call_context or {}
    direction_label = 'Incoming' if ctx.get('direction') == 'INBOUND' else 'Outgoing'
    contact_label = ctx.get('lead_name') or ctx.get('phone_number') or 'the contact'
    language = detect_call_language(transcript_text, whisper_lang=whisper_lang)

    clean_text = re.sub(r'\s+', ' ', (transcript_text or '').strip())
    if len(clean_text) < 6:
        return {
            'language': language,
            'sentiment': 'NEUTRAL',
            'summary': (
                f'{direction_label} call with {contact_label} contained no clear speech '
                '(likely unanswered ringing, voicemail, or silent audio).'
            ),
            'key_points': ['No intelligible conversation detected in audio.'],
            'objections': [],
            'next_action': 'Verify if the call connected or retry contact during business hours.',
        }

    sentences = _split_sentences(transcript_text, segments)
    normalized_pairs = [
        (s, re.sub(r'([aeiou])\1+', r'\1', devanagari_to_hinglish(s).lower()))
        for s in sentences
    ]
    full_norm = ' '.join(norm for _, norm in normalized_pairs)

    # 1. Detect Topics
    topics: list[str] = []
    for label, pattern in _TOPIC_PATTERNS:
        if re.search(pattern, full_norm, re.IGNORECASE):
            topics.append(label)

    # 2. Detect Objections (with exact call excerpt evidence)
    objections: list[dict[str, str]] = []
    next_steps_from_obj: list[str] = []
    for rule in OBJECTION_RULES:
        evidence = ''
        for orig_sent, norm_sent in normalized_pairs:
            if any(re.search(pat, norm_sent, re.IGNORECASE) for pat in rule['patterns']):
                evidence = _clean_excerpt(orig_sent, 150)
                break
        if not evidence and any(re.search(pat, full_norm, re.IGNORECASE) for pat in rule['patterns']):
            evidence = _clean_excerpt(clean_text, 150)
        if evidence:
            objections.append({
                'category': rule['category'],
                'label': rule['label'],
                'detail': rule['detail'],
                'evidence': evidence,
            })
            next_steps_from_obj.append(rule['next_step'])

    # 3. Sentiment classification
    pos_hits = len(_POSITIVE_RE.findall(full_norm))
    neg_hits = len(_NEGATIVE_RE.findall(full_norm))
    obj_cats = {o['category'] for o in objections}

    if 'ALREADY_ENROLLED' in obj_cats or (neg_hits >= 2 and pos_hits == 0):
        sentiment = 'NEGATIVE'
    elif objections and pos_hits >= 1:
        sentiment = 'HESITANT'
    elif objections:
        sentiment = 'HESITANT' if len(objections) >= 1 else 'NEUTRAL'
    elif pos_hits >= 1:
        sentiment = 'POSITIVE'
    else:
        sentiment = 'NEUTRAL'

    # 4. Key Points extraction (topics + concrete entities + high-signal excerpts)
    key_points: list[str] = []
    if topics:
        key_points.append(f"Topics discussed: {', '.join(topics[:5])}.")

    years = sorted(set(_YEAR_RE.findall(full_norm)))
    if years:
        key_points.append(f"Admission/intake year mentioned: {', '.join(years)}.")

    amounts = list(dict.fromkeys(m.group(1).strip() for m in _FEE_AMOUNT_RE.finditer(full_norm)))
    if amounts:
        key_points.append(f"Fee/budget figures discussed: {', '.join(amounts[:3])}.")

    # Pick up to 3 informative, deduplicated non-filler utterances not already quoted in objections
    evidence_set = {o['evidence'] for o in objections}
    seen_excerpts: set[str] = set()
    ranked_sentences: list[tuple[int, str]] = []
    for orig_sent, norm_sent in normalized_pairs:
        excerpt = _clean_excerpt(orig_sent, 155)
        dedupe_key = re.sub(r'[^a-z0-9]+', ' ', norm_sent).strip()
        if (
            len(excerpt) < 16
            or _FILLER_ONLY_RE.match(norm_sent)
            or excerpt in evidence_set
            or not dedupe_key
            or dedupe_key in seen_excerpts
        ):
            continue
        seen_excerpts.add(dedupe_key)
        score = min(40, len(norm_sent.split()))
        if any(re.search(pat, norm_sent, re.IGNORECASE) for _, pat in _TOPIC_PATTERNS):
            score += 30
        if _POSITIVE_RE.search(norm_sent):
            score += 20
        ranked_sentences.append((score, excerpt))

    ranked_sentences.sort(key=lambda item: item[0], reverse=True)
    for _, excerpt in ranked_sentences[:3]:
        if len(key_points) >= 5:
            break
        key_points.append(f'"{excerpt}"')

    if not key_points:
        key_points.append(f'Conversation excerpt: "{_clean_excerpt(clean_text, 160)}"')

    # 5. Executive Summary (2-3 crisp sentences)
    summary_parts = [f"{direction_label} {language} call with {contact_label}"]
    if topics:
        summary_parts[0] += f" covering {', '.join(topics[:3])}."
    else:
        summary_parts[0] += f" ({len(sentences)} spoken segment{'s' if len(sentences) != 1 else ''})."

    if objections:
        obj_names = ', '.join(o['label'] for o in objections[:3])
        summary_parts.append(f"Objections raised: {obj_names}.")
    elif sentiment == 'POSITIVE':
        summary_parts.append('Contact showed positive engagement with no major objections flagged.')
    else:
        summary_parts.append('No explicit objections were flagged during the conversation.')

    # Add best content line if summary is still short
    if ranked_sentences and len(' '.join(summary_parts)) < 210:
        summary_parts.append(f'Highlight: "{_clean_excerpt(ranked_sentences[0][1], 110)}"')

    summary = ' '.join(summary_parts)

    # 6. Next Action
    if next_steps_from_obj:
        next_action = next_steps_from_obj[0]
    elif 'WhatsApp / Brochure Request' in topics:
        next_action = 'Send requested course details/brochure on WhatsApp and confirm receipt.'
    elif 'Counselling / Office Visit' in topics:
        next_action = 'Confirm counselling slot / office visit time and assign counselor.'
    elif sentiment == 'POSITIVE':
        next_action = 'Proceed with counselling handoff or schedule confirmation follow-up.'
    else:
        next_action = 'Review notes and follow up according to lead status.'

    return {
        'language': language,
        'sentiment': sentiment,
        'summary': summary[:1200],
        'key_points': key_points[:6],
        'objections': objections[:6],
        'next_action': next_action[:250],
    }


# ---------------------------------------------------------------------------
# Local Faster-Whisper Singleton (Idle Auto-Unload to Keep Server RAM Low)
# ---------------------------------------------------------------------------

_MODEL_LOCK = threading.Lock()
_LOCAL_MODEL: Any = None
_LOCAL_MODEL_LAST_USED: float = 0.0


def _get_local_whisper_model():
    global _LOCAL_MODEL, _LOCAL_MODEL_LAST_USED
    with _MODEL_LOCK:
        if _LOCAL_MODEL is None:
            from faster_whisper import WhisperModel

            model_size = getattr(settings, 'AI_LOCAL_WHISPER_MODEL', 'small')
            compute_type = getattr(settings, 'AI_LOCAL_WHISPER_COMPUTE_TYPE', 'int8')
            cpu_threads = int(getattr(settings, 'AI_LOCAL_WHISPER_CPU_THREADS', 2))
            model_dir = Path(getattr(settings, 'AI_LOCAL_WHISPER_DIR', Path(settings.BASE_DIR) / 'whisper_models'))
            model_dir.mkdir(parents=True, exist_ok=True)
            logger.info(
                'Loading faster-whisper model=%s compute=%s threads=%s dir=%s',
                model_size,
                compute_type,
                cpu_threads,
                model_dir,
            )
            _LOCAL_MODEL = WhisperModel(
                model_size,
                device='cpu',
                compute_type=compute_type,
                cpu_threads=cpu_threads,
                num_workers=1,
                download_root=str(model_dir),
            )
        _LOCAL_MODEL_LAST_USED = time.monotonic()
        return _LOCAL_MODEL


def unload_local_model_if_idle(idle_seconds: int | None = None) -> bool:
    """Release local Whisper RAM when the worker has been idle."""
    global _LOCAL_MODEL
    max_idle = idle_seconds if idle_seconds is not None else int(getattr(settings, 'AI_LOCAL_MODEL_IDLE_SECONDS', 300))
    with _MODEL_LOCK:
        if _LOCAL_MODEL is not None and (time.monotonic() - _LOCAL_MODEL_LAST_USED) >= max_idle:
            logger.info('Unloading idle faster-whisper model after %ss inactivity to free RAM.', max_idle)
            _LOCAL_MODEL = None
            gc.collect()
            return True
    return False


def _prepare_wav_16k(input_path: str) -> str:
    """Convert any call recording container into clean 16kHz mono WAV for speech recognition."""
    ffmpeg = shutil.which('ffmpeg')
    if not ffmpeg:
        return input_path
    with tempfile.NamedTemporaryFile(suffix='.wav', delete=False) as tmp:
        out_path = tmp.name
    cmd = [
        ffmpeg, '-y', '-hide_banner', '-loglevel', 'error',
        '-i', input_path,
        '-vn', '-ac', '1', '-ar', '16000', '-c:a', 'pcm_s16le',
        out_path,
    ]
    res = subprocess.run(cmd, capture_output=True, text=True, timeout=60, check=False)
    if res.returncode == 0 and os.path.isfile(out_path) and os.path.getsize(out_path) > 44:
        return out_path
    try:
        os.unlink(out_path)
    except OSError:
        pass
    return input_path


def _format_timestamped_transcript(segments: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    for seg in segments:
        start = max(0, int(seg.get('start') or 0))
        text = str(seg.get('text') or '').strip()
        if not text:
            continue
        mm, ss = divmod(start, 60)
        lines.append(f'[{mm:02d}:{ss:02d}] {text}')
    return '\n'.join(lines)


def _load_wav_16k_float32(wav_path: str):
    """Read a 16kHz mono PCM16 WAV into a normalized float32 numpy array.

    Bypasses PyAV container decoding inside faster-whisper (which breaks on PyAV >= 17
    due to removed `metadata_errors` kwarg) and avoids decoding twice on Urdu->Hindi retries.
    """
    try:
        import numpy as np
        with wave.open(wav_path, 'rb') as wf:
            if wf.getnchannels() == 1 and wf.getframerate() == 16000 and wf.getsampwidth() == 2:
                pcm = wf.readframes(wf.getnframes())
                if pcm:
                    return np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
    except Exception as exc:
        logger.debug('Falling back to file-path audio decode for %s: %s', wav_path, exc)
    return wav_path


def _collapse_asr_repetitions(text: str) -> str:
    """Strip Whisper repetition-loop artifacts (e.g. 'तो तो तो तो...') while keeping normal speech."""
    if not text:
        return ''
    # Collapse 4+ consecutive identical tokens (supports Latin and Devanagari scripts)
    cleaned = re.sub(r'([^\s,.;:!?]+)(?:\s+\1){3,}', r'\1', text, flags=re.IGNORECASE)
    return re.sub(r'\s+', ' ', cleaned).strip()


def _transcribe_local_whisper(audio_path: str, call_context: dict[str, Any]) -> dict[str, Any]:
    wav_path = _prepare_wav_16k(audio_path)
    cleanup_wav = wav_path != audio_path
    try:
        model = _get_local_whisper_model()
        audio_input = _load_wav_16k_float32(wav_path) if cleanup_wav else wav_path

        def _run_pass(language: str | None = None):
            seg_iter, info = model.transcribe(
                audio_input,
                language=language,
                task='transcribe',
                beam_size=2,
                temperature=(0.0, 0.4),
                compression_ratio_threshold=2.2,
                no_speech_threshold=0.6,
                repetition_penalty=1.15,
                no_repeat_ngram_size=3,
                vad_filter=True,
                vad_parameters={'min_silence_duration_ms': 400},
                condition_on_previous_text=False,
                initial_prompt=DOMAIN_HINGLISH_PROMPT,
            )
            segs = []
            for s in seg_iter:
                txt = _collapse_asr_repetitions(s.text or '')
                if txt:
                    segs.append({
                        'start': round(float(s.start), 2),
                        'end': round(float(s.end), 2),
                        'text': txt,
                    })
            return segs, getattr(info, 'language', None)

        segments, detected_lang = _run_pass(language=None)
        joined_plain = ' '.join(s['text'] for s in segments)

        # Classic Whisper Indian-call fix: spoken Hindi/Hinglish is often mis-tagged as Urdu ('ur').
        # Re-run forced to Hindi ('hi') whenever Urdu or Arabic script is produced.
        if detected_lang == 'ur' or _ARABIC_URDU_RE.search(joined_plain):
            logger.info('Whisper detected Urdu script on Indian CRM call; re-running forced to Hindi (hi).')
            segments, detected_lang = _run_pass(language='hi')
            joined_plain = ' '.join(s['text'] for s in segments)

        formatted_transcript = _format_timestamped_transcript(segments)
        analysis = analyze_transcript_locally(
            joined_plain,
            segments=segments,
            whisper_lang=detected_lang,
            call_context=call_context,
        )
        model_name = getattr(settings, 'AI_LOCAL_WHISPER_MODEL', 'small')
        return {
            'transcript_text': formatted_transcript or joined_plain,
            'transcript_segments': segments,
            'ai_summary': analysis['summary'],
            'ai_key_points': analysis['key_points'],
            'ai_objections': analysis['objections'],
            'ai_language': analysis['language'],
            'ai_sentiment': analysis['sentiment'],
            'ai_next_action': analysis['next_action'],
            'ai_provider': f'local-faster-whisper-{model_name}',
        }
    finally:
        if cleanup_wav and os.path.exists(wav_path):
            try:
                os.unlink(wav_path)
            except OSError:
                pass


# ---------------------------------------------------------------------------
# Cloud LLM JSON Parsing & Normalization Helper
# ---------------------------------------------------------------------------

_CLOUD_SYSTEM_PROMPT = """You are a senior Indian Admissions & CRM Call Quality Analyst for Vaani CRM.
Analyze the phone call (which may be in Hindi, English, or Hinglish code-switching).
Return ONLY valid JSON with these exact keys:
{
  "language": "Hindi" | "English" | "Hinglish",
  "transcript": "Clean readable transcript preserving Hindi/English/Hinglish with speaker turns (Caller: / Contact:) or timestamps",
  "summary": "2-3 crisp English sentences summarizing the purpose, discussion, and outcome so an admin does not need to listen to the audio.",
  "key_points": ["3 to 6 concise factual bullet points in clear English (courses, intake year, marks/NEET, preferences, promises)"],
  "objections": [
    {
      "category": "FEES_BUDGET" | "FAMILY_APPROVAL" | "TIMING_BUSY" | "ALREADY_ENROLLED" | "LOCATION_DISTANCE" | "ELIGIBILITY_DOCS" | "TRUST_VERIFICATION" | "OTHER",
      "label": "Short objection title",
      "detail": "1 sentence explaining the objection clearly in English",
      "evidence": "Short direct quote from the call"
    }
  ],
  "sentiment": "POSITIVE" | "NEUTRAL" | "HESITANT" | "NEGATIVE",
  "next_action": "One concrete next step for the caller or counselor"
}
If no objections were raised, return "objections": []."""


def _extract_json_block(raw: str) -> dict[str, Any]:
    text = (raw or '').strip()
    if text.startswith('```'):
        text = re.sub(r'^```(?:json)?\s*', '', text)
        text = re.sub(r'\s*```$', '', text)
    start = text.find('{')
    end = text.rfind('}')
    if start >= 0 and end > start:
        return json.loads(text[start : end + 1])
    return json.loads(text)


def _normalize_llm_payload(
    parsed: dict[str, Any],
    *,
    fallback_transcript: str = '',
    segments: list[dict[str, Any]] | None = None,
    provider: str,
) -> dict[str, Any]:
    transcript = str(parsed.get('transcript') or fallback_transcript or '').strip()
    summary = str(parsed.get('summary') or '').strip()
    language = str(parsed.get('language') or detect_call_language(transcript)).strip()[:24]
    sentiment = str(parsed.get('sentiment') or 'NEUTRAL').strip().upper()
    if sentiment not in {'POSITIVE', 'NEUTRAL', 'HESITANT', 'NEGATIVE'}:
        sentiment = 'NEUTRAL'

    raw_kp = parsed.get('key_points') or []
    key_points = [str(p).strip() for p in raw_kp if str(p).strip()][:8]

    raw_obj = parsed.get('objections') or []
    objections: list[dict[str, str]] = []
    for item in raw_obj[:8]:
        if isinstance(item, dict):
            label = str(item.get('label') or item.get('category') or 'Objection').strip()[:80]
            detail = str(item.get('detail') or '').strip()[:300]
            evidence = str(item.get('evidence') or '').strip()[:200]
            cat = str(item.get('category') or 'OTHER').strip().upper()[:32]
            if label or detail:
                objections.append({
                    'category': cat,
                    'label': label,
                    'detail': detail,
                    'evidence': evidence,
                })
        elif isinstance(item, str) and item.strip():
            objections.append({
                'category': 'OTHER',
                'label': 'Objection',
                'detail': item.strip()[:300],
                'evidence': '',
            })

    next_action = str(parsed.get('next_action') or '').strip()[:250]
    return {
        'transcript_text': transcript,
        'transcript_segments': segments or [],
        'ai_summary': summary[:1500],
        'ai_key_points': key_points,
        'ai_objections': objections,
        'ai_language': language,
        'ai_sentiment': sentiment,
        'ai_next_action': next_action,
        'ai_provider': provider,
    }


def _transcribe_and_summarize_gemini(audio_path: str, mime_type: str, call_context: dict[str, Any]) -> dict[str, Any]:
    api_key = getattr(settings, 'AI_GEMINI_API_KEY', '').strip()
    model = getattr(settings, 'AI_GEMINI_MODEL', 'gemini-2.5-flash').strip()
    if not api_key:
        raise RuntimeError('GEMINI_API_KEY is not configured.')

    audio_bytes = Path(audio_path).read_bytes()
    encoded = base64.b64encode(audio_bytes).decode('ascii')
    ctx_note = (
        f"Call direction: {call_context.get('direction', 'OUTBOUND')}. "
        f"Lead: {call_context.get('lead_name') or call_context.get('phone_number') or 'Unknown'}. "
        f"Duration: {call_context.get('duration_seconds', 0)}s."
    )
    url = f'https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}'
    payload = {
        'contents': [
            {
                'parts': [
                    {'text': f'{_CLOUD_SYSTEM_PROMPT}\n\nContext: {ctx_note}'},
                    {'inline_data': {'mime_type': mime_type or 'audio/mp4', 'data': encoded}},
                ]
            }
        ],
        'generationConfig': {
            'temperature': 0.2,
            'responseMimeType': 'application/json',
        },
    }
    resp = requests.post(url, json=payload, timeout=120)
    resp.raise_for_status()
    data = resp.json()
    text_out = data['candidates'][0]['content']['parts'][0]['text']
    parsed = _extract_json_block(text_out)
    return _normalize_llm_payload(parsed, provider=f'gemini-{model}')


def _transcribe_openai_compatible(
    *,
    audio_path: str,
    api_key: str,
    base_url: str,
    whisper_model: str,
    llm_model: str,
    provider_label: str,
    call_context: dict[str, Any],
) -> dict[str, Any]:
    headers = {'Authorization': f'Bearer {api_key}'}
    with open(audio_path, 'rb') as fp:
        stt_resp = requests.post(
            f'{base_url}/audio/transcriptions',
            headers=headers,
            files={'file': (Path(audio_path).name, fp)},
            data={
                'model': whisper_model,
                'prompt': DOMAIN_HINGLISH_PROMPT,
                'response_format': 'verbose_json',
            },
            timeout=120,
        )
    stt_resp.raise_for_status()
    stt_data = stt_resp.json()
    raw_text = (stt_data.get('text') or '').strip()
    whisper_lang = stt_data.get('language')
    segments = [
        {
            'start': round(float(s.get('start', 0)), 2),
            'end': round(float(s.get('end', 0)), 2),
            'text': str(s.get('text', '')).strip(),
        }
        for s in (stt_data.get('segments') or [])
        if str(s.get('text', '')).strip()
    ]
    formatted = _format_timestamped_transcript(segments) or raw_text

    if len(raw_text) < 6:
        local = analyze_transcript_locally(raw_text, segments=segments, whisper_lang=whisper_lang, call_context=call_context)
        return {
            'transcript_text': formatted,
            'transcript_segments': segments,
            'ai_summary': local['summary'],
            'ai_key_points': local['key_points'],
            'ai_objections': local['objections'],
            'ai_language': local['language'],
            'ai_sentiment': local['sentiment'],
            'ai_next_action': local['next_action'],
            'ai_provider': provider_label,
        }

    ctx_note = (
        f"Direction: {call_context.get('direction', 'OUTBOUND')}, "
        f"Contact: {call_context.get('lead_name') or call_context.get('phone_number') or 'Unknown'}, "
        f"Duration: {call_context.get('duration_seconds', 0)}s."
    )
    chat_resp = requests.post(
        f'{base_url}/chat/completions',
        headers={**headers, 'Content-Type': 'application/json'},
        json={
            'model': llm_model,
            'temperature': 0.2,
            'response_format': {'type': 'json_object'},
            'messages': [
                {'role': 'system', 'content': _CLOUD_SYSTEM_PROMPT},
                {'role': 'user', 'content': f'Context: {ctx_note}\n\nTranscript:\n{formatted}'},
            ],
        },
        timeout=90,
    )
    chat_resp.raise_for_status()
    content = chat_resp.json()['choices'][0]['message']['content']
    parsed = _extract_json_block(content)
    return _normalize_llm_payload(parsed, fallback_transcript=formatted, segments=segments, provider=provider_label)


# ---------------------------------------------------------------------------
# Public Orchestration API
# ---------------------------------------------------------------------------

def resolve_ai_provider() -> str:
    configured = (getattr(settings, 'AI_TRANSCRIBE_PROVIDER', 'auto') or 'auto').strip().lower()
    if configured in {'gemini', 'groq', 'openai', 'local'}:
        return configured
    if getattr(settings, 'AI_GEMINI_API_KEY', '').strip():
        return 'gemini'
    if getattr(settings, 'AI_GROQ_API_KEY', '').strip():
        return 'groq'
    if getattr(settings, 'AI_OPENAI_API_KEY', '').strip():
        return 'openai'
    return 'local'


def should_auto_queue_recording(recording) -> bool:
    if not getattr(settings, 'AI_TRANSCRIBE_AUTO_ENABLED', True):
        return False
    min_dur = int(getattr(settings, 'AI_TRANSCRIBE_MIN_DURATION_SECONDS', 5))
    dur = int(getattr(recording, 'duration_seconds', 0) or 0)
    size = int(getattr(recording, 'file_size', 0) or 0)
    if dur >= min_dur:
        return True
    # Allow unprobed recordings (duration=0) if audio file is non-trivial (>= 16 KB)
    return dur == 0 and size >= 16_000


def process_recording_ai(recording) -> dict[str, Any]:
    """Transcribe and summarize a CallRecording instance and persist structured insights."""
    from apps.calls.models import CallRecording

    provider = resolve_ai_provider()
    call = recording.call
    call_context = {
        'call_id': call.pk,
        'direction': getattr(call, 'direction', 'OUTBOUND'),
        'lead_name': call.lead.name if call.lead_id else '',
        'phone_number': call.phone_number or (call.lead.phone if call.lead_id else ''),
        'duration_seconds': recording.duration_seconds or call.duration_seconds or 0,
        'outcome': call.outcome,
    }

    tmp_audio_path = None
    try:
        # Resolve local filesystem path or copy storage bytes to temp file
        try:
            audio_path = recording.file.path
            if not os.path.isfile(audio_path):
                raise FileNotFoundError(audio_path)
        except (NotImplementedError, AttributeError, ValueError, FileNotFoundError):
            ext = recording.download_extension or '.m4a'
            with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as tmp:
                with recording.file.open('rb') as src:
                    shutil.copyfileobj(src, tmp)
                tmp_audio_path = tmp.name
                audio_path = tmp_audio_path

        if provider == 'gemini':
            result = _transcribe_and_summarize_gemini(audio_path, recording.content_type, call_context)
        elif provider == 'groq':
            result = _transcribe_openai_compatible(
                audio_path=audio_path,
                api_key=getattr(settings, 'AI_GROQ_API_KEY', '').strip(),
                base_url='https://api.groq.com/openai/v1',
                whisper_model=getattr(settings, 'AI_GROQ_WHISPER_MODEL', 'whisper-large-v3-turbo'),
                llm_model=getattr(settings, 'AI_GROQ_LLM_MODEL', 'llama-3.3-70b-versatile'),
                provider_label='groq-whisper+llama',
                call_context=call_context,
            )
        elif provider == 'openai':
            result = _transcribe_openai_compatible(
                audio_path=audio_path,
                api_key=getattr(settings, 'AI_OPENAI_API_KEY', '').strip(),
                base_url='https://api.openai.com/v1',
                whisper_model=getattr(settings, 'AI_OPENAI_WHISPER_MODEL', 'whisper-1'),
                llm_model=getattr(settings, 'AI_OPENAI_LLM_MODEL', 'gpt-4o-mini'),
                provider_label='openai-whisper+gpt4o-mini',
                call_context=call_context,
            )
        else:
            result = _transcribe_local_whisper(audio_path, call_context)

        recording.transcript_status = CallRecording.TranscriptStatus.COMPLETED
        recording.transcript_text = result['transcript_text']
        recording.transcript_segments = result['transcript_segments']
        recording.ai_summary = result['ai_summary']
        recording.ai_key_points = result['ai_key_points']
        recording.ai_objections = result['ai_objections']
        recording.ai_language = result['ai_language']
        recording.ai_sentiment = result['ai_sentiment']
        recording.ai_next_action = result['ai_next_action']
        recording.ai_provider = result['ai_provider']
        recording.transcript_error = ''
        recording.transcribed_at = timezone.now()
        recording.save(update_fields=[
            'transcript_status',
            'transcript_text',
            'transcript_segments',
            'ai_summary',
            'ai_key_points',
            'ai_objections',
            'ai_language',
            'ai_sentiment',
            'ai_next_action',
            'ai_provider',
            'transcript_error',
            'transcribed_at',
        ])
        logger.info(
            'AI_TRANSCRIBED recording_id=%s call_id=%s provider=%s lang=%s sentiment=%s objections=%s',
            recording.pk,
            recording.call_id,
            recording.ai_provider,
            recording.ai_language,
            recording.ai_sentiment,
            len(recording.ai_objections or []),
        )
        return result
    except Exception as exc:
        err_msg = f'{type(exc).__name__}: {exc}'[:1000]
        logger.exception('AI transcription failed for recording_id=%s call_id=%s: %s', recording.pk, recording.call_id, err_msg)
        recording.transcript_status = CallRecording.TranscriptStatus.FAILED
        recording.transcript_error = err_msg
        recording.save(update_fields=['transcript_status', 'transcript_error'])
        raise
    finally:
        if tmp_audio_path and os.path.exists(tmp_audio_path):
            try:
                os.unlink(tmp_audio_path)
            except OSError:
                pass
