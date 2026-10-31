"""Admin-owned payment destinations; customer references still require settlement review."""
import json
import os
import re
import time
from urllib.parse import urlsplit

from fastapi import Depends, HTTPException
from pydantic import BaseModel, Field, field_validator, model_validator
from sqlalchemy import Column, Integer, Text

from .db import Base, audit


class PaymentConfiguration(Base):
    __tablename__ = 'payment_configurations'
    id = Column(Integer, primary_key=True)
    payload = Column(Text, nullable=False)
    updated_at = Column(Integer, nullable=False, default=0)
    updated_by = Column(Integer, nullable=False)


def _text(value, *, multiline=False):
    if any(ord(char) < 32 and (not multiline or char not in '\r\n\t') for char in value) or '\x7f' in value:
        raise ValueError('Control characters are not allowed')
    return value.strip()


def valid_pk_iban(value, bank_code=None):
    if not re.fullmatch(r'PK[0-9]{2}[A-Z]{4}[0-9]{16}', value):
        return False
    if bank_code and value[4:8] != bank_code:
        return False
    rotated = value[4:] + value[:4]
    digits = ''.join(str(ord(char) - 55) if char.isalpha() else char for char in rotated)
    return int(digits) % 97 == 1


class StrictInput(BaseModel):
    model_config = {'extra': 'forbid'}


class PhoneDestination(StrictInput):
    enabled: bool = Field(default=False, strict=True)
    number: str = Field(default='', max_length=40)
    account_title: str = Field(default='', max_length=120)

    @field_validator('number')
    @classmethod
    def normalize_number(cls, value):
        value = _text(value)
        compact = re.sub(r'[ ()+-]', '', value)
        if compact.startswith('92'):
            compact = '0' + compact[2:]
        return compact

    @field_validator('account_title')
    @classmethod
    def title(cls, value):
        return _text(value)

    @model_validator(mode='after')
    def enabled_number(self):
        if self.enabled and not re.fullmatch(r'03[0-9]{9}', self.number):
            raise ValueError('An enabled wallet needs a complete Pakistan mobile number')
        return self


class IbanDestination(StrictInput):
    enabled: bool = Field(default=False, strict=True)
    iban: str = Field(default='', max_length=80)
    account_title: str = Field(default='', max_length=120)

    @field_validator('iban')
    @classmethod
    def normalize_iban(cls, value):
        return _text(value).replace(' ', '').upper()

    @field_validator('account_title')
    @classmethod
    def title(cls, value):
        return _text(value)


class LegacyBankDestination(StrictInput):
    enabled: bool = Field(default=False, strict=True)
    details: str = Field(default='', max_length=800)

    @field_validator('details')
    @classmethod
    def clean(cls, value):
        return _text(value, multiline=True)

    @model_validator(mode='after')
    def nonempty(self):
        if self.enabled and not self.details:
            raise ValueError('An enabled bank transfer needs payment details')
        return self


class StripeDestination(StrictInput):
    enabled: bool = Field(default=False, strict=True)
    url: str = Field(default='', max_length=1000)

    @field_validator('url')
    @classmethod
    def clean(cls, value):
        return _text(value)

    @model_validator(mode='after')
    def safe_link(self):
        if self.enabled:
            try:
                parsed = urlsplit(self.url)
                allowed = (parsed.scheme == 'https' and parsed.hostname
                           and not parsed.username and not parsed.password
                           and not any(char.isspace() for char in self.url)
                           and '\\' not in self.url and parsed.port in (None, 443))
            except ValueError:
                allowed = False
            if not allowed:
                raise ValueError('An enabled payment link must use a valid HTTPS address')
        return self


class PaymentSettings(StrictInput):
    jazzcash: PhoneDestination
    easypaisa: PhoneDestination
    nayapay: IbanDestination
    meezan: IbanDestination
    bank: LegacyBankDestination
    stripe: StripeDestination
    whatsapp: str = Field(default='', max_length=40)

    @field_validator('whatsapp')
    @classmethod
    def clean_contact(cls, value):
        value = _text(value)
        if value and not re.fullmatch(r'\+?[0-9]{7,15}', value):
            raise ValueError('WhatsApp contact must be a complete phone number')
        return value

    @model_validator(mode='after')
    def bank_identity(self):
        for field, code in (('nayapay', 'NAYA'), ('meezan', 'MEZN')):
            destination = getattr(self, field)
            if destination.enabled and not valid_pk_iban(destination.iban, code):
                raise ValueError('An enabled bank destination needs its valid 24-character Pakistan IBAN')
        return self


class PaymentSettingsIn(StrictInput):
    settings: PaymentSettings
    expected_user_id: int | None = Field(default=None, ge=1, strict=True)


def default_settings():
    return PaymentSettings.model_validate({
        'jazzcash': {'enabled': True, 'number': '03128315518'},
        'easypaisa': {'enabled': True, 'number': '03128315518'},
        'nayapay': {'enabled': True, 'iban': 'PK78NAYA1234503128315518'},
        'meezan': {'enabled': True, 'iban': 'PK81MEZN0029040112662811'},
        'bank': {'enabled': False}, 'stripe': {'enabled': False}, 'whatsapp': '',
    })


