"""100-point AI Lead Scoring & 5-tier Temperature Engine.

Computes a transparent 0-100 readiness score for every lead by combining:
  - AI call summaries, key points, transcripts, sentiment, and objections
  - Core buying-intent signals:
      * Asked about fees (+6)
      * Budget disclosed (+8)
      * Mentions timeline (+5)
      * Raised an objection (-3 per objection)
      * Attended a demo (+8)
  - Engagement depth, academic/course clarity, follow-ups, WhatsApp activity,
    and verified CRM milestones.

Maps scores to the exact 5-tier temperature rubric:
  * Hot:        80-100
  * Warm:       60-79
  * Interested: 40-59
  * Cold:       20-39
  * Inactive:   0-19
"""
from __future__ import annotations

from datetime import timedelta
import re
from typing import Any

from django.db.models import Prefetch
from django.utils import timezone


TEMPERATURE_TIERS: list[dict[str, Any]] = [
    {
        'key': 'HOT',
        'label': 'Hot',
        'range': '80–100',
        'min': 80,
        'max': 100,
        'color': '#d61f3c',
        'bg': 'rgba(214, 31, 60, 0.12)',
        'text_dark': '#fb7185',
        'text_light': '#be123c',
    },
    {
        'key': 'WARM',
        'label': 'Warm',
        'range': '60–79',
        'min': 60,
        'max': 79,
        'color': '#e5971a',
        'bg': 'rgba(229, 151, 26, 0.14)',
        'text_dark': '#fbbf24',
        'text_light': '#b45309',
    },
    {
        'key': 'INTERESTED',
        'label': 'Interested',
        'range': '40–59',
        'min': 40,
        'max': 59,
        'color': '#2558a8',
        'bg': 'rgba(37, 88, 168, 0.13)',
        'text_dark': '#60a5fa',
        'text_light': '#1d4ed8',
    },
    {
        'key': 'COLD',
        'label': 'Cold',
        'range': '20–39',
        'min': 20,
        'max': 39,
        'color': '#7c3aed',
        'bg': 'rgba(124, 58, 237, 0.13)',
        'text_dark': '#a78bfa',
        'text_light': '#6d28d9',
    },
    {
        'key': 'INACTIVE',
        'label': 'Inactive',
        'range': '0–19',
        'min': 0,
        'max': 19,
        'color': '#8c929d',
        'bg': 'rgba(140, 146, 157, 0.14)',
        'text_dark': '#cbd5e1',
        'text_light': '#475569',
    },
]

TEMPERATURE_META: dict[str, dict[str, Any]] = {tier['key']: tier for tier in TEMPERATURE_TIERS}

CORE_RUBRIC_SIGNALS: list[dict[str, Any]] = [
    {'code': 'ASKED_FEES', 'label': 'Asked about fees', 'delta': 6},
    {'code': 'BUDGET_DISCLOSED', 'label': 'Budget disclosed', 'delta': 8},
    {'code': 'MENTIONS_TIMELINE', 'label': 'Mentions timeline', 'delta': 5},
    {'code': 'RAISED_OBJECTION', 'label': 'Raised an objection', 'delta': -3},
    {'code': 'ATTENDED_DEMO', 'label': 'Attended a demo', 'delta': 8},
]

# ---------------------------------------------------------------------------
# Signal detection patterns (English + Romanized Hinglish + Hindi)
# ---------------------------------------------------------------------------

_FEES_RE = re.compile(
    r'\b(fees?|tuition|fee\s*structure|cost|charges?|package|scholarship|installments?|'
    r'hostel\s*fee|total\s*expense|kitna\s*lag(ta|ega|e)|kitni\s*fee|kharch|paisa|paise)\b',
    re.IGNORECASE,
)

_BUDGET_RE = re.compile(
    r'(\bbudget\b|\bafford(?:able)?\b|\bunder\s*\d+|\bupto\s*\d+|'
    r'\b\d+(?:\.\d+)?\s*(?:lakh|lakhs|lac|lacs|cr|crore|thousand|k\b|usd|\$)|'
    r'(?:₹|rs\.?|inr)\s*\d+)',
    re.IGNORECASE,
)

