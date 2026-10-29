"""Explainable skill matching, employer risk scoring, and Bidman-style AI fallback engine."""
import re
import time
import math
import unicodedata
from functools import lru_cache

SKILL_GROUPS = {
    'Short-form editing': ['short form', 'short-form', 'reels', 'tiktok', 'tik tok', 'youtube shorts', 'instagram reels', 'vertical video', 'shorts'],
    'Video editing': ['video editing', 'video editor', 'edit videos', 'video production', 'premiere pro', 'davinci', 'capcut', 'video content', 'post-production'],
    'Motion graphics': ['motion graphic', 'after effects', 'animated text', 'kinetic typography', 'logo animation', '2d animation', '3d motion', 'explainer video'],
    'AI video': ['ai video', 'ai-generated video', 'ai generated', 'veo', 'runway', 'kling', 'heygen', 'synthesia', 'elevenlabs', 'generative video', 'ai avatar', 'ai ugc', 'ai ads'],
    'Video ads': ['video ads', 'facebook ad', 'instagram ad', 'commercial', 'promotional video', 'product ad', 'ugc ad', 'social media ad', 'brand video'],
    'UI/UX & Web Design': ['ui/ux', 'ui ux', 'figma', 'user interface', 'user experience', 'web design', 'landing page', 'app design', 'wireframe', 'prototype'],
    'Real estate': ['real estate', 'property tour', 'real estate video', 'property listing'],
    'Captions & VFX': ['captions', 'subtitles', 'vfx', 'color grading', 'sound design', 'compositing', 'b-roll'],
}
PRIMARY = {'Short-form editing', 'Video editing', 'Motion graphics', 'AI video', 'Video ads', 'UI/UX & Web Design', 'Real estate'}
TARGET_COUNTRIES = {'US', 'USA', 'GB', 'UK', 'AU', 'CA', 'AE', 'DE', 'NL', 'SG'}
RISK_TERMS = ('telegram', 'whatsapp only', 'pay a registration', 'registration fee', 'deposit to start', 'upfront payment', 'send me money', 'crypto payment')


def parse_number(v):
    """Parse finite numeric data; booleans are not amounts or review counts."""
    if isinstance(v, bool):
        return None
    try:
        if v is not None and v != '':
            number = float(v)
            return number if math.isfinite(number) else None
    except (TypeError, ValueError, OverflowError):
        pass
    return None


def normalized_text(value):
    text = unicodedata.normalize('NFKC', str(value or '')).casefold()
    # Formatting characters and line breaks must not split excluded phrases.
    text = ''.join(c for c in text if unicodedata.category(c) != 'Cf')
    text = re.sub('[\u2010-\u2015\u2212]', '-', text)
    return re.sub(r'\s+', ' ', text).strip()


@lru_cache(maxsize=512)
def _term_pattern(term):
    # Match whole terms, allowing spaces/hyphens between words. Escaping each
    # component also keeps user-supplied exclusion terms out of regex syntax.
    parts = re.split(r'[\s_-]+', term)
    body = r'[\s_-]+'.join(re.escape(part) for part in parts)
    return re.compile(r'(?<![\w+#])' + body + r'(?![\w+#])')


def contains_term(text, term):
    term = normalized_text(term)
    return bool(term and any(c.isalnum() for c in term) and _term_pattern(term).search(normalized_text(text)))


def country_code(value):
    code = str(value or '').strip().upper()
    return {'UK': 'GB', 'USA': 'US'}.get(code, code)


def country_codes(value):
    return {country_code(item) for item in re.split(r'[,;\s]+', str(value or '')) if item.strip()}


def skill_text(value):
    """Accept the API's string/list forms without iterating strings as letters."""
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return str(value.get('name') or '')
    if isinstance(value, (list, tuple)):
        return ' '.join(skill_text(item) for item in value)
    return ''


