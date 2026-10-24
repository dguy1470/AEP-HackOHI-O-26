# AI Outage Concierge

**HACKOHI/O 2026 — AEP Challenge**
*Predict · Explain · Prepare · Communicate*

A focused, explainable prototype that turns anonymized outage/damage,
geospatial, network, and restoration data into trusted guidance for a customer
facing an approaching storm. It answers the four questions the challenge asks,
through **two interfaces**: a structured **Dashboard** and a conversational
**Chat** tab.

- *"Will I lose power?"* → an outage-risk band with a confidence level
- *"How long could restoration take?"* → a calibrated restoration-time **range**
- *"What should I do to prepare?"* → tailored safety & preparation guidance
- *"Do I qualify for outage alerts?"* → alert eligibility + a proactive message

### Chat with the Concierge
The **Chat** tab lets a customer type questions in plain language ("Will I lose
power?", "How long until it's back?"). A lightweight intent router maps the
question to the same engine the dashboard uses, so answers always reflect the
current sidebar scenario and the real computed numbers, never invented values.

- **Default (no key):** deterministic, grounded answers. Works offline, zero
  cost, nothing to configure. Ideal for a reliable live demo.
- **With `OPENAI_API_KEY`:** an LLM rephrases the grounded answer
  conversationally. It is explicitly instructed not to change any number, risk
  level, or time range, and only ever sees the already-computed,
  privacy-filtered facts.

## Quick start

```bash
pip install -r requirements.txt
streamlit run app.py
```

Then open http://localhost:8501 and change any input in the sidebar — every
output updates live (built for the "let judges change a scenario input and
observe the response" requirement).

Optional: set `OPENAI_API_KEY` to enable an LLM "polish" pass over the
customer messages. It is **off by default**; the app is fully functional and
deterministic without it.

## What the data gives us

The app reads the organizer shapefiles in `data/Data1` and `data/Data2`
(WGS84 / Web Mercator). The key signals:

| Source | What we use |
| --- | --- |
| `arcos_prod_damage_09_28_Clip` | Damage records with asset type, access/special-equipment flags, `NEAR_DIST` to the primary line, and **report → repaired timestamps** that give real restoration durations. |
| `Customer_Clip` | Anonymized premises keyed by circuit, with `critical` and `special_op` (declared) flags. |
| `Primary / Pole / Fuse / Transformer / Switch / Recloser / Secondary` | Network context and asset exposure. |
| `Hospitals / Fire / Ambulance` | Priority-facility **proximity only** — names and addresses are never surfaced. |

Substation, circuit, and customer identifiers are already anonymized upstream
(e.g. `CIR1…CIR115`, `STATION1…33`). We treat the data as the city it clearly
is only internally; nothing identifying reaches the customer view.

## How it works

```
app.py                      Streamlit UI — the end-to-end customer journey
concierge/
  config.py                 Paths, schema maps, weights, thresholds (all tunable)
  data_loader.py            Reads shapefiles, normalizes CRS, builds analysis frames
  risk_model.py             Transparent weighted risk score → band + confidence
  restoration_model.py      Restoration range (empirical if data allows, else baseline)
  recommendations.py        Risk/duration/need-tailored prep & safety guidance
  alerts.py                 Alert eligibility, timing/channel/escalation
  explain.py                Plain-language explanations + privacy filter
  nl.py                     Deterministic message templates (+ optional LLM polish)
  chat.py                   Intent router + grounded chat answers (+ optional LLM)
smoke_test.py               Headless end-to-end check (no UI)
chat_test.py                Headless check of the chat router
```

### Outage risk (transparent, explainable)
A composite score in `[0,1]` from four individually-explainable signals:
storm severity, historical damage density on the circuit, tree/overhead asset
exposure, and nearness to past damage. Weights live in `config.RISK_WEIGHTS`.
The score maps to **Low / Moderate / Elevated / High**, and confidence
reflects how much real history backs it.

### Restoration time (calibrated range, not false precision)
When enough comparable past repairs exist (same circuit/asset), the range is
the **25th–75th percentile of observed `Repaired − Reported` durations**.
Otherwise it falls back to a clearly-labeled representative baseline. Damage
quantity, access difficulty, special equipment, and travel/non-productive time
all shift the range, and the UI lists what moved it.

### Responsible use
- **Privacy:** a blocklist (`config.PRIVACY_BLOCKLIST`) strips addresses, meter
  numbers, crew details, and precise facility names/locations; every outgoing
  message is re-filtered before display.
- **Confirmed vs. estimated:** messages separate confirmed facts, model
  estimates, and customer actions.
- **Uncertainty & limits:** risk shows confidence; restoration is a range; when
  history is too thin the app says so and degrades gracefully.

## Notes / known data quirks
- Restoration timestamps in the sample appear day-quantized (durations cluster
  at 24/48h), so empirical ranges are coarse. This is a property of the data,
  surfaced as an assumption rather than hidden.
- The damage file and customer file use different circuit-coding schemes
  (`CIRn` vs `0000nnn`); risk is computed on the damage-circuit space, and the
  loader degrades gracefully if a selected circuit has no history.

## Verify without the UI
```bash
python smoke_test.py
```
Prints data-load counts, a sample prediction, the generated messages, and a
privacy check.
