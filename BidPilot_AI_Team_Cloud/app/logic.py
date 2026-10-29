"""Bidman.co Pro Engine: 0-100 Smart Matching, Flexible Pricing Strategies, and Instant AI Proposal Builder."""
import re, time, math
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR
from .scoring_base import RISK_TERMS, parse_number as _parse_number, contains_term, normalized_text, skill_text, country_code, country_codes

ALIASES = {
    'video editing': ['video editor', 'video editing', 'edit video', 'video production', 'premiere pro', 'davinci resolve', 'capcut', 'final cut'],
    'short-form editing': ['short-form', 'short form', 'reels', 'youtube shorts', 'tiktok', 'vertical video', 'shorts', 'hormozi'],
    'motion graphics': ['motion graphics', 'after effects', 'kinetic typography', 'animation', '2d animation', 'explainer'],
    'ai video': ['ai video', 'ai ads', 'ai avatar', 'ai ugc', 'generative video', 'veo', 'runway', 'kling', 'heygen', 'elevenlabs', 'midjourney'],
    'video ads': ['video ads', 'product ad', 'brand ad', 'promotional', 'commercial', 'advertising video', 'ugc ad', 'facebook ad'],
    'real estate video': ['real estate', 'property tour', 'property video', 'real estate video', 'drone edit'],
    'ui/ux design': ['ui/ux', 'ui ux', 'figma', 'web design', 'app design', 'user interface', 'wireframe', 'prototype'],
    'graphic design': ['graphic design', 'photoshop', 'branding', 'logo design', 'thumbnail', 'illustrator'],
    'web development': ['web development', 'react', 'frontend', 'javascript', 'website', 'wordpress', 'shopify', 'html'],
    'python': ['python', 'backend', 'django', 'fastapi', 'automation', 'bot'],
    'seo': ['seo', 'search engine optimization', 'youtube seo'],
}


def parse_number(value):
    """Remote numeric fields must be finite before scoring or pricing."""
    result = _parse_number(value)
    return result if result is not None and math.isfinite(result) else None


def words(v):
    return list(dict.fromkeys(normalized_text(x) for x in re.split(r'[,;\n]+', str(v or '')) if x.strip()))


def score_job(job, skills, countries, excluded_keywords=''):
    title = normalized_text(job.get('title'))
    description = normalized_text(job.get('description'))
    text = title + ' ' + description + ' ' + skill_text(job.get('skills'))
    user_skills = words(skills)
    matched = []
    for skill in user_skills:
        variants = [skill] + ALIASES.get(skill, [])
        if any(contains_term(text, v) for v in variants):
            matched.append(skill)

    quality = sum(2 if any(contains_term(title, v) for v in [s] + ALIASES.get(s, [])) else 1 for s in matched)
    skill_pts = min(52, quality * 14)

    custom_excluded = words(excluded_keywords)
    all_risks = sorted(set(list(RISK_TERMS) + custom_excluded))
    risks = [x for x in all_risks if contains_term(text, x)]

    pay = job.get('payment_verified')
    review = parse_number(job.get('client_reviews'))
    review = max(0, review) if review is not None else None
    rating = parse_number(job.get('client_rating'))
    rating = rating if rating is not None and 0 <= rating <= 5 else None
    country = country_code(job.get('country_code'))
    targets = country_codes(countries)

    trust = 10
    if pay is True:
        trust += 8
    elif pay is False:
        trust -= 3
    if review is not None:
        trust += min(5, int(review))
    if rating is not None:
        trust += 3 if rating >= 4.5 else (-3 if rating < 3 else 0)
    if country and country in targets:
        trust += 3
    trust = max(0, min(25, trust))

    cur = str(job.get('currency') or 'USD').upper()
    high = parse_number(job.get('budget_max'))
    low = parse_number(job.get('budget_min'))
    cost = high if high is not None else low
    if cost is None:
        budget = 8
    elif 'hour' in str(job.get('type') or '').lower():
        budget = 14 if cost >= 15 else 8
    else:
        budget = 15 if cost >= 80 else (11 if cost >= 25 else 5)

    detail = 10 if len(description.split()) > 50 else (7 if len(description.split()) >= 18 else 3)
    posted = parse_number(job.get('posted_at'))
    now = time.time()
    age = max(0, now - posted) if posted and 0 < posted <= now + 300 else None
    fresh = 5 if age is not None and age < 7200 else (3 if age is not None and age < 86400 else 1)

    score = max(0, min(100, skill_pts + trust + budget + detail + fresh - (40 if risks else 0)))
    verdict = 'Skip' if risks or not matched or score < 42 else ('Strong match' if score >= 68 else 'Review')

    reasons = [
        f'Skills matched: {", ".join(matched)}' if matched else 'No direct profile-skill match',
        'Payment verified' if pay is True else ('Payment not verified' if pay is False else 'Payment status unverified/new client'),
        f'Employer country: {job.get("country") or country}' if country else 'Employer country global/unspecified',
        f'Client reviews: {int(review)}' if review is not None else 'Employer review count: New/0'
    ]
    if risks:
        reasons.append('Blocked/Red flag terms: ' + ', '.join(risks))

    return {
        'score': score,
        'decision': verdict,
        'matched': matched,
        'reasons': reasons,
        'breakdown': {
            'skills': skill_pts,
            'client': trust,
            'budget': budget,
            'clarity': detail,
            'freshness': fresh,
            'penalty': 40 if risks else 0
        }
    }


