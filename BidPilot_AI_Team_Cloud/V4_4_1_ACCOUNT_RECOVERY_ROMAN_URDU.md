# BidPilot AI v4.4.1 — existing account ka login check aur recovery

`Invalid email or password` (HTTP401) aur `Too many attempts` (HTTP429) alag errors hain. V4.4 ne successful/shared-IP sign-ins se lagne wala false429 repair kiya tha. Yeh update local operator ko genuine401 ki diagnosis aur existing account ka secure password reset deta hai. Customer ki actual database check kiye baghair uske401 ko resolved nahi kaha ja sakta.

Plan upgrade/payment approval password ya account Enabled status change nahi karta. Enabled account mein401 ka reason stored password mismatch, missing account/different database, ya concurrent password change ho sakta hai. Browser mein password saved hona server ke stored password se match hone ka proof nahi. Registration pehli baar session directly banati hai.

## Pehle existing account check karein

PowerShell mein `wsl` kholein. Existing installation use karein; placeholder email ko customer ke registered email se replace karein. Password command mein ya chat mein na likhein.

```bash
cd ~/bidpilot-v2 &&
git pull --ff-only &&
cd BidPilot_AI_Team_Cloud &&
.venv/bin/python manage_login.py check --email 'customer@example.test' --verify-password
```

Password terminal ke hidden prompt mein enter karein: characters ya stars nazar nahi aayenge. Command account record change nahi karti. Output SQLite ki resolved file location, account status, plan aur entered password ka match result dikhata hai; password, hash, API keys aur database credentials print nahi hotay.

- **Password matches aur Enabled:** is database mein entered password accepted hai. Web aur worker bhi isi installation/private configuration se start karein. Browser autofill ki jagah wahi checked password enter karein. Agar web phir bhi401 de, web process ka folder/configuration compare karein; reset zaroori nahi.
- **Password does not match:** is database mein stored password entered value se alag hai. Local operator neeche wala reset existing account par use kar sakta hai.
- **Account not found:** current database mein yeh email nahi hai. Original installation ki database aur matching private `.env` locate karein; replacement signup na karein.
- **Disabled:** admin intended member ka Enabled status check kare. Reset disabled account ko activate nahi karta.
- **Database unavailable/missing:** helper new database create nahi karti. Existing configuration, persistent volume aur app folder check karein.

Bahut purane version se update kiya ho to updated web app ko isi existing database ke saath ek baar normally start karein, taaki uski existing startup migrations complete hon. Recovery helper khud schema migrate ya owner bootstrap nahi karti.

Setup ka `sqlite:///./bidpilot_local.db` path process ke working directory se resolve hota hai. Web/worker ko app folder se chalayein. Helper khud isi folder mein resolve karti hai; database ko move ya merge nahi karti. PostgreSQL credentials/URL share na karein.

## Zaroorat par usi account ka password reset

Sirf server operator, intended customer ki identity confirm karne ke baad, application host ke interactive terminal par run kare:

```bash
cd ~/bidpilot-v2/BidPilot_AI_Team_Cloud &&
.venv/bin/python manage_login.py reset-password --email 'customer@example.test'
```

Naya password aur confirmation dono hidden prompts mein enter karein (12–256 characters). Secure terminal available na ho to command refuse karti hai. Password command argument, pipe, chat ya email se pass na karein. Customer isi registered email aur new password se login kare. Naya account ya dobara payment required nahi.

Reset intended existing account ka salted password hash update, uski sessions revoke aur uska failed-login counter clear karta hai; action audit hota hai. Plan tier, payment history, expiry/start dates, role, Enabled status, encrypted API keys aur doosre accounts preserve rehte hain. Network/request limits remain; genuine429 ka wait follow karein.

## Web aur worker start karein

Existing database aur matching private `.env` ka backup preserve karein. Purane web/worker terminals Ctrl+C se stop karne ke baad:

```bash
cd ~/bidpilot-v2/BidPilot_AI_Team_Cloud &&
.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Doosri PowerShell window mein `wsl`, phir:

```bash
cd ~/bidpilot-v2/BidPilot_AI_Team_Cloud &&
.venv/bin/python -m app.worker
```

Browser `http://127.0.0.1:8000` par Ctrl+Shift+R karein. Account recreate, private `.env`/database overwrite ya `FERNET_KEY` replace na karein.

[Previous limiter fix](V4_4_LOGIN_FIX_ROMAN_URDU.md) · [Verification](REVIEW_AND_TESTS.md)