_TIMELINE_RE = re.compile(
    r'\b(202[5-9]|2030|this\s*year|next\s*year|next\s*month|this\s*month|this\s*session|'
    r'intake|september|october|november|january|february|march|april|may|june|july|august|'
    r'urgent|immediately|soon|asap|counselling\s*round|after\s*result|abhi\s*lena|is\s*saal)\b',
    re.IGNORECASE,
)

_DEMO_TEXT_RE = re.compile(
    r'\b(attended\s*(?:a\s*)?demo|demo\s*(?:done|completed|attended)|counselling\s*(?:done|completed|attended)|'
    r'visited\s*office|office\s*visit\s*done|google\s*meet\s*(?:done|completed)|walk[\s-]*in\s*done)\b',
    re.IGNORECASE,
)

_ACADEMIC_RE = re.compile(
    r'\b(neet|pcb|scorecard|score\s*card|\d{2,3}\s*marks|\d{2,3}\s*score|'
    r'12th|10th|twelfth|percentage|cgpa|mbbs\s*qualif|qualified|rank|dropper|attempt)\b',
    re.IGNORECASE,
)

_COURSE_RE = re.compile(
    r'\b(mbbs|mba|b\.?tech|btech|bba|bca|md|ms|bds|bams|bhms|nursing|pharmacy|abroad|'
    r'russia|georgia|kazakhstan|uzbekistan|nepal|bangladesh|philippines|egypt|india|'
    r'patna|delhi|bangalore|pune|kota|sikar|medical\s*college|engineering|management)\b',
    re.IGNORECASE,
)

_VOICEMAIL_RE = re.compile(
    r'\b(voicemail|not\s*answering|switched\s*off|out\s*of\s*reach|unreachable|'
    r'forwarded\s*to\s*voicemail|busy\s*on\s*another\s*call|no\s*speech\s*detected)\b',
    re.IGNORECASE,
)

_DISQUALIFIED_STATUSES = {
    'NOT_INTERESTED',
    'WRONG_NUMBER',
    'NO_CANDIDATE',
    'ADMISSION_DONE_BY_OTHER_CONSULTANCY',
}

_POSITIVE_STATUSES = {
    'INTERESTED',
    'CALL_BACK',
    'FOLLOW_UP_REQUIRED',
    'ADMISSION_DONE',
    'CONVERTED',
}


def score_to_temperature(score: int) -> str:
    """Map numeric 0-100 score to the exact 5-tier temperature key."""
    s = max(0, min(100, int(score or 0)))
    if s >= 80:
        return 'HOT'
    if s >= 60:
        return 'WARM'
    if s >= 40:
        return 'INTERESTED'
    if s >= 20:
        return 'COLD'
    return 'INACTIVE'


def _collect_text_blobs(lead, calls: list[Any]) -> tuple[str, list[dict[str, Any]], list[str]]:
    """Gather all AI summaries, key points, transcripts, notes, objections, and sentiments."""
    chunks: list[str] = []
    objections: list[dict[str, Any]] = []
    sentiments: list[str] = []

    if getattr(lead, 'notes', ''):
        chunks.append(lead.notes)
    if getattr(lead, 'preferred_intake', ''):
        chunks.append(f'intake {lead.preferred_intake}')

    for call in calls:
        if call.notes:
            chunks.append(call.notes)
        rec = getattr(call, 'recording', None)
        if rec is None:
            continue
        if rec.ai_summary:
            chunks.append(rec.ai_summary)
        if isinstance(rec.ai_key_points, list):
            for pt in rec.ai_key_points:
                if pt:
                    chunks.append(str(pt))
        if rec.transcript_text:
            chunks.append(rec.transcript_text[:4000])
        if isinstance(rec.ai_objections, list):
            for obj in rec.ai_objections:
                if isinstance(obj, dict):
                    objections.append(obj)
                elif isinstance(obj, str) and obj.strip():
                    objections.append({'label': obj.strip(), 'detail': ''})
        if rec.ai_sentiment:
            sentiments.append(rec.ai_sentiment)

    return '\n'.join(chunks), objections, sentiments


