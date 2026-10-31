# BidPilot AI v4.2 — auto-bidding ON karne ka tareeqa

Latest [v4.3 billing/security update](V4_3_BILLING_SECURITY_ROMAN_URDU.md) mein yeh auto-bidding helper shamil hai.

Auto-bidding engine pehle se shamil hai. Worker ka `auto-bid gate: False` server configuration ka OFF switch dikhata hai. Is update mein operator helper aur saaf Settings guidance shamil hain, taaki aap private configuration ko manually edit kiye baghair server switch ON/OFF kar sakein. General Gemini/OpenAI AI Chat aur v4 ke live-worker features barqarar hain.

## Existing WSL setup par enable karein

Pehle web aur worker dono terminals mein Ctrl+C karein. Database aur matching private `.env` ka backup rakhein. Existing `FERNET_KEY` preserve karein.

Ubuntu/WSL terminal mein:

```bash
cd ~/bidpilot-v2 &&
git pull --ff-only &&
cd BidPilot_AI_Team_Cloud &&
.venv/bin/python configure_autobid.py enable &&
.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Doosri PowerShell window mein `wsl` chalayein, phir Ubuntu prompt par:

```bash
cd ~/bidpilot-v2/BidPilot_AI_Team_Cloud &&
.venv/bin/python -m app.worker
```

Worker startup par `auto-bid gate: True` saved switch ke active process mein load hone ko dikhata hai. Kisi inherited environment override ki warning aaye to us deployment ke `PLATFORM_AUTOBID_APPROVED` value ko bhi update karein; helper kisi inherited variable ko silently override nahi karta.

Browser `http://127.0.0.1:8000` kholein aur Ctrl+Shift+R karein. Apne account mein:

1. Settings mein Freelancer OAuth token **Verify & Connect** karein aur imported identity check karein. Pehle sandbox test karein. Connection replace/reconnect karne par Auto-Pilot OFF hota hai.
2. Skills, search keywords, project filters, bid cap aur daily/hourly limits save karein. AI-generated proposals ke liye supported model aur valid Gemini/OpenAI key save/test karein; configured template fallback provider verification ka proof nahi.
3. **ENABLE 24/7 UNATTENDED AI AUTO-BIDDING** checkbox ON karein aur **Save Auto-Bidder & Chat Settings** dabayein. Active subscription aur admin account permission bhi required hain.
4. Overview par worker heartbeat, actual successful Freelancer poll aur automation state dekhein. Ready status worker ki health ya live bid acceptance ki jagah nahi leta.

Server switch ON karna har user ka checkbox ON nahi karta. Har account ki saved preference aur permissions alag rehti hain. Auto-chat apna alag opt-in hai. Helper keys/tokens, database, accounts, packages ya user opt-ins change nahi karta. File save hone se running processes ka loaded configuration nahi badalta; web aur worker dono restart karna zaroori hai.

## Status aur disable

Saved file configuration check:

```bash
.venv/bin/python configure_autobid.py status
```

Sirf apne account ko pause karne ke liye Settings mein Auto-Pilot OFF karke Save karein. Server switch disable karne ke liye web/worker stop karke:

```bash
.venv/bin/python configure_autobid.py disable
```

Phir web aur worker restart karein. Pehle se dispatched remote request finish ho sakti hai. Docker/Compose mein host `.env` update ke baad web/worker containers ko updated source/configuration se rebuild/recreate karein; sirf host file badalne se existing containers update nahi hote.

Helper sirf existing `.env` ki `PLATFORM_AUTOBID_APPROVED` setting badalta hai. Baqi text/credentials preserve rehte hain, private `.env.autobid-backup-*` copy banti hai aur replacement atomic hota hai. `.env`, uske backups, database aur venv Git mein include nahi hote. New installations aur new user opt-ins ab bhi default OFF hain.

Filters, working hours, minimum 60-second bid gap, quotas, duplicate/uncertain-result protection aur API cooldown enforce rehte hain. [Freelancer permission and platform limits](https://www.freelancer.com/about/terms) live automation par apply hotay hain. Request intervals account restrictions ya under-one-minute delivery ki guarantee nahi dete. 24/7 operation ke liye online server aur worker chahiye.

[Complete updated v4.2 ZIP](https://github.com/innointerface-design/-freelancer-auto-bidder/raw/refs/heads/main/downloads/BidPilot_AI_Updated_v4_2.zip) aur [verification report](REVIEW_AND_TESTS.md).
