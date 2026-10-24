"""Headless test of the chat router against the real engine."""
from concierge.data_loader import load_data
from concierge.risk_model import predict_risk
from concierge.restoration_model import estimate_restoration
from concierge.recommendations import build_guidance
from concierge.alerts import evaluate_alert
from concierge.chat import ChatContext, respond, classify

data = load_data()
circuit = data.circuits()[0] if data.circuits() else "CIR1"
risk = predict_risk(circuit, 0.8, data.circuit_profile)
rest = estimate_restoration(circuit=circuit, asset_type="POLE",
                            damage_quantity=3, access_issue=True,
                            special_equipment=False, damage=data.damage)
guidance = build_guidance(risk_band=risk.band, restore_hours=rest.point_hours,
                          is_critical=False, has_medical_need=True)
alert = evaluate_alert(risk_band=risk.band, is_critical=False,
                       has_medical_need=True)
ctx = ChatContext(circuit=circuit, risk=risk, restoration=rest,
                  guidance=guidance, alert=alert)

questions = [
    "Will I lose power?",
    "How long until the power is back on?",
    "What should I do to prepare?",
    "Do I qualify for alerts?",
    "hello there",
    "what can you do",
    "asdfqwer nonsense",
]
for q in questions:
    intent, ans = respond(q, ctx)
    print(f"\nQ: {q}\n  intent={intent}\n  A: {ans[:160].replace(chr(10),' ')}")

print("\nOK: chat router ran end-to-end.")