def compute_lead_score(lead, *, now=None) -> dict[str, Any]:
    """Compute 0-100 score, 5-tier temperature, and structured signal breakdown for `lead`."""
    current_time = now or timezone.now()

    calls = list(
        lead.calls.select_related('recording').order_by('-started_at', '-pk')
    )
    counsellings_count = lead.counsellings.count()
    admissions_count = lead.admissions.count()
    whatsapp_count = lead.whatsapp_activities.count()
    followups = list(lead.followups.all())
    has_counselor_assignment = (
        hasattr(lead, 'counselor_assignments') and lead.counselor_assignments.exists()
    )

    # Check verified counselling milestone if performance app is present
    has_demo_milestone = False
    try:
        has_demo_milestone = lead.performance_milestones.filter(
            event__in=['COUNSELLING', 'COUNSELLING_COMPLETED']
        ).exists()
    except Exception:
        has_demo_milestone = False

    combined_text, objections, sentiments = _collect_text_blobs(lead, calls)
    breakdown: list[dict[str, Any]] = []
    raw_score = 0

    def add_signal(code: str, label: str, delta: int, detail: str, *, core: bool = False):
        nonlocal raw_score
        raw_score += delta
        breakdown.append({
            'code': code,
            'label': label,
            'delta': delta,
            'detail': detail,
            'core': core,
        })

    # ------------------------------------------------------------------
    # 1. Core 5 Rubric Signals (matching screenshot verbatim)
    # ------------------------------------------------------------------
    if _FEES_RE.search(combined_text):
        add_signal(
            'ASKED_FEES',
            'Asked about fees',
            6,
            'Fee structure, tuition, or scholarship discussed in call/AI summary',
            core=True,
        )

    if _BUDGET_RE.search(combined_text):
        add_signal(
            'BUDGET_DISCLOSED',
            'Budget disclosed',
            8,
            'Lead shared a specific budget range or affordability figure',
            core=True,
        )

    has_timeline = bool(
        (lead.preferred_intake or '').strip()
        or any(getattr(c, 'expected_admission_year', None) for c in calls)
        or _TIMELINE_RE.search(combined_text)
    )
    if has_timeline:
        intake_note = lead.preferred_intake.strip() if (lead.preferred_intake or '').strip() else 'Admission timeline mentioned in conversation'
        add_signal(
            'MENTIONS_TIMELINE',
            'Mentions timeline',
            5,
            intake_note,
            core=True,
        )

    if objections:
        # Cap total objection deduction at -15 (5 objections x -3) while reflecting exact -3 per objection
        effective_count = min(len(objections), 5)
        obj_delta = -3 * effective_count
        labels = [str(o.get('label') or o.get('category') or 'Objection') for o in objections[:3]]
        detail = f"{len(objections)} objection{'s' if len(objections) != 1 else ''}: {', '.join(labels)}"
        add_signal(
            'RAISED_OBJECTION',
            'Raised an objection',
            obj_delta,
            detail,
            core=True,
        )

    attended_demo = bool(
        counsellings_count > 0
        or has_demo_milestone
        or _DEMO_TEXT_RE.search(combined_text)
    )
    if attended_demo:
        demo_detail = (
            f'{counsellings_count} counselling / demo session{"s" if counsellings_count != 1 else ""} logged'
            if counsellings_count > 0
            else 'Demo / counselling session confirmed'
        )
        add_signal(
            'ATTENDED_DEMO',
            'Attended a demo',
            8,
            demo_detail,
            core=True,
        )

    # ------------------------------------------------------------------
    # 2. Conversation Depth & Multi-Channel Engagement Signals
    # ------------------------------------------------------------------
    connected_calls = []
    inbound_calls = 0
    total_talk_seconds = 0
    max_call_seconds = 0
    for c in calls:
        rec = getattr(c, 'recording', None)
        dur = max(int(c.duration_seconds or 0), int(getattr(rec, 'duration_seconds', 0) or 0))
        total_talk_seconds += dur
        if dur > max_call_seconds:
            max_call_seconds = dur
        if getattr(c, 'direction', '') == 'INBOUND':
            inbound_calls += 1

        rec_summary = (getattr(rec, 'ai_summary', '') or '').strip()
        is_voicemail_only = bool(rec_summary and _VOICEMAIL_RE.search(rec_summary) and dur < 20)
        is_live = (
            (dur >= 15 and not is_voicemail_only)
            or (c.outcome in _POSITIVE_STATUSES)
            or (rec is not None and rec.transcript_status == 'COMPLETED' and len((rec.transcript_text or '').strip()) >= 35 and not is_voicemail_only)
        )
        if is_live:
            connected_calls.append(c)

    if connected_calls:
        add_signal(
            'CONNECTED_CALL',
            'Live conversation connected',
            16,
            f'{len(connected_calls)} connected call{"s" if len(connected_calls) != 1 else ""} ({total_talk_seconds}s talk time)',
        )
        if max_call_seconds >= 60 or total_talk_seconds >= 90:
            add_signal(
                'DEEP_CONVERSATION',
                'Deep conversation (60s+)',
                8,
                f'Longest call {max_call_seconds}s · Total {total_talk_seconds}s',
            )
        if len(connected_calls) >= 2:
            add_signal(
                'MULTI_TOUCH',
                'Multi-call engagement',
                6,
                f'{len(connected_calls)} live conversations recorded',
            )
    elif calls:
        add_signal(
            'VOICEMAIL_ONLY',
            'No live conversation yet',
            -5,
            f'{len(calls)} call attempt{"s" if len(calls) != 1 else ""} without a live conversation',
        )

    if inbound_calls > 0:
        add_signal(
            'INBOUND_CALLBACK',
            'Inbound callback from lead',
            10,
            f'Lead called back {inbound_calls} time{"s" if inbound_calls != 1 else ""}',
        )

    # AI Sentiment (use most recent analyzed recording's sentiment, preferring positive if multiple)
    if sentiments:
        latest_sentiment = sentiments[0]
        if latest_sentiment == 'POSITIVE':
            add_signal('AI_SENTIMENT', 'Positive AI call sentiment', 10, 'AI rated the latest call sentiment as Positive')
        elif latest_sentiment == 'NEUTRAL':
            add_signal('AI_SENTIMENT', 'Neutral AI call sentiment', 4, 'AI rated the latest call sentiment as Neutral')
        elif latest_sentiment == 'HESITANT':
            add_signal('AI_SENTIMENT', 'Hesitant AI call sentiment', 1, 'Lead sounded cautious / hesitant on the call')
        elif latest_sentiment == 'NEGATIVE':
            add_signal('AI_SENTIMENT', 'Negative AI call sentiment', -10, 'AI flagged negative sentiment on the latest call')

    # Academic & Course Qualification Signals
    has_academic = bool(
        (lead.neet_status or '').strip()
        or lead.pcb_percentage is not None
        or (lead.college or '').strip()
        or _ACADEMIC_RE.search(combined_text)
    )
    if has_academic:
        add_signal(
            'ACADEMIC_DETAILS',
            'Academic / NEET profile shared',
            10,
            'NEET status, scores, marks, or college preference recorded',
        )

    has_course_clarity = bool(
        lead.preferred_course
        or (lead.preferred_course_custom or '').strip()
        or (lead.location or '').strip()
        or any(getattr(c, 'selected_course', None) for c in calls)
        or _COURSE_RE.search(combined_text)
    )
    if has_course_clarity:
        course_label = lead.preferred_course_label or lead.location or 'Course & destination discussed'
        add_signal(
            'COURSE_CLARITY',
            'Course & location clarity',
            7,
            str(course_label)[:120],
        )

    # Follow-up / WhatsApp / Counsellor engagement
    has_active_followup = any(f.status in ('PENDING', 'COMPLETED') for f in followups)
    if has_active_followup or whatsapp_count > 0 or has_counselor_assignment:
        parts = []
        if has_active_followup:
            parts.append(f'{len(followups)} follow-up{"s" if len(followups) != 1 else ""}')
        if whatsapp_count > 0:
            parts.append(f'{whatsapp_count} WhatsApp')
        if has_counselor_assignment:
            parts.append('Counsellor assigned')
        add_signal(
            'NEXT_STEP_ENGAGED',
            'Follow-up / WhatsApp engaged',
            8,
            ' · '.join(parts),
        )

    # ------------------------------------------------------------------
    # 3. CRM Stage Boost / Disqualification & Inactivity Decay
    # ------------------------------------------------------------------
    is_admitted = lead.status in ('ADMISSION_DONE', 'CONVERTED') or admissions_count > 0
    if is_admitted:
        add_signal(
            'CRM_ADMITTED',
            'Admission / conversion milestone',
            20,
            f'Lead status: {lead.get_status_display()}',
        )
    elif lead.status == 'INTERESTED':
        add_signal('CRM_INTENT', 'Marked Interested in CRM', 12, 'Caller confirmed active admission interest')
    elif lead.status in ('FOLLOW_UP_REQUIRED', 'CALL_BACK'):
        add_signal('CRM_INTENT', 'Active follow-up stage', 8, f'Stage: {lead.get_status_display()}')
    elif lead.status == 'CALLED':
        add_signal('CRM_INTENT', 'Initial contact logged', 4, 'Lead has been contacted by caller')
    elif lead.status in _DISQUALIFIED_STATUSES:
        add_signal(
            'CRM_DISQUALIFIED',
            f'Disqualified ({lead.get_status_display()})',
            -25,
            'Lead marked not interested, wrong number, or enrolled elsewhere',
        )

    # Inactivity decay (>14 days since latest activity and no upcoming pending follow-up)
    if not is_admitted:
        latest_touch = lead.created_at
        if calls and calls[0].started_at and (latest_touch is None or calls[0].started_at > latest_touch):
            latest_touch = calls[0].started_at
        has_future_followup = any(
            f.status == 'PENDING' and f.scheduled_at and f.scheduled_at >= current_time - timedelta(days=2)
            for f in followups
        )
        if latest_touch and (current_time - latest_touch) > timedelta(days=14) and not has_future_followup and raw_score > 0:
            add_signal(
                'INACTIVITY_DECAY',
                'No recent activity (14+ days)',
                -8,
                'Score decayed due to inactivity without scheduled follow-up',
            )

    # Final clamping & floor/cap safeguards
    final_score = max(0, min(100, raw_score))
    if is_admitted and final_score < 88:
        final_score = 88
    if lead.status in _DISQUALIFIED_STATUSES and not is_admitted and final_score > 25:
        final_score = min(final_score, 25)

    temperature = score_to_temperature(final_score)
    return {
        'score': final_score,
        'temperature': temperature,
        'temperature_meta': TEMPERATURE_META[temperature],
        'breakdown': breakdown,
        'scored_at': current_time,
    }


