# BidPilot AI — Ubuntu setup aur zaroori baatein

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
- Gemini **404**: selected model/endpoint available nahi. Updated saved-key test official API se generation-supported models ki read-only list lene ki koshish karta hai. Agar list mile to model ID copy karke Gemini Model mein set, **Save**, phir **Test saved AI keys** karein. Koi model automatically change nahi hota. Discovery fail ho to uska message dekhein; 404 se akelay key ki validity confirm nahi hoti.
- Key/model/preference mein changes ke baad pehle **Save** zaroor karein. Test sirf saved configuration use karta hai. Configured provider label API success ka proof nahi; successful test alag status dikhata hai.

## Second founder email wala startup error

`Second founder email already belongs to a paid member` par app band hone ka bug fixed hai. Existing paid member ko automatically free/admin banaye baghair startup ab continue hoti hai; generic optional-founder warning aa sakti hai. Free founder plan assign karna ho to admin login ke baad Team & SaaS Admin se karein, jab free slot available ho.

Apni existing .env aur database preserve karein. Repository update aur server restart kafi hai; setup dobara chalane, database delete karne ya encryption key badalne ki zaroorat nahi.

## Wahi API error dobara aaye

Gemini ka selected model catalog mein listed ho aur generation404 phir bhi aaye to baar-baar model names na badlein. Updated test mein **Provider code** ke HTTP/status/reason/kind/body fields milte hain; in codes se key restriction, service/project configuration, ya unknown generation access ko alag samajh sakte hain. Codes unknown hon to exact wajah abhi confirm nahi hui. Raw response ya key share karne ki zaroorat nahi.

OpenAI401 par agar purani saved key hi test ho rahi hai, provider dashboard se **nayi complete secret API key** bana kar app mein Save karein. Key ID ya account password use na karein. Key sirf app/private configuration mein rakhein; chat mein na bhejein. New key ke test ka result dekh kar agla step decide karein.

## OpenAI key active ho lekin invalid_api_key aaye

OpenAI key ka active record/ID aur complete secret value alag hain. API record ka `key_...` ID ya masked value authentication secret ki jagah use nahi hota. App key format ko dekh kar reject nahi karti; official endpoint ke explicit invalid-key401 ke baad hi possible-ID guidance dikhati hai.

App/database se alag check ke liye isi folder mein `.venv/bin/python check_openai_key.py` chalayein. Secret ko hidden prompt mein paste karein; terminal par text nazar nahi aayega aur key save nahi hogi. Checker sirf official OpenAI models endpoint ko GET karta hai. Result401/credential_rejected ho to provider ne is value ko app ke baghair bhi reject kiya. Result200/model_read_access aaye aur app test401 ho to saved value/configuration ko compare karna hoga. Model-list200 chat model access ya billing ka proof nahi hai. Sirf result screenshot share karein, key nahi.
