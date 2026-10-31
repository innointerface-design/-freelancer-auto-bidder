# BidPilot AI v4.4.2 — manually plan expire karein, login preserve rahe

Admin ke **Team & SaaS Admin → Manage users** mein paid active/trial plan ke saamne **Expire plan** button hai.

1. Intended customer ka naam/email aur current plan check karein.
2. **Expire plan** click karein. Confirmation mein customer aur plan dobara check karke confirm karein; Cancel par koi change nahi hota.
3. Plan ka status **expired** ho jayega, paid access band aur automatic bidding/chat greetings OFF ho jayengi. Account ka login status change nahi hota: Enabled customer same email/password se login karke Billing aur renewal use kar sakta hai.

**Disable login** account mein sign-in band karta hai. Isay sirf plan expire karne ke liye use na karein. **Enable login** bhi subscription activate ya renew nahi karta. Disabled customer ka plan expire karne par uska account disabled hi rehta hai. Expiry password reset ya account recreation nahi karti.

Manual expiry current access ko abhi end karti hai. Original activation/billing anchors, receipts, bid usage/history, role, saved API keys aur other accounts preserve rehte hain. Effective expiry date current time tak update hoti hai; pehle se purani expiry ko future mein nahi le jati. Administrator ka action audit mein record hota hai. Koi refund ya external bank/payment cancellation automatically nahi hota.

Paid administrator apna plan bhi confirmation ke baad expire kar sakta hai; admin sign-in/account management preserve rehta hai. Free Founder accounts par paid-plan expiry apply nahi hoti. Natural expiry bhi displayed date par automatically hoti rehti hai. Renewal ke liye actual settled payment verify karke existing payment approval flow use karein.

## Automatic expiry aur auto-bidding dono

- **Automatic expiry:** saved expiry date par paid access khud end hoti hai. Worker idle ho tab bhi scheduled expiry sweep chalti hai; paid requests server clock se expiry check karti hain. **Expire plan** date se pehle manual control hai.
- **Auto-bidding:** active plan par existing worker matching projects scan karta hai. Admin **Allow Auto / Pause Auto** se customer ki automation permission control karta hai; customer Settings mein **Auto-Pilot** enable karke apni filters/limits save karta hai. Apna Auto-Pilot OFF karke settings save karna bhi automation stop karta hai.
- Real bidding ke liye verified Freelancer connection, server automation switch, configured limits aur running worker required hain. **Allow Auto** akela missing token/permission/server switch ko bypass nahi karta. Worker/server offline ho to live scanning/bidding nahi chalti. Sirf is release ko install karna automatically sab accounts ki bidding ON nahi karta.
- Manual/natural plan expiry sirf intended account ki paid automation stop karti hai. Doosre active accounts ki settings preserve rehti hain. Verified renewal se paid access milne ke baad customer apni automation settings review karke dobara opt in kare; expiry se paused automation silently restart nahi hoti.

Page khula ho aur doosra admin plan renew/change kar de, to stale expiry request reject hoti hai; current dates reload/review karein. Member accounts expiry endpoint use nahi kar sakte. Existing CSRF and account guards remain.

## Existing installation update

Web aur worker terminals Ctrl+C se stop karein. Existing database aur matching private `.env` ka backup preserve rakhein. WSL mein:

```bash
cd ~/bidpilot-v2 &&
git pull --ff-only &&
cd BidPilot_AI_Team_Cloud &&
~/.local/bin/uv pip install --python .venv/bin/python -r requirements.txt &&
.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Agar Git local changes ki wajah se abort kare, app stopped rakh kar private backup aur local code stash banayein, phir upar wala update block dobara chalayein:

```bash
cd ~/bidpilot-v2 &&
umask 077 &&
tar --exclude='.venv' --exclude='venv' --exclude='__pycache__' --exclude='.pytest_cache' \
-czf "$HOME/bidpilot-backup-$(date +%Y%m%d-%H%M%S).tar.gz" BidPilot_AI_Team_Cloud &&
git stash push -u -m "Local code before v4.4.2 update" -- BidPilot_AI_Team_Cloud
```

Repo ke private-file ignore rules `.env`, SQLite databases/sidecars aur `.venv` ko stash se exclude karte hain. Backup private local file hai; usay GitHub par upload na karein. `git stash pop` se purana code blindly restore na karein. Database delete/recreate, `git reset --hard` ya `FERNET_KEY` replacement required nahi.

Doosri PowerShell window mein `wsl`, phir:

```bash
cd ~/bidpilot-v2/BidPilot_AI_Team_Cloud &&
.venv/bin/python -m app.worker
```

Browser mein Ctrl+Shift+R karein, phir **Team & SaaS Admin → Manage users** kholein.

[Account recovery](V4_4_1_ACCOUNT_RECOVERY_ROMAN_URDU.md) · [Billing/security](V4_3_BILLING_SECURITY_ROMAN_URDU.md) · [Verification](REVIEW_AND_TESTS.md)
