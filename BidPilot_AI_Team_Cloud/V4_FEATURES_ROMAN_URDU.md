# BidPilot AI v4 — live automation aur update

31 October 2026: is update ka focus live Freelancer refresh, automatic bidding ki timing, Settings bugs aur same dark/purple design ko saaf banana hai. Existing accounts, encrypted keys, subscriptions, filters aur v3 app-help feature preserve rehte hain.

## Kya naya hai

- **Overview mein live status:** worker ki recent heartbeat, actual successful Freelancer project poll, next refresh, bid gap aur pending projects dikhte hain. Worker online hona API success ka proof nahi. Scan start hone ko successful scan nahi dikhaya jata.
- **60-second refresh target:** background worker due accounts har second check karta hai. `SCAN_SECONDS` ka default aur minimum 60 seconds hai; operator isay barha sakta hai. Slow account ki API/AI request doosray accounts ko serial wait nahi karwati; ek worker maximum chaar accounts saath process karta hai.
- **Fresh match par auto-bid checks:** successful refresh ke baad existing AI proposal/auto-bid flow foran check hota hai. Scan ke darmiyan stored matches bhi check hotay hain. Profile match, chosen filters, working hours, minimum gap aur account/package limits phir bhi apply hotay hain.
- **Overlapping scan protection:** har account ka persistent scan claim hota hai. Ek purani request connection change ke baad results import nahi kar sakti. Crash ke baad expired claim recover ho sakta hai. Worker band karte waqt queued tasks cancel hotay hain; running read-only requests finish ho sakti hain, aur automatic paths stop signal dobara check karti hain.
- **Settings fixes:** Save pending ho to uske baad type ki hui edits preserve rehti hain. Basic settings Save doosray AI fields ki unsaved edits nahi mitata. Save aur Restore ek saath chal kar automation ko dobara enable nahi kar sakte.
- **Same design, cleaner layout:** dark/purple theme ke andar live status cards aur desktop/mobile spacing improve hui hai. General-purpose naya AI chatbot is update mein add nahi hua; existing v3 app-help barqarar hai.

“Projects awaiting checks” ka count final bid guarantee nahi. Usmein aapke current connected account ke pending live candidates hotay hain; dispatch se pehle eligibility dobara check hoti hai. Demo projects aur purani/different account connection ke projects is count mein shamil nahi.

## Timing aur account controls

60 seconds project-refresh ka target hai, job post hone se bid pohanchne ki guarantee nahi. Freelancer response, AI generation, busy worker capacity, schedule, bid gap, quota ya cooldown ki wajah se ek minute se zyada lag sakta hai. Bid sirf tab attempt hoti hai jab operator switch aur user Auto-Pilot dono enabled hon aur account eligible ho.

Minimum bid gap 60 seconds hai aur aap isay barha sakte hain. New accounts ke default controls 10 bids/day aur 3/hour hain; existing chosen limits preserve rehte hain. Freelancer `401/403` access pause, `429` provider wait aur uncertain-result automatic retry protection barqarar hain. App ka quota Freelancer ke remaining bids, wallet balance ya account verification ki jagah nahi leta.

24/7 chalne ke liye online server aur running worker chahiye. Personal computer band ho to uska local WSL worker bhi band ho jata hai. Overview status har 15 seconds app ke apne server se update hota hai; browser direct Freelancer polling nahi karta.

[Freelancer User Agreement section 33](https://www.freelancer.com/about/terms) automated access, including API, ke liye express written permission ka zikr karta hai. [API Terms section 4.9](https://www.freelancer.com/about/apiterms) excessive requests aur limits bypass karna mana karta hai. App controls account block hone ki guarantee nahi dete. Live automation se pehle permission aur account eligibility confirm karein.

## Existing WSL installation update

Pehle **web aur worker dono terminals mein Ctrl+C** karein. Apni database aur matching private `.env` ka backup rakhein. Existing `FERNET_KEY` preserve karein; database ya `.env` delete/recreate na karein. V4 additive scan-status columns aur worker-heartbeat table banata hai; pehle restored test copy par upgrade check karein.

Web terminal mein:

```bash
cd ~/bidpilot-v2 &&
git pull --ff-only &&
cd BidPilot_AI_Team_Cloud &&
~/.local/bin/uv pip install --python .venv/bin/python -r requirements.txt &&
.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Doosray terminal mein worker chalayein:

```bash
cd ~/bidpilot-v2/BidPilot_AI_Team_Cloud
.venv/bin/python -m app.worker
```

Browser mein `http://127.0.0.1:8000` kholein aur **Ctrl+Shift+R** karein. Overview mein worker heartbeat check karein, phir actual last successful poll dekhein. Valid token/model aur target server network access zaroori hain. Invalid API secret ko code se valid nahi banaya ja sakta; fresh complete secret sirf logged-in Settings ya private server configuration mein save karein.

Automatic bidding default OFF hai. Permission, sandbox test, actual account balance aur apne bid limits review karne ke baad operator `.env` switch `PLATFORM_AUTOBID_APPROVED=true` set karke web/worker restart kare, phir user Auto-Pilot explicitly ON kare. Settings restore Auto-Pilot aur auto-chat OFF rakhta hai.

## Download aur source

- [Complete updated v4 ZIP](https://github.com/innointerface-design/-freelancer-auto-bidder/raw/refs/heads/main/downloads/BidPilot_AI_Updated_v4.zip)
- [Application source](https://github.com/innointerface-design/-freelancer-auto-bidder/tree/main/BidPilot_AI_Team_Cloud)
- [Verification report](REVIEW_AND_TESTS.md)
- [Previous v3 features](V3_FEATURES_ROMAN_URDU.md)

ZIP mein complete application source, tests, deployment files aur instructions hain. Venv, real customer database aur private `.env` shamil nahi. Isliye source ZIP ka 16 MB se chhota hona normal hai. GitHub ZIP preview ke bajaye direct download link use karein. Naye version ko apni private configuration aur database ke saath target server par verify karein; public GitHub upload se website automatically deploy nahi hoti.