def extract_client_requirements(title, description):
    """Deep heuristic parser that reads the client brief and extracts deliverables, tools, and pain points."""
    text = normalized_text(f"{title} {description}")
    deliverables = []
    if any(contains_term(text, k) for k in ('tiktok', 'reel', 'reels', 'short', 'shorts', 'vertical', '9:16')):
        deliverables.append('high-retention vertical short-form clips with dynamic hooks & synced captions')
    if any(contains_term(text, k) for k in ('ai', 'veo', 'runway', 'kling', 'heygen', 'elevenlabs', 'avatar', 'ugc')):
        deliverables.append('realistic AI-generated video sequences, voiceovers, and seamless post-production')
    if any(contains_term(text, k) for k in ('ad', 'ads', 'commercial', 'promo', 'shopify', 'roas', 'conversion', 'marketing')):
        deliverables.append('conversion-focused promotional video ads structured with strong 3-second scroll-stoppers')
    if any(contains_term(text, k) for k in ('motion', 'after effects', 'animation', 'kinetic', 'explainer', 'logo')):
        deliverables.append('custom After Effects motion graphics, smooth transitions, and kinetic typography')
    if any(contains_term(text, k) for k in ('ui', 'ux', 'figma', 'website', 'app', 'landing page', 'dashboard')):
        deliverables.append('modern, conversion-optimized UI/UX prototypes and responsive layouts in Figma')
    if any(contains_term(text, k) for k in ('real estate', 'property', 'villa', 'drone', 'tour')):
        deliverables.append('cinematic property tour grading, speed ramps, and luxury callout titles')
    if any(contains_term(text, k) for k in ('youtube', 'vlog', 'podcast', 'long form', 'documentary')):
        deliverables.append('engaging long-form storytelling, B-roll layering, color grading, and crisp audio mastering')
    if not deliverables:
        deliverables.append('high-precision execution tailored strictly to your brand guidelines and reference style')
    return deliverables


def job_score(job):
    title = str(job.get('title') or '')
    description = str(job.get('description') or '')
    haystack = normalized_text(title + ' ' + description + ' ' + skill_text(job.get('skills')))
    haystack = re.sub(r'\b(?:no|not looking for|without)\s+(?:any\s+)?(?:video editor|video editing|motion graphics|ai video)\s*(?:involved|needed|required)?\b', ' ', haystack)
    matched = [name for name, terms in SKILL_GROUPS.items() if any(contains_term(haystack, term) for term in terms)]
    core = [x for x in matched if x in PRIMARY]
    skill = min(48, 14 * len(core) + (8 if 'Captions & VFX' in matched else 0))
    if not core:
        skill = 0

    verification = job.get('payment_verified')
    completed = parse_number(job.get('client_reviews'))
    completed = max(0, completed) if completed is not None else None
    rating = parse_number(job.get('client_rating'))
    rating = rating if rating is not None and 0 <= rating <= 5 else None
    country = country_code(job.get('country_code'))
    trust = 10
    if verification is True:
        trust += 8
    elif verification is False:
        trust -= 4
    if completed is not None:
        trust += min(5, int(completed))
    if rating is not None:
        trust += 3 if rating >= 4.5 else (-4 if rating < 3 else 0)
    if country in TARGET_COUNTRIES:
        trust += 3
    trust = max(0, min(25, trust))

    min_budget = parse_number(job.get('budget_min'))
    max_budget = parse_number(job.get('budget_max'))
    typ = str(job.get('type') or '').lower()
    budget_value = max_budget if max_budget is not None else min_budget
    curr = str(job.get('currency') or 'USD').upper()
    if budget_value is None:
        budget = 6
    elif 'hour' in typ:
        budget = 15 if budget_value >= 20 else (10 if budget_value >= 10 else 4)
    else:
        budget = 15 if budget_value >= 100 else (12 if budget_value >= 40 else (7 if budget_value >= 15 else 3))

    words = len(description.split())
    clarity = min(10, (3 if words >= 12 else 1) + (4 if words >= 40 else 0) + (3 if any(x in haystack for x in ('deliverable', 'per video', 'seconds', 'weekly', 'deadline', 'footage', 'examples', 'reference')) else 0))
    posted = parse_number(job.get('posted_at'))
    now = time.time()
    hours = max(0, (now - posted) / 3600) if posted and 0 < posted <= now + 300 else None
    fresh = 5 if hours is not None and hours < 2 else (3 if hours is not None and hours < 24 else (1 if hours is not None and hours < 72 else 0))
    red_flags = [x for x in RISK_TERMS if contains_term(haystack, x)]
    penalty = 40 if red_flags else 0
    score = max(0, min(100, int(skill + trust + budget + clarity + fresh - penalty)))
    if red_flags:
        decision = 'Skip'
    elif not core:
        decision = 'Skip'
    elif score >= 70:
        decision = 'Bid now'
    elif score >= 45:
        decision = 'Review'
    else:
        decision = 'Skip'

    reasons = []
    if matched:
        reasons.append('Skill match: ' + ', '.join(matched[:4]))
    else:
        reasons.append('Weak match to your main services')
    if verification is True:
        reasons.append('Payment verified')
    elif verification is False:
        reasons.append('Payment not verified')
    else:
        reasons.append('Payment verification not specified')
    if country:
        reasons.append('Client country: ' + str(job.get('country') or country))
    if red_flags:
        reasons.append('Risk terms: ' + ', '.join(red_flags))
    return dict(
        score=score,
        decision=decision,
        matched=matched,
        reasons=reasons,
        breakdown=dict(skills=skill, trust=trust, budget=budget, clarity=clarity, freshness=fresh, risk_penalty=penalty)
    )
