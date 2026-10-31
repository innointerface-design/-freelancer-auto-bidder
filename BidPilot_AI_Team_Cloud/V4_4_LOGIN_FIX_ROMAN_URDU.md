# BidPilot AI v4.4 — Pro upgrade ke baad sign-in aur lock recovery

Screenshot ka `Too many attempts` HTTP429 sign-in limiter se aata tha. V4.3 limiter successful sign-ins ko bhi count karta tha, aur ek shared IP se admin/customer logins mil kar 10 attempts par block ho sakte thay. Registration ke baad frontend ek extra login request bhi bhejta tha, jabke registration pehle se session create karti hai. Dono behaviors repair kiye gaye hain.

## Naya behavior

- Successful sign-ins failed-password allowance consume nahi karte. Correct sign-in par sirf us account ka failure counter clear hota hai; shared network failures erase nahi hote.
- Account par 15 minutes mein 10 failed attempts, network par 100 failed attempts aur 120 sign-in requests/minute ki separate protection hai. Short in-flight reservations parallel attempts ko limit karti hain; password hashing database write lock ke bahar hoti hai.
- Genuine429 par server remaining wait aur `Retry-After` bhejta hai. Login screen usi wait ka countdown dikhati hai. Duplicate submits aur stale authentication responses guarded hain.
- Old mixed success/failure counters new versioned login counters se alag hain, taaki purane false-positive locks naye deployment par persist na karein. New genuine failure limits restart ke baad bhi persist rehte hain.
- Pro upgrade, renewal ya payment approval password change nahi karta. Unpaid/expired subscription wala enabled account bhi login karke Billing use kar sakta hai; paid AI/bidding access ke apne checks rehte hain. Disabled account ko payment ya unlock automatically enable nahi karta.
- Concurrent password change ke baad purana verified password naya session create nahi kar sakta: login session banane se pehle current password hash aur account status dobara check hota hai.

## Existing WSL installation update

Web aur worker dono terminals mein Ctrl+C karein. Database aur matching private `.env` ka backup rakhein. Existing `FERNET_KEY` preserve karein; account delete ya database recreate karne ki zaroorat nahi.

```bash
cd ~/bidpilot-v2 &&
git pull --ff-only &&
cd BidPilot_AI_Team_Cloud &&
~/.local/bin/uv pip install --python .venv/bin/python -r requirements.txt &&
.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Doosri PowerShell window mein `wsl`, phir:

```bash
cd ~/bidpilot-v2/BidPilot_AI_Team_Cloud &&
.venv/bin/python -m app.worker
```

Browser `http://127.0.0.1:8000` par Ctrl+Shift+R karein. Customer wahi registered email aur password use kare. Browser autofill mein saved credentials sahi honay chahiye; payment ke liye password admin ko bhejna zaroori nahi.

## Genuine account lock ko recover karein

Signed-in admin **Team & SaaS Admin → intended member → Reset sign-in lock** use kar sakta hai. Yeh sirf selected account ka failed-sign-in counter reset karta hai. Password, plan, expiry, account status, active sessions aur network/request limits change nahi hotay. Admin action audit mein record hota hai. Pehle customer ki identity aur intended account confirm karein. Reset ke baad customer sign-in page reload karke correct password se dobara try kare, taaki purana browser countdown clear ho.

Operator khud locked ho to application folder mein local recovery command bhi hai. Placeholder ko existing customer/admin email se replace karein; password command mein na likhein:

```bash
.venv/bin/python manage_login.py unlock --email 'customer@example.test'
```

Yeh command password reset/recovery nahi karti. Agar unlock ke baad `Invalid email or password` aaye, registered email, exact password aur admin panel ka account Enabled status check karein. Shared network/request limit active ho to displayed wait follow karein; account unlock usay bypass nahi karta.

Payment methods, monthly expiry, account security, AI Chat aur auto-bidding controls v4.3 se preserve hain. [Verification report](REVIEW_AND_TESTS.md) · [Complete v4.4 ZIP](https://github.com/innointerface-design/-freelancer-auto-bidder/raw/refs/heads/main/downloads/BidPilot_AI_Updated_v4_4.zip)
