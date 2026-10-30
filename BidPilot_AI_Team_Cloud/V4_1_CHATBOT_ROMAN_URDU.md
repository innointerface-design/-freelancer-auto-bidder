# BidPilot AI v4.1 — Gemini aur OpenAI AI Chat

Naya general AI chatbot ab app mein shamil hai. Sidebar ka **AI Chat** kholein. **General assistant** mein aam sawal, writing, coding, translation aur ideas pooch sakte hain. **App help** mein BidPilot setup, API errors, billing aur bidding settings ki guidance milti hai. Existing live worker, filters, packages aur dark/purple design preserve rehte hain.

## Istemaal

1. Settings mein apni complete Gemini/OpenAI secret key aur account-supported model save karein. **Test saved AI keys** se saved configuration check karein; key chat mein paste na karein.
2. **AI Chat → General assistant** kholein aur sawal likhein. **Auto** pehle Gemini, phir OpenAI try karta hai; explicit provider sirf wahi provider use karta hai.
3. Answer ke label mein actual provider aur model dikhte hain. Provider unavailable ho to general chat connection problem dikhata hai. App help ka offline fallback **Local help** ke label se aata hai.
4. Mode change ya Clear se current conversation clear hoti hai. Logout/account change ke baad purane replies display nahi hote. Conversation app database ya browser storage mein save nahi hoti; recent conversation sirf current tab ki memory mein rehti hai.

Live AI ke liye active subscription, valid saved ya operator-configured shared key, supported model aur target server se provider access zaroori hai. App ki shared AI allowance 60 requests per 15 minutes per account hai; proposal/chat requests isi allowance ko share karti hain. Provider ke apne quotas aur billing alag apply hotay hain. Active dashboard key record ID, masked value ya ChatGPT subscription full API secret ki jagah nahi leta.

Chat server-side Gemini/OpenAI use karta hai; API secrets browser ko return nahi hotay. Known saved credentials aur common secret formats provider request/reply se redact hotay hain. Passwords, tokens aur sensitive customer information chat mein na bhejein. General assistant ko app ka account-status snapshot nahi bheja jata. App help limited own-account status use karta hai. Redacted question aur recent history selected AI provider ko bheji jati hai.

Chat account settings change, payment approve, code execute ya bid/message send nahi karta. Web browsing ya files upload is feature mein shamil nahi; answers live internet verification ka proof nahi hain. Automatic Freelancer bidding existing worker aur explicit settings se chalti hai.

## Existing installation update

Pehle web aur worker dono terminals mein Ctrl+C karein. Database aur matching private `.env` ka backup rakhein; existing `FERNET_KEY` preserve karein.

```bash
cd ~/bidpilot-v2 &&
git pull --ff-only &&
cd BidPilot_AI_Team_Cloud &&
~/.local/bin/uv pip install --python .venv/bin/python -r requirements.txt &&
.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Doosray terminal mein:

```bash
cd ~/bidpilot-v2/BidPilot_AI_Team_Cloud
.venv/bin/python -m app.worker
```

Browser `http://127.0.0.1:8000` par kholein aur Ctrl+Shift+R karein. Sidebar mein **AI Chat** nazar aayega. GitHub upload se aapka running server automatically update nahi hota.

[Updated v4.1 ZIP](https://github.com/innointerface-design/-freelancer-auto-bidder/raw/refs/heads/main/downloads/BidPilot_AI_Updated_v4_1.zip) mein complete application source, tests aur deployment instructions hain. V4/v2 download paths latest-source compatibility aliases hain; v3 archive historical release hai. ZIP mein venv, real `.env`, API keys ya customer database shamil nahi.
