# Updated v2 — kya theek hua?

29 October 2026 ko doosra review mukammal hua. Yeh ZIP pehle wali reviewed ZIP ki updated replacement hai.

- Logout/account switch ke baad puranay admin/customer ka data screen par rehne ka bug fix. Doosray browser tab mein account change ho to bhi purana data clear hota hai.
- Successful API test ke baad purana failure cooldown clear hota hai. Adhura AI jawab automatic bid mein use nahi hota.
- AI proposal bante waqt settings pause ho sakti hain; pause, expired subscription ya changed connection par bid request rukti hai.
- Missing currency/type, own project aur puranay unverified job records par live bid block hoti hai; rescan karein.
- Short skills, matching/risk filters, price rounding, chat delivery status aur private database permissions fix kiye.
- Healthcheck custom port aur database outage ko sahi handle karta hai.

**Verification:** 125 tests aur 12 subtests pass; 13 Chromium desktop/mobile flows; Docker aur actual PostgreSQL checks pass. Kisi asli account par bid ya chat message nahi bheja gaya.

Packages $10 Starter aur $40 Pro hain; owner samait do founder slots free hain. Payment approval manual verification se hoti hai.

Ubuntu commands README_ROMAN_URDU.md mein hain. Existing installation update karne se pehle database aur matching private .env ka backup lein, old worker band karein, phir isolated copy par migration test karein. Purani FERNET_KEY preserve karein.

Live OpenAI/Gemini keys, Freelancer sandbox bid aur aapke domain ke HTTPS/payment settlement tests ab bhi aapke server par karne hain. Shared chat keys revoke karke fresh keys privately save karein. Koi app zero bugs ya hacker-proof guarantee nahi de sakti.
