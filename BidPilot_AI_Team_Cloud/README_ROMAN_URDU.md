# BidPilot AI — Ubuntu setup aur zaroori baatein

**Latest v4 — 31 October 2026:** live Freelancer refresh, worker status, automatic bidding timing aur current WSL update commands ke liye [V4_FEATURES_ROMAN_URDU.md](V4_FEATURES_ROMAN_URDU.md) dekhein. Existing dark/purple design aur v3 features barqarar hain. Latest requirements install karein; web aur worker alag processes hain.

Poore commands aur production details `README.md` mein hain. Is ZIP mein koi chat wali API key include nahi hai. Jo credentials chat mein share hue thay unhein revoke karke fresh keys banayein; keys sirf private `.env` ya logged-in Settings mein save karein.

## Packages

- **Starter: $10 / 30 din** — 150 bid attempts per billing period. Sending/uncertain attempts bhi quota mein count hotay hain taake retries se limit bypass na ho.
- **Pro: $40 / 30 din** — monthly app quota nahi; Freelancer ke limits aur aapke hourly/daily controls phir bhi apply hotay hain.
- **Do founders free** — owner/admin aur optional second founder. `setup_local.py` par emails/passwords set karein. Public signup se koi admin/free access nahi le sakta.

Payment screen mein transaction ID submit karna payment verification nahi hai. Admin apne asli JazzCash/EasyPaisa/bank/Stripe account mein payment confirm karne ke baad approve kare. Pehli approval se 30 din activate hotay hain. Wohi active tier renew karne se expiry mein 30 din add hotay hain; current quota window reset nahi hoti. Tier change par naya 30-day period start hota hai. Automatic recurring payment ya verified Stripe webhook is version mein nahi hai. Public signup par automatic email verification ya password-reset email bhi nahi hai.

## Ubuntu par local test

Yeh commands Ubuntu 24.04 LTS / Python 3.12 ke liye hain. Doosray Ubuntu version par Python 3.12 explicitly select karein ya Docker deployment use karein.

```bash
sudo apt update
sudo apt install -y python3 python3-venv
cd BidPilot_AI_Team_Cloud
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements-dev.txt
python setup_local.py
python -m pytest -q
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Browser mein `http://127.0.0.1:8000` kholein. Doosray terminal mein isi folder/venv se `python -m app.worker` chalayein. Worker ke baghair automatic scanning nahi hogi.

## Public HTTPS website

Domain ko server par point karein aur Docker Engine + Compose plugin install karein. Fresh production folder mein `python setup_local.py --production` chalayein; apna domain, admin aur optional second-founder credentials dein. Script existing `.env` overwrite nahi karti. Pehle se `.env` ho to usmein production URL/settings privately update karein. Phir:

```bash
chmod 600 .env
docker compose up --build -d
docker compose ps
curl --fail https://aapka-domain/health
```

Caddy HTTPS, PostgreSQL persistent storage aur web/worker containers Compose file mein hain. Sirf ports 80/443 public hon; database aur port 8000 expose na karein. `.env` ya `docker compose config` ka output share na karein — usmein secrets hotay hain.

## Freelancer aur AI test

Settings se fresh provider key save karke **Test AI Keys** karein. Freelancer token pehle **Sandbox** environment mein connect karein, identity confirm karein aur apne sandbox project par manual bid test karein. Real token/model/permissions aur server internet access ke baghair live API success confirm nahi ho sakti. Local template proposal ka chalna cloud AI key ke chalne ka proof nahi hai.

Auto-bidding default OFF hai. Sandbox verification ke baad `.env` mein `PLATFORM_AUTOBID_APPROVED=true`, web/worker restart aur user Settings mein Auto-Pilot ON karna hota hai. Auto-chat alag opt-in hai. Real bids/messages Freelancer account par action lete hain; bid caps aur currency carefully set karein. `uncertain` bid ko Freelancer par manually check karein; automatic retry nahi hoti.

## Data aur backup

Passwords hashed aur API tokens encrypted hain, lekin koi app “hacker-proof” guarantee nahi de sakti. HTTPS, private secrets, updated server aur restricted backups zaroori hain. `bash deploy/backup.sh` se DB backup lein aur `.env` ka alag encrypted backup rakhein. `FERNET_KEY` kho gayi ya replace hui to puranay saved tokens decrypt nahi honge. Production se pehle restore drill aur actual payment approval flow test karein. Detailed recovery/update instructions `README.md` mein hain.

## Login 403 aur AI test errors

Local browser mein `http://127.0.0.1:8000` kholein. Development/test mode mein `localhost` bhi usi scheme aur port par supported hai. Production mein `PUBLIC_URL` ko actual HTTPS domain se match hona chahiye; production origin checks strict hain.

