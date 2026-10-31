# BidPilot AI v4.3 — payments, monthly plans aur account security

Is update mein v4.2 auto-bidding setup aur naya Gemini/OpenAI chatbot barqarar hain. Receiving accounts admin settings mein edit ho sakte hain:

| Method | Receiving number / IBAN |
| --- | --- |
| Easypaisa | 03128315518 |
| JazzCash | 03128315518 |
| NayaPay | PK78NAYA1234503128315518 |
| Meezan Bank | PK81MEZN0029040112662811 |

Dono supplied IBANs Pakistan ki 24-character length aur checksum validation pass karte hain. Yeh bank account ownership ya live payment settlement ka proof nahi. Bank account title admin apni verified details se fill kare; app koi title invent nahi karti. Admin payment settings mein receiving accounts change/disable ho sakte hain. Existing non-empty operator payment configuration preserve hoti hai jab tak admin saved settings usay replace na kare.

## Customer aur payment ka flow

1. Customer apna full name, email aur private password use karke account banaye. Password salted hash ke taur par store hota hai; payment ke liye admin ko password, PIN ya OTP dene ki zaroorat nahi.
2. Billing mein Starter **$10** ya Pro **$40** select kare. Local PKR transfer se pehle operator se exact amount/conversion confirm kare; app koi exchange rate invent nahi karti.
3. Payment ke baad method, transaction reference aur sender details submit kare. Customer account ka naam/email admin request par nazar aata hai. Payment reference submission **pending review** hai; usse plan active nahi hota.
4. Admin apne actual bank/merchant statement mein settlement verify karke **Approve & Activate** kare. App reviewer, decision aur access dates record karti hai. Repeated approval/reused references block hote hain. Approval kisi deliberate admin auto-bid pause ko remove nahi karta.
5. Naye paid grant ki activation date aur agle calendar month ki expiry automatic record hoti hai, Asia/Karachi timezone ke mutabiq. Misal: 15 January ko activation se 15 February; 31 January se February ke last day, aur agla renewal original billing day ko follow karta hai.

Same-tier renewal current expiry se agla month add karta hai aur current bid allowance window ko reset nahi karta. Upgrade/new grant apni recorded date aur selected tier ke mutabiq activate hota hai. Existing paid expiries upgrade ke waqt silently rewrite nahi hotin. Purane 30-day grants ka existing quota anchor preserve hota hai; naye grants calendar-month quota windows use karte hain.

Expiry boundary par paid AI, live scans aur bids time check se block hote hain. Worker periodically status ko expired record karta hai aur automatic bidding/greetings OFF karta hai; startup aur account/billing reads bhi expiry reconcile karte hain. Worker band ho tab bhi paid access expiry ke baad valid nahi hota. Founder accounts automatic monthly expiry se exempt rehte hain.

**Payment verification manual hai.** Is setup ke paas official merchant settlement API/signed webhook credentials nahi hain. Chatbot, screenshot ya customer ka transaction text bank settlement ka proof nahi maana jata. App is update mein payment confirmation emails send nahi karti; admin dashboard mein request aur grant details available hain.

## Security controls

- Account passwords hashes hain; admin/payment APIs unhein return nahi kartin. Settings ke Security panel se apna password change karein. Existing password required hai; successful change par sab sessions revoke ho jate hain aur dobara login chahiye.
- Logout-all control sirf apne account ke sessions revoke karta hai. Wrong-password attempts throttle hote hain. Account switch/pending-response guards previous account ke sensitive forms aur responses clear karte hain.
- Admin payment settings aur approval endpoints role-protected aur CSRF-protected hain. Account mismatch, invalid extra fields, duplicate approval aur payment-reference reuse reject hotay hain. Receiving IBAN/phone validation aur safe text rendering apply hoti hai.
- Gemini/OpenAI keys aur Freelancer tokens encrypted storage mein rehte hain. Settings exports credentials, roles aur billing restore nahi karte. Auto-bid limits, schedules, cooldown aur uncertain-send protection preserve rehte hain.
- Public hosting ke liye HTTPS, private database/network, protected `.env`, restricted logs aur database plus matching encryption-key backups maintain karein. Email ownership verification, merchant integration aur MFA is release mein implement nahi hain; inhein enabled security features na samjhein.

## Existing WSL installation update

Web aur worker dono terminals mein Ctrl+C karein. Database aur matching private `.env` ka backup lein; existing `FERNET_KEY` preserve karein. Naye additive columns/tables existing accounts aur encrypted values ko preserve karte hain; pehle backup copy par migration check karein.

```bash
cd ~/bidpilot-v2 &&
git pull --ff-only &&
cd BidPilot_AI_Team_Cloud &&
~/.local/bin/uv pip install --python .venv/bin/python -r requirements.txt &&
.venv/bin/python configure_autobid.py enable &&
.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Auto-bidding enable command tabhi chalayein jab aap operator switch ON karna chahte hain. Existing personal opt-ins apni jagah rehte hain; intended account ka verified Freelancer connection aur Auto-Pilot ON/Save alag required hain. Full enable/disable details [v4.2 guide](V4_2_AUTOBID_ROMAN_URDU.md) mein hain.

Doosri PowerShell window mein `wsl`, phir:

```bash
cd ~/bidpilot-v2/BidPilot_AI_Team_Cloud &&
.venv/bin/python -m app.worker
```

Browser `http://127.0.0.1:8000` par Ctrl+Shift+R karein. Billing mein receiving accounts aur plan dates, Team/Admin mein payment review/receiving settings, aur Settings mein account security dekhein. GitHub upload running website ko khud deploy nahi karta.

[Complete v4.3 ZIP](https://github.com/innointerface-design/-freelancer-auto-bidder/raw/refs/heads/main/downloads/BidPilot_AI_Updated_v4_3.zip) · [Verification report](REVIEW_AND_TESTS.md)