def make_proposal(job, user):
    """Build an editable local template from the user's supplied profile."""
    title = str(job.get('title') or 'your project').strip()[:180]
    client_name = str(job.get('client_name') or '').strip()[:80]
    greeting = f"Hi {client_name}," if client_name and client_name.lower() not in ('employer', 'client', 'demo employer', 'demo client') else "Hi there,"
    description = str(getattr(user, 'description', '') or '').strip()
    portfolio = str(getattr(user, 'portfolio', '') or '').strip()
    days = getattr(user, 'default_days', 3) or 3
    tone = getattr(user, 'proposal_tone', 'winning_direct')
    # Do not invent experience, tools, same-day availability, or free work.
    profile = f"\n\n{description}" if description else ''
    portfolio_line = f"\n\nPortfolio: {portfolio}" if portfolio else ''
    if tone == 'consultative':
        opening = f'I reviewed your brief for "{title}". I would like to clarify the deliverables and acceptance criteria before starting.'
        approach = 'My proposed process is to agree the scope, share a first draft for feedback, and then complete the agreed deliverables.'
    elif tone == 'agency_pitch':
        opening = f'We would like to discuss your project, "{title}".'
        approach = 'We suggest agreeing the scope and milestones first, then sharing progress for review before final delivery.'
    elif tone == 'friendly_expert':
        opening = f'Thanks for sharing "{title}". I would be happy to discuss the details with you.'
        approach = 'I suggest confirming the reference material, deliverables, and review stages before work begins.'
    else:
        opening = f'I am interested in your project, "{title}".'
        approach = 'I propose confirming the requirements first, then completing the agreed deliverables with a review stage.'
    body = (
        f'{greeting}\n\n{opening}\n\n{approach}{profile}\n\n'
        f'My proposed timeline is {days} days, subject to confirming the scope and receiving the required materials.'
        f'{portfolio_line}\n\nCould you share any references and your preferred deadline?\n\n'
        f'Best regards,\n{getattr(user, "name", "")}'
    )
    return body[:2800]


def price_for(job, user):
    """Choose a price inside both project bounds and the user's hard bounds.

    Amounts use the listing currency. No exchange-rate conversion is implied.
    An empty or invalid range has no safe suggested price.
    """
    minimum = parse_number(job.get('budget_min'))
    maximum = parse_number(job.get('budget_max'))
    floor = parse_number(getattr(user, 'min_budget', None))
    cap = parse_number(getattr(user, 'max_bid', None))
    for name in ('budget_min', 'budget_max'):
        raw = job.get(name)
        if raw not in (None, '') and (parse_number(raw) is None or parse_number(raw) < 0):
            return None
    if floor is None or cap is None or floor <= 0 or cap <= 0:
        return None
    if minimum is not None and maximum is not None and minimum > maximum:
        return None
    lower = max(0.01, floor, minimum if minimum is not None else 0.01)
    upper = min(cap, maximum if maximum is not None else cap)
    if lower > upper or not math.isfinite(lower * 100) or not math.isfinite(upper * 100):
        return None
    # Round towards the permitted interval, never beyond a hard bound.
    lower_cents = int((Decimal(str(lower)) * 100).to_integral_value(rounding=ROUND_CEILING))
    upper_cents = int((Decimal(str(upper)) * 100).to_integral_value(rounding=ROUND_FLOOR))
    if lower_cents > upper_cents:
        return None
    strategy = getattr(user, 'bid_strategy', 'average') or 'average'
    low = minimum if minimum is not None else lower
    high = maximum if maximum is not None else (minimum if minimum is not None else lower)
    if strategy == 'minimum':
        target = low
    elif strategy == 'maximum':
        target = high
    elif strategy == 'competitive_low':
        target = low + (high - low) * 0.25
    else:
        target = low + (high - low) / 2
    target = max(lower, min(upper, target))
    cents = max(lower_cents, min(upper_cents, round(target * 100)))
    return cents / 100.0


