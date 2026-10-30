# BidPilot AI v3 — naye features aur update ka tareeqa

30 October 2026: FABB ki public feature list se useful workflows compare karke BidPilot mein apni implementation add ki gayi hai. Existing dark/purple design, accounts, packages aur encrypted saved keys preserve rehte hain.

## Naye features

- **AI Help Chat:** sidebar mein AI Help kholein. App setup, API errors, billing aur bidding settings ke sawal Gemini ya OpenAI se backend par pooch sakte hain. Auto mode Gemini ke baad OpenAI try karta hai; explicit mode sirf selected provider use karta hai. Live AI ke liye active subscription aur valid saved/server key chahiye. Provider unavailable ho ya subscription inactive ho to **Local help** label ke saath app guidance milti hai; usay live AI nahi kaha jata.
- **Keyword proposal templates:** naam, keywords aur ANY/ALL matching set karein. Pehla enabled matching template use hota hai; Move up se priority badalti hai. Optional default template baqi projects par lagta hai. Saved template local aur AI proposal buttons dono par use hota hai.
- **Advanced project filters:** allowed currencies, excluded countries, exact employer username/account ID, minimum employer rating/reviews, maximum project age aur title/description/skills search fields.
- **Manual-review keywords:** sensitive projects ko automatic bidding se rok kar manual review ke liye rakhte hain. Manual bids par bhi explicit project filters aur profile-skill match apply hota hai.
- **Working hours:** IANA timezone, weekdays aur start/end hour set karein. Midnight cross karne wale windows supported hain. Start/end barabar ho to selected weekdays poore din enabled hote hain.
- **FIFO queue:** eligible projects discovery order mein wait karte hain. Score order bhi choose kar sakte hain. Bids ke beech minimum gap 60 seconds hai; aap isay barha sakte hain. Queue database ke projects se chalti hai, isliye browser tab ya worker restart se pending project gayab nahi hota.
- **Settings backup/restore:** JSON file mein profile, filters, templates aur schedule export hote hain. Passwords, API keys, Freelancer tokens, roles aur billing export/restore nahi hote. File preview ke baad explicit Restore karein; auto-bidding aur auto-chat OFF ho jate hain.
- **Same design, cleaner layout:** dark/purple theme barqarar hai; focus states, form spacing, chat bubbles aur mobile overflow improve huay hain.

Template placeholders: `{client_name}`, `{project_title}`, `{skills}`, `{portfolio}`, `{signature}`, `{delivery_days}`, `{project_description}`. Placeholder ke ilawa attribute access ya executable format supported nahi. Apna sachha profile text aur delivery estimate likhein.

## Account aur API controls

- New accounts ke defaults **10 bids/day, 3/hour** hain. Existing accounts ke chosen limits preserve rehte hain; apne Settings mein review karein.
- Minimum gap actual dispatch ke waqt record hota hai. Parallel worker/API requests ek account se duplicate ya rapid overlapping bids nahi bhej sakti.
- Freelancer `401/403` par token/access pause aur automation OFF hoti hai. Account/permission check ke baad successful reconnect zaroori hai.
- Freelancer `429` par valid `Retry-After` follow hota hai; header na ho to conservative wait lagta hai. Cooldown restart, disconnect aur token replacement se erase nahi hota.
- Unknown bid/chat result ko `uncertain` rakha jata hai; automatic retry nahi hoti. Freelancer par manually result check karein.
- AI Help Chat account actions execute nahi karta. Known credentials/common secret formats cloud request se redact hote hain; phir bhi passwords/API keys chat mein paste na karein. Conversation app database ya browser storage mein save nahi hoti. Live help ke liye redacted question, recent history aur limited own-account status selected provider ko bheja jata hai.

**Account restriction ki guarantee nahi di ja sakti.** [Freelancer User Agreement section 33](https://www.freelancer.com/about/terms) automated access, including API, ke liye express written permission ka zikr karta hai. [API Terms section 4.9](https://www.freelancer.com/about/apiterms) limits bypass aur excessive requests mana karta hai. 60-second gap ya 10/day limit platform-approved safe limits nahi hain. Live automation se pehle Freelancer se permission aur apni account eligibility confirm karein.

App ka Starter/Pro quota Freelancer ke remaining bids, wallet balance ya verification ki jagah nahi leta. Actual balance/bid allowance Freelancer par check karein; is update mein live marketplace balance verification nahi ki gayi. 24/7 chalne ke liye online server aur running worker chahiye; personal computer band hone par local WSL server bhi band hota hai.

## Existing WSL installation update

Pehle web aur worker terminals mein Ctrl+C karein. Database aur matching private `.env` ka backup rakhein. Existing `FERNET_KEY` ko preserve karein.

```bash
cd ~/bidpilot-v2 &&
git pull --ff-only &&
cd BidPilot_AI_Team_Cloud &&
~/.local/bin/uv pip install --python .venv/bin/python -r requirements.txt &&
.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Browser: `http://127.0.0.1:8000`, phir Ctrl+Shift+R. AI Help sidebar mein hai; naye filters/templates Settings ke Advanced automation panel mein hain.

Timezone schedules ke liye nayi pinned `tzdata` dependency shamil hai; is update par requirements install karna zaroori hai. Existing `.env`, database ya venv delete/recreate na karein. Startup additive columns migrate karta hai; upgrade se pehle backup aur isolated copy par migration verification recommended hai.

Worker ko alag terminal mein chalayein:

```bash
cd ~/bidpilot-v2/BidPilot_AI_Team_Cloud
.venv/bin/python -m app.worker
```

Automation default OFF hai. Written permission, sandbox acceptance, live token/scopes, configured bid limits aur actual account balance verify karne ke baad operator server switch aur user preferences enable kare.

## Download aur source

[Complete updated v3 ZIP](https://github.com/innointerface-design/-freelancer-auto-bidder/raw/refs/heads/main/downloads/BidPilot_AI_Updated_v3.zip) mein application source, tests, deployment files aur instructions hain. Old v2 download path latest source ka compatible alias rahega. ZIP mein venv, customer database aur private `.env` nahi hain; source archive ka 16 MB se chhota hona normal hai.

Public feature reference: [Freelancer Auto Bidding Bot](https://www.freelancerautobiddingbot.com/). Us website ke marketing claims ko safety/earnings verification nahi maana gaya. Yeh update automatic NDA/IP agreements, paid bid upgrades ya unsolicited clarification-board posting add nahi karta.