def refresh_lead_score(lead, *, save: bool = True, now=None) -> dict[str, Any]:
    """Recompute and optionally persist `ai_score`, `ai_temperature`, and `ai_score_breakdown`."""
    result = compute_lead_score(lead, now=now)
    lead.ai_score = result['score']
    lead.ai_temperature = result['temperature']
    lead.ai_score_breakdown = result['breakdown']
    lead.ai_scored_at = result['scored_at']
    if save and lead.pk:
        lead.__class__.objects.filter(pk=lead.pk).update(
            ai_score=lead.ai_score,
            ai_temperature=lead.ai_temperature,
            ai_score_breakdown=lead.ai_score_breakdown,
            ai_scored_at=lead.ai_scored_at,
        )
    return result


def backfill_lead_scores(queryset=None, *, batch_size: int = 200) -> dict[str, int]:
    """Recalculate scores for all leads in `queryset` and return tier distribution counts."""
    from .models import Lead

    qs = queryset if queryset is not None else Lead.objects.all()
    counts = {tier['key']: 0 for tier in TEMPERATURE_TIERS}
    updated_batch: list[Lead] = []
    now = timezone.now()

    for lead in qs.iterator(chunk_size=batch_size):
        res = compute_lead_score(lead, now=now)
        lead.ai_score = res['score']
        lead.ai_temperature = res['temperature']
        lead.ai_score_breakdown = res['breakdown']
        lead.ai_scored_at = res['scored_at']
        counts[res['temperature']] += 1
        updated_batch.append(lead)
        if len(updated_batch) >= batch_size:
            Lead.objects.bulk_update(
                updated_batch,
                ['ai_score', 'ai_temperature', 'ai_score_breakdown', 'ai_scored_at'],
            )
            updated_batch.clear()

    if updated_batch:
        Lead.objects.bulk_update(
            updated_batch,
            ['ai_score', 'ai_temperature', 'ai_score_breakdown', 'ai_scored_at'],
        )
    return counts
