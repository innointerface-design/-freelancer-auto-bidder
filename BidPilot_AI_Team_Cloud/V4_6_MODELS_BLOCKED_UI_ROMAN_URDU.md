# BidPilot v4.6 — models, Blocked projects aur design

3 November 2026

## Kya badla

- Sidebar mein **Blocked** page: payment false, missing ya unknown project yahan aata hai; Opportunities aur top matches mein nahi. Aapki final clarification ke mutabiq yeh sirf payment verification ka rule hai: email, identity ya phone status akelay payment-verified project ko Blocked nahi karte.
- Manual submit, two-step confirm aur automatic dispatch same verification rule follow karte hain. Aage ki successful scan verification update kare to project wapas Opportunities mein aa sakta hai. Purane saved records mein payment status missing ho to rescan tak Blocked rahega.
- Settings mein saved Gemini/OpenAI API keys ke liye dynamic text-model catalog aur **Refresh models**. Normal refresh cache 6 hours, manual refresh ke remote calls mein minimum 60 seconds. Naye accessible model IDs list mein aa sakte hain; saved selection automatically replace nahi hoti.
- Catalog outage par purani list stale status ke saath rehti hai. List access generation access, billing ya endpoint compatibility ka proof nahi hai. **Test saved AI keys** se selected model test karein. ChatGPT subscription aur OpenAI API access alag hain.
- Proposal editor mein actual AI provider/model, local fallback, busy state aur cooldown/failure ka clear status. Paid request ko same provider par automatically repeat nahi kiya jata. Auto mode existing second provider try kar sakta hai.
- **AI Write Proposal** ab saved template ke bawajood selected provider try karta hai; automatic worker ke saved-template rules preserve hain. AI unavailable ho to fallback ka source clear hai aur existing edited proposal overwrite nahi hoti.
- Dark/purple design preserve: consistent local SVG icons, readable labels/line spacing, aligned forms/actions, clean plan summary, receiving-account cards aur responsive payment history.
- Model fields 120 characters tak store ho sakti hain. Existing PostgreSQL short columns startup migration se widen hoti hain; SQLite account data ko rebuild nahi kiya jata.

## Models ka scope

Proposal aur chat ke liye text candidates dikhte hain. Image, audio, embedding aur realtime-only models is editor ke liye nahi hain. Provider ke API catalog aur current generation protocol se models discover/use hote hain. Future provider breaking API changes ko code update ki zaroorat ho sakti hai; har future model ko bina testing guaranteed compatible kehna sahi nahi hoga.

Model-list request read-only hai. Model selection badalne se provider ki pricing aur latency badal sakti hai. List refresh khud proposal generate nahi karta. Personal keys encrypted rehti hain; browser ko key values nahi bheji jati.

Official OpenAI Codex/Pro aur deep-research family IDs ke liye Responses request route available hai; normal models aur compatible provider hosts apna Chat Completions route rakhte hain. Operator `OPENAI_API_MODE=responses` ya `chat_completions` se protocol explicitly choose kar sakta hai; default `auto` hai. Responses requests mein `store=false`, bounded output aur no tools hain. Deep-research jaise specialized models ko tools/data sources chahiye ho sakte hain; yeh editor un tools ko enable nahi karta. Short proposal output budget mein model complete reply na de to failure clear hota hai; app silently cost/output budget nahi barhati.

## Plan aur automation controls

Team panel ka **Expire plan** paid access band karta hai; account login/password preserve rehte hain. **Disable** account login band karta hai. **Pause Auto / Allow Auto** admin permission hai; user opt-in, active plan, verified project, connected account aur online worker bhi zaroori hain. Automatic expiry worker ke saath chalti rehti hai. Payment approval administrator settled payment verify karne ke baad karta hai.

## Existing installation update

Pehle app aur worker stop karein, aur poore folder ka private backup rakhein. Existing `.env`, database aur `FERNET_KEY` preserve karein. Uncommitted local code ho to `git pull` overwrite nahi karega; local code ka backup/stash pehle karein. Private environment/database files GitHub par upload na karein.

Ubuntu/WSL mein clean checkout ke liye:

```bash
cd ~/bidpilot-v2 &&
git pull --ff-only &&
cd BidPilot_AI_Team_Cloud &&
~/.local/bin/uv pip install --python .venv/bin/python -r requirements.txt &&
.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Doosri WSL window:

```bash
cd ~/bidpilot-v2/BidPilot_AI_Team_Cloud &&
.venv/bin/python -m app.worker
```

Browser refresh `Ctrl+Shift+R`. GitHub upload se aapke PC par code apne aap install nahi hota.

Provider references: [OpenAI model list](https://developers.openai.com/api/reference/resources/models/methods/list), [Gemini model metadata](https://aistudio.google.com/api-reference/models).

Verification results are recorded in [REVIEW_AND_TESTS.md](REVIEW_AND_TESTS.md). Live provider credentials, real bid acceptance and actual payment settlement are not exercised by synthetic regression checks.