def _environment_values():
    names = {'jazzcash': 'PAYMENT_JAZZCASH', 'easypaisa': 'PAYMENT_EASYPAISA',
             'bank': 'PAYMENT_BANK', 'stripe': 'PAYMENT_STRIPE_URL', 'whatsapp': 'PAYMENT_WHATSAPP'}
    return {key: os.getenv(name, '').strip() for key, name in names.items()}


def _settings_and_row(session):
    row = session.get(PaymentConfiguration, 1)
    if row:
        try:
            return PaymentSettings.model_validate_json(row.payload), row, []
        except ValueError:
            return None, row, ['Stored payment settings are invalid. Review and save corrected settings.']
    settings = default_settings()
    environment = _environment_values()
    for name in ('jazzcash', 'easypaisa'):
        if environment[name]:
            try:
                setattr(settings, name, PhoneDestination(enabled=True, number=environment[name]))
            except ValueError:
                setattr(settings, name, PhoneDestination(enabled=False, number=environment[name][:40]))
    if environment['bank']:
        settings.bank = LegacyBankDestination(enabled=True, details=environment['bank'][:800])
    if environment['stripe']:
        try:
            settings.stripe = StripeDestination(enabled=True, url=environment['stripe'][:1000])
        except ValueError:
            settings.stripe = StripeDestination(enabled=False, url=environment['stripe'][:1000])
    if environment['whatsapp']:
        settings.whatsapp = environment['whatsapp'][:40]
    return settings, None, []


def public_payment_methods(session):
    settings, row, _ = _settings_and_row(session)
    public = {name: '' for name in ('jazzcash', 'easypaisa', 'nayapay', 'meezan', 'bank', 'stripe_url', 'whatsapp')}
    public.update(channels=[], verification_mode='manual')
    if settings is None:
        return public
    labels = {'jazzcash': 'JazzCash', 'easypaisa': 'Easypaisa', 'nayapay': 'NayaPay',
              'meezan': 'Meezan Bank', 'bank': 'Bank transfer', 'stripe': 'Payment link'}
    environment = _environment_values() if row is None else {}
    for name in labels:
        destination = getattr(settings, name)
        details = (destination.number if name in ('jazzcash', 'easypaisa') else
                   destination.iban if name in ('nayapay', 'meezan') else
                   destination.details if name == 'bank' else destination.url)
        # Legacy environment wallet strings may also contain an account title.
        # Preserve those explicitly configured destinations until the admin saves
        # the structured form. New/edited destinations use strict validation.
        legacy = environment.get(name, '') if name in ('jazzcash', 'easypaisa') else ''
        if legacy:
            details = legacy
        if not destination.enabled and not legacy:
            continue
        key = 'stripe_url' if name == 'stripe' else name
        public[key] = details
        public['channels'].append({'id': name, 'label': labels[name], 'details': details,
                                   'account_title': getattr(destination, 'account_title', '')})
    public['whatsapp'] = settings.whatsapp
    return public


def payment_method_configured(session, method):
    if method not in {'jazzcash', 'easypaisa', 'nayapay', 'meezan', 'bank', 'stripe'}:
        return False
    return bool(public_payment_methods(session)['stripe_url' if method == 'stripe' else method])


def _admin_response(session):
    settings, row, warnings = _settings_and_row(session)
    if settings is None:
        settings = default_settings()
        for name in ('jazzcash', 'easypaisa', 'nayapay', 'meezan', 'bank', 'stripe'):
            getattr(settings, name).enabled = False
    for name, code in (('nayapay', 'NAYA'), ('meezan', 'MEZN')):
        value = getattr(settings, name)
        if value.iban and not valid_pk_iban(value.iban, code):
            warnings.append(labels_warning(name))
    for name in ('jazzcash', 'easypaisa'):
        value = getattr(settings, name)
        if value.number and not re.fullmatch(r'03[0-9]{9}', value.number):
            warnings.append(name.title() + ' needs a valid mobile number before enabling it in saved settings.')
    return {'settings': settings.model_dump(), 'warnings': warnings,
            'updated_at': row.updated_at if row else 0, 'verification_mode': 'manual'}


def labels_warning(name):
    return ('NayaPay' if name == 'nayapay' else 'Meezan') + ' needs a valid 24-character IBAN before enabling it.'


def install_payment_settings_routes(app, current, mutate, admin_only):
    @app.get('/api/admin/payment-settings')
    def get_settings(expected_user_id: int | None = None, uid: int = Depends(current)):
        from . import main
        admin_only(uid)
        if expected_user_id is not None and expected_user_id != uid:
            raise HTTPException(409, 'Account changed. Reload before reviewing payment settings.')
        with main.SessionLocal() as session:
            return _admin_response(session)

    @app.post('/api/admin/payment-settings')
    def save_settings(values: PaymentSettingsIn, uid: int = Depends(mutate)):
        from . import main
        admin_only(uid)
        if values.expected_user_id is not None and values.expected_user_id != uid:
            raise HTTPException(409, 'Account changed. Reload before saving payment settings.')
        with main.SessionLocal() as session:
            main.lock_user(session, uid)
            row = session.get(PaymentConfiguration, 1)
            if row is None:
                row = PaymentConfiguration(id=1, payload='{}', updated_by=uid)
                session.add(row)
            row.payload = json.dumps(values.settings.model_dump(), ensure_ascii=False)
            row.updated_at = int(time.time())
            row.updated_by = uid
            audit(session, uid, 'payment_settings_updated', 'Payment destinations updated; settlement still requires admin verification')
            session.commit()
            return _admin_response(session)
