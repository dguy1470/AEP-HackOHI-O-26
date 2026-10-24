"""Headless smoke test: exercises the full pipeline against the real data
without launching Streamlit. Prints a compact report."""
from concierge.data_loader import load_data
from concierge.risk_model import predict_risk
from concierge.restoration_model import estimate_restoration
from concierge import explain as explain_mod
from concierge.recommendations import build_guidance
from concierge.alerts import evaluate_alert
from concierge.nl import compose_messages

data = load_data()
print("=== DATA LOAD ===")
print("loaded:", data.loaded)
print("warnings:", data.warnings)
print("damage rows:", len(data.damage))
print("customer rows:", len(data.customers))
print("circuit profiles:", len(data.circuit_profile))
print("priority points:", len(data.priority_points))
circuits = data.circuits()
print("num circuits:", len(circuits), "sample:", circuits[:5])

if not data.damage.empty:
    print("\n=== RESTORE HOURS (empirical) ===")
    rh = data.damage["restore_hours"].dropna()
    print("count:", len(rh),
          "median:", round(rh.median(), 2) if len(rh) else None,
          "p25/p75:", (round(rh.quantile(.25), 2), round(rh.quantile(.75), 2)) if len(rh) else None)

test_circuit = circuits[0] if circuits else "CIR1"
print(f"\n=== PIPELINE for circuit={test_circuit} ===")
risk = predict_risk(test_circuit, 0.8, data.circuit_profile)
print("risk band:", risk.band, "| score:", round(risk.score, 3),
      "| confidence:", risk.confidence, "| samples:", risk.sample_size)
print("top factor:", risk.factors[0]["label"] if risk.factors else None)

rest = estimate_restoration(
    circuit=test_circuit, asset_type="POLE", damage_quantity=3,
    access_issue=True, special_equipment=False, damage=data.damage)
print("restoration:", rest.pretty_range(), "| basis:", rest.basis,
      "| samples:", rest.sample_size)

risk_expl = explain_mod.explain_risk(risk)
print("risk headline:", risk_expl["headline"])

guidance = build_guidance(risk_band=risk.band, restore_hours=rest.point_hours,
                          is_critical=False, has_medical_need=True)
print("guidance items:", len(guidance.items))

alert = evaluate_alert(risk_band=risk.band, is_critical=False,
                       has_medical_need=True)
print("alert eligible:", alert.eligible, "| channels:", alert.channels)

msgs = compose_messages(
    risk_band=risk.band, confidence=risk.confidence,
    restore_range=rest.pretty_range(),
    primary_driver=risk_expl["primary_driver"],
    top_actions=[r.text for r in guidance.sorted_items()[:3]])
print("\n--- pre_storm message ---")
print(msgs["pre_storm"])

print("\n=== PRIVACY CHECK ===")
blob = " ".join(msgs.values())
from concierge import config
leaks = [f for f in config.PRIVACY_BLOCKLIST if f.lower() in blob.lower()]
print("blocklisted tokens present in messages:", leaks or "none")
print("\nOK: pipeline ran end-to-end.")