Purana code login par `Server returned an unreadable response (403)` dikhaye to server Ctrl+C se band karein, repository root mein `git pull --ff-only` karein, phir app folder se server restart karein. Browser Ctrl+Shift+R se refresh karein. Private `.env`/database delete ya recreate na karein.

- OpenAI **401**: provider ne credential accept nahi kiya. Provider dashboard se nayi poori **secret API key** create karke app mein save karein. Key ID, ChatGPT login password ya subscription ko API secret na samjhein. Fresh key chat mein share na karein.
- Gemini **404**: generation resource/access available nahi; exact cause code se check karein. Google 2.5 access ko existing users tak limited rakhta hai. Neeche current-model instructions dekhein. Test official API se generation-supported models ki read-only list lene ki koshish karta hai. Koi model automatically change nahi hota; 404 se akelay key ki validity confirm nahi hoti.
- Key/model/preference mein changes ke baad pehle **Save** zaroor karein. Test sirf saved configuration use karta hai. Configured provider label API success ka proof nahi; successful test alag status dikhata hai.

## Second founder email wala startup error

`Second founder email already belongs to a paid member` par app band hone ka bug fixed hai. Existing paid member ko automatically free/admin banaye baghair startup ab continue hoti hai; generic optional-founder warning aa sakti hai. Free founder plan assign karna ho to admin login ke baad Team & SaaS Admin se karein, jab free slot available ho.

Apni existing .env aur database preserve karein. Repository update aur server restart kafi hai; setup dobara chalane, database delete karne ya encryption key badalne ki zaroorat nahi.

## Wahi API error dobara aaye

Google ki [current access policy](https://ai.google.dev/gemini-api/docs/deprecations) ke mutabiq Gemini 2.5 ka access pehle se istemal karne walay users tak limited hai. Naye projects ke liye `gemini-3.5-flash-lite` recommended hai. Yeh aapke 404 ki mumkin wajah hai; model catalog mein listed hona generation access ka proof nahi.

Updated app mein 2.5 test par 404 aaye aur catalog mein recommended model ho to **Use gemini-3.5-flash-lite** button model field bhar deta hai. Uske baad **Save Gemini & ChatGPT Keys**, phir **Test saved AI keys** karein. Button key ya model automatically save nahi karta. Existing saved choices aur private `.env` preserve rehte hain. Shared server key ho to operator privately `.env` mein `GEMINI_MODEL` update kare aur web/worker restart kare.

Naye accounts/setup ka default `gemini-3.5-flash-lite` hai. Request se purana optional temperature parameter hata diya gaya hai taake current Gemini models ke saath compatibility rahe. Provider test fail ho to **Provider code** dekhein; raw response ya key share na karein.

OpenAI401 par agar purani saved key hi test ho rahi hai, provider dashboard se **nayi complete secret API key** bana kar app mein Save karein. Key ID ya account password use na karein. Key sirf app/private configuration mein rakhein; chat mein na bhejein. New key ke test ka result dekh kar agla step decide karein.

## OpenAI key active ho lekin invalid_api_key aaye

OpenAI key ka active record/ID aur complete secret value alag hain. API record ka `key_...` ID ya masked value authentication secret ki jagah use nahi hota. App key format ko dekh kar reject nahi karti; official endpoint ke explicit invalid-key401 ke baad hi possible-ID guidance dikhati hai.

App/database se alag check ke liye isi folder mein `.venv/bin/python check_openai_key.py` chalayein. Secret ko hidden prompt mein paste karein; terminal par text nazar nahi aayega aur key save nahi hogi. Checker sirf official OpenAI models endpoint ko GET karta hai. Result401/credential_rejected ho to provider ne is value ko app ke baghair bhi reject kiya. Result200/model_read_access aaye aur app test401 ho to saved value/configuration ko compare karna hoga. Model-list200 chat model access ya billing ka proof nahi hai. Sirf result screenshot share karein, key nahi.

## GitHub se latest update — existing WSL installation

Server wale terminal mein Ctrl+C karein, phir:

```bash
cd ~/bidpilot-v2 &&
git pull --ff-only &&
cd BidPilot_AI_Team_Cloud &&
~/.local/bin/uv pip install --python .venv/bin/python -r requirements.txt &&
.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Browser mein `http://127.0.0.1:8000` kholein aur Ctrl+Shift+R karein. V3 ke liye latest requirements install karein; `.env` aur database preserve karein. Save/test ke dauran repeat clicks block hain; nayi typed key erase nahi hoti aur purani configuration ka late test result current result ke taur par nahi dikhaya jata.

30 October ko di gayi `BidPilot_AI_All_Freelancer_Bids_Pro_FIXED.zip` mein sirf `venv` aur `requirements.txt` thay; application source nahi tha. GitHub ka `downloads/BidPilot_AI_Updated_v2.zip` complete source package hai. Venv, customer database aur private configuration release mein shamil nahi hain; isi liye ZIP ka size 16 MB se chhota hai.
