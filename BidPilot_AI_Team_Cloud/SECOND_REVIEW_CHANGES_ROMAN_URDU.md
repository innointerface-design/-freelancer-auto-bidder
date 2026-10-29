# Updated v2 — kya theek hua?

30 October update: AI key save/test ke races fix hain; save ke dauran typed replacement key preserve rehti hai, duplicate requests block hoti hain aur late result purani configuration ko current nahi dikhata. Naye Gemini setups `gemini-3.5-flash-lite` use karte hain; existing models preserve rehte hain. Google 2.5 access existing users tak limited rakhta hai. Agar recommended current model catalog mein ho to explicit selection button milta hai; **Save**, phir **Test saved AI keys** karein. Request mein deprecated optional temperature nahi bheja jata. OpenAI ka actual invalid-key401 valid full secret ke baghair fix nahi ho sakta.

Nayi uploaded 16 MB `FIXED.zip` mein application code nahi, sirf Python venv aur requirements thay. GitHub download mein poora reviewed application source hai, venv/private database/.env nahi. Isliye source ZIP chhoti hai.

29 October 2026 ko doosra review mukammal hua. Yeh ZIP pehle wali reviewed ZIP ki updated replacement hai.

- Logout/account switch ke baad puranay admin/customer ka data screen par rehne ka bug fix. Doosray browser tab mein account change ho to bhi purana data clear hota hai.
- Successful API test ke baad purana failure cooldown clear hota hai. Adhura AI jawab automatic bid mein use nahi hota.
- AI proposal bante waqt settings pause ho sakti hain; pause, expired subscription ya changed connection par bid request rukti hai.
- Missing currency/type, own project aur puranay unverified job records par live bid block hoti hai; rescan karein.
- Short skills, matching/risk filters, price rounding, chat delivery status aur private database permissions fix kiye.
- Healthcheck custom port aur database outage ko sahi handle karta hai.

**Verification:** 222 tests aur 19 subtests pass; latest Chromium mein5 AI-settings flows pass. Pehle reviewed v2 par13 desktop/mobile flows aur Docker/PostgreSQL deployment checks pass huay thay; deployment smoke is patch par repeat nahi hua. Kisi asli account par bid ya chat message nahi bheja gaya.

Packages $10 Starter aur $40 Pro hain; owner samait do founder slots free hain. Payment approval manual verification se hoti hai.

Ubuntu commands README_ROMAN_URDU.md mein hain. Existing installation update karne se pehle database aur matching private .env ka backup lein, old worker band karein, phir isolated copy par migration test karein. Purani FERNET_KEY preserve karein.

Live OpenAI/Gemini keys, Freelancer sandbox bid aur aapke domain ke HTTPS/payment settlement tests ab bhi aapke server par karne hain. Shared chat keys revoke karke fresh keys privately save karein. Koi app zero bugs ya hacker-proof guarantee nahi de sakti.

Latest patch mein localhost/127.0.0.1 local login403 fix, readable JSON errors, saved-only API tests aur Gemini404 available-model discovery shamil hain. OpenAI401 actual credential rejection hai; valid fresh secret key app mein save karni hogi. Production origin policy strict rehti hai.

Startup patch: optional second-founder email existing paid account se match ho to app ab band nahi hoti. Passwords, paid plan aur saved tokens preserve rehte hain; founder access sirf Admin se assign hota hai. Free seats ki concurrent assignment lock aur test-mode dotenv isolation bhi verify ki gayi.

Provider diagnostic patch: Gemini model listed hone par bhi404 ho to ab model change ka andaza nahi lagaya jata. Safe Provider code/status/reason aur body-format dikhte hain. Root cause code se confirm hoga; OpenAI401 par rejected saved key ko replace/verify karna zaroori hai.

Saved-key verification patch: encrypted-save se outgoingBearerheader tak exactvalue preserve hone ka end-to-end regression pass hai. OpenAI recordID-vs-secret guidance ab explicit invalid-key401 ke baad dikhati hai. Independent `check_openai_key.py` se app/config ke baghair private hidden-input test ho sakta hai; total217tests+12subtests pass.