def auto_eligible(job, score, user, conn, platform_gate=True, existing=None, day_count=0, hour_count=0):
    """Fail closed before automated bids; the caller enforces atomic quotas."""
    from .db import subscription_active
    causes = []
    if not platform_gate:
        causes.append('Server auto-bid switch is disabled')
    if not getattr(user, 'active', False):
        causes.append('Account disabled')
    if not subscription_active(user):
        causes.append('Subscription inactive or expired')
    if not user.auto_admin_allowed:
        causes.append('Admin has paused auto-bidding for this account')
    if not user.auto_requested:
        causes.append('Auto-Pilot is turned OFF in your settings')
    if not conn:
        causes.append('Freelancer account not connected')
    else:
        owner_id = str(job.get('owner_id') or '').strip()
        freelancer_id = str(getattr(conn, 'freelancer_id', '') or '').strip()
        if owner_id and freelancer_id and owner_id == freelancer_id:
            causes.append('Cannot bid on your own project')
    if job.get('demo'):
        causes.append('Demo job (use Live Freelancer jobs for real auto-bids)')

    project_type = str(job.get('type') or '').strip().lower()
    if project_type not in ('fixed', 'hourly'):
        causes.append('Project pricing type is unknown')
    currency = str(job.get('currency') or '').strip().upper()
    if not re.fullmatch(r'[A-Z]{3}', currency):
        causes.append('Project currency is missing or invalid')
    if not getattr(user, 'allow_hourly', True) and project_type == 'hourly':
        causes.append('Hourly projects excluded by your filter')
    if not getattr(user, 'allow_any_currency', True) and currency != 'USD':
        causes.append('Non-USD currency excluded by your filter')
    if getattr(user, 'require_payment_verified', False) and job.get('payment_verified') is not True:
        causes.append('Client payment not verified')

    allowed_countries = country_codes(user.countries)
    job_country = country_code(job.get('country_code'))
    if getattr(user, 'require_country_match', False) and (not allowed_countries or job_country not in allowed_countries):
        causes.append('Client country not in your target list')

    fresh_score = score_job(job, user.skills, user.countries, getattr(user, 'excluded_keywords', ''))
    if fresh_score['decision'] == 'Skip':
        causes.append('Project blocked by skill, exclusion, or risk filters')
    numeric_score = parse_number(score)
    checked_score = min(numeric_score, fresh_score['score']) if numeric_score is not None else 0
    if checked_score < int(user.min_score or 60):
        causes.append(f'Score {checked_score} is below your minimum threshold ({user.min_score})')
    if existing:
        causes.append('Already bid or drafted on this project')

    max_daily = max(1, int(user.max_daily or 50))
    max_hourly = max(1, int(getattr(user, 'max_hourly', 15) or 15))
    if day_count >= max_daily:
        causes.append(f'Daily auto-bid limit reached ({max_daily}/day)')
    if hour_count >= max_hourly:
        causes.append(f'Hourly auto-bid limit reached ({max_hourly}/hr)')

    amount = price_for(job, user)
    if amount is None:
        causes.append('Project budget and your price limits do not overlap, or contain invalid values')
    if parse_number(job.get('budget_min')) is None and parse_number(job.get('budget_max')) is None:
        causes.append('Project budget is missing or invalid')
    if job.get('project_status') != 'active':
        causes.append('Project is closed or its active status is unknown')

    return (not causes, causes, amount)
