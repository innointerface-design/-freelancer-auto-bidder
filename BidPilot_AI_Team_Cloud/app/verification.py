"""Shared client-verification policy for lists, scoring, and bid dispatch.

Payment verification is required even when Freelancer supplies no status. Other
checks are informational; they do not move a payment-verified project to Blocked.
Profile completeness, deposits, reviews, and country are not verification flags.
"""


VERIFICATION_LABELS = {
    'payment_verified': 'Payment',
    'email_verified': 'Email',
    'identity_verified': 'Identity',
    'phone_verified': 'Phone',
}


def normalize_client_verifications(user):
    """Keep actual boolean UserStatus checks without inventing missing values."""
    user = user if isinstance(user, dict) else {}
    status = user.get('status')
    status = status if isinstance(status, dict) else {}
    checks = {}
    for key in VERIFICATION_LABELS:
        value = status.get(key)
        if not isinstance(value, bool):
            value = user.get(key)
        if isinstance(value, bool):
            checks[key] = value
    return checks


def classify_verification(job):
    """Classify normalized and legacy saved jobs with the same fail-closed rule.

    The top-level payment field remains canonical for older saved jobs. A nested
    payment failure also wins over a contradictory positive flag. Unknown
    optional checks are omitted; other supplied checks remain informational.
    """
    job = job if isinstance(job, dict) else {}
    supplied = job.get('client_verifications')
    supplied = supplied if isinstance(supplied, dict) else {}
    checks = {}
    reasons = []
    for key, label in VERIFICATION_LABELS.items():
        top = job.get(key)
        nested = supplied.get(key)
        if top is False or nested is False:
            value = False
        elif key == 'payment_verified':
            value = top if isinstance(top, bool) else None
        elif top is True or nested is True:
            value = True
        else:
            value = None
        if key == 'payment_verified' or value is not None:
            checks[key] = value
        if key == 'payment_verified' and value is False:
            reasons.append(f'Client {label.lower()} is unverified')
        elif key == 'payment_verified' and value is None:
            reasons.append('Client payment verification is unknown')
    return {'blocked': bool(reasons), 'reasons': reasons, 'checks': checks}


def verification_block_reasons(job):
    """Reasons suitable for a UI explanation or a server-side bid rejection."""
    return classify_verification(job)['reasons']
