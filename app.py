"""AI Outage Concierge - Streamlit demo (HACKOHI/O 2026, AEP Challenge).

End-to-end customer journey:
  1. Select an anonymized location (circuit).
  2. Get an outage-risk prediction with confidence + explanation.
  3. Explore damage / restoration scenarios -> restoration range updates live.
  4. Get tailored preparation & safety guidance.
  5. See alert eligibility + a proactive, plain-language message.
  6. Everything shows how privacy, safety, uncertainty, and limits are handled.
"""
from __future__ import annotations

import streamlit as st

from concierge import config
from concierge.data_loader import load_data
from concierge.risk_model import predict_risk
from concierge.restoration_model import estimate_restoration
from concierge import explain as explain_mod
from concierge.recommendations import build_guidance
from concierge.alerts import evaluate_alert
from concierge.nl import compose_messages
from concierge.chat import ChatContext, respond


st.set_page_config(page_title="AI Outage Concierge", page_icon="\u26a1", layout="wide")


@st.cache_resource(show_spinner="Loading challenge data...")
def _data():
    return load_data()


def _band_color(band: str) -> str:
    return {
        "Low": "#2e7d32",
        "Moderate": "#f9a825",
        "Elevated": "#ef6c00",
        "High": "#c62828",
    }.get(band, "#555")


def main() -> None:
    data = _data()

    st.title("\u26a1 AI Outage Concierge")
    st.caption("Predict \u00b7 Explain \u00b7 Prepare \u00b7 Communicate  "
               "\u2014  HACKOHI/O 2026 AEP Challenge prototype")

    if data.warnings:
        with st.expander("Data status / warnings", expanded=not data.loaded):
            for w in data.warnings:
                st.warning(w)

    # ---------------- Sidebar: the scenario inputs judges can change --------
    st.sidebar.header("Scenario inputs")
    st.sidebar.caption("Judges: change any input and watch every output update.")

    circuits = data.circuits()
    if circuits:
        circuit = st.sidebar.selectbox("Anonymized circuit / location", circuits)
    else:
        circuit = st.sidebar.text_input("Anonymized circuit id", "CIR1")
        st.sidebar.info("No circuit list loaded; enter an id manually.")

    storm_label = st.sidebar.select_slider(
        "Incoming storm severity",
        options=list(config.STORM_LEVELS.keys()),
        value="Severe storm / high wind",
    )
    storm_severity = config.STORM_LEVELS[storm_label]

    st.sidebar.divider()
    st.sidebar.subheader("Damage / restoration scenario")
    asset_type = st.sidebar.selectbox(
        "Primary damaged asset",
        ["POLE", "TRANSFORMER", "CONDUCTOR", "FUSE", "RECLOSER", "SWITCH", "SERVICE"],
    )
    damage_quantity = st.sidebar.slider("Number of damaged assets", 1, 25, 3)
    access_issue = st.sidebar.checkbox("Access difficulty (blocked roads, terrain)")
    special_equipment = st.sidebar.checkbox("Special equipment required")

    st.sidebar.divider()
    st.sidebar.subheader("Declared customer needs")
    st.sidebar.caption("Used as declared only \u2014 never inferred.")
    is_critical = st.sidebar.checkbox("Critical location")
    has_medical_need = st.sidebar.checkbox("Medical / life-support need")

    # ---------------- Run the pipeline --------------------------------------
    risk = predict_risk(circuit, storm_severity, data.circuit_profile)
    restoration = estimate_restoration(
        circuit=circuit,
        asset_type=asset_type,
        damage_quantity=damage_quantity,
        access_issue=access_issue,
        special_equipment=special_equipment,
        damage=data.damage,
    )
    risk_expl = explain_mod.explain_risk(risk)
    rest_expl = explain_mod.explain_restoration(restoration)
    guidance = build_guidance(
        risk_band=risk.band,
        restore_hours=restoration.point_hours,
        is_critical=is_critical,
        has_medical_need=has_medical_need,
    )
    alert = evaluate_alert(
        risk_band=risk.band,
        is_critical=is_critical,
        has_medical_need=has_medical_need,
    )
    messages = compose_messages(
        risk_band=risk.band,
        confidence=risk.confidence,
        restore_range=restoration.pretty_range(),
        primary_driver=risk_expl["primary_driver"],
        top_actions=[r.text for r in guidance.sorted_items()[:3]],
    )

    # ---------------- Top-level tabs: Dashboard + Chat ----------------------
    dash_tab, chat_tab = st.tabs(["\U0001f4ca Dashboard", "\U0001f4ac Chat with the Concierge"])

    with chat_tab:
        render_chat(ChatContext(
            circuit=circuit, risk=risk, restoration=restoration,
            guidance=guidance, alert=alert))

    with dash_tab:
        render_dashboard(
            data=data, risk=risk, restoration=restoration,
            risk_expl=risk_expl, rest_expl=rest_expl, guidance=guidance,
            alert=alert, messages=messages)


def render_dashboard(*, data, risk, restoration, risk_expl, rest_expl,
                     guidance, alert, messages) -> None:
    """The structured, input-driven view."""
    col1, col2 = st.columns([1, 1])

    with col1:
        st.subheader("1 \u00b7 Outage risk")
        st.markdown(
            f"<div style='padding:14px;border-radius:10px;"
            f"background:{_band_color(risk.band)};color:white;font-size:20px;'>"
            f"<b>{risk.band} risk</b> &nbsp; \u2022 &nbsp; confidence: "
            f"{risk.confidence}</div>",
            unsafe_allow_html=True,
        )
        st.progress(min(risk.score, 1.0), text=f"Composite score {risk.score:.2f}")

        st.markdown("**Why this prediction?**")
        for b in risk_expl["bullets"]:
            st.write(f"- {b}")
        st.info(risk_expl["uncertainty"])
        for a in risk_expl["assumptions"]:
            st.caption(f"Assumption: {a}")

    with col2:
        st.subheader("2 \u00b7 Restoration estimate")
        st.markdown(
            f"<div style='padding:14px;border-radius:10px;background:#263238;"
            f"color:white;font-size:20px;'>Estimated window: "
            f"<b>{restoration.pretty_range()}</b></div>",
            unsafe_allow_html=True,
        )
        st.caption(f"Basis: {restoration.basis}"
                   + (f" ({restoration.sample_size} past repairs)"
                      if restoration.sample_size else ""))
        st.markdown("**What moves this estimate?**")
        for d in rest_expl["drivers"]:
            st.write(f"- {d}")
        st.warning(rest_expl["caveat"])
        for a in rest_expl["assumptions"]:
            st.caption(f"Assumption: {a}")

    st.divider()

    col3, col4 = st.columns([1, 1])

    with col3:
        st.subheader("3 \u00b7 Preparation & safety")
        for r in guidance.sorted_items():
            icon = {"emergency": "\U0001f6a8", "safety": "\u26a0\ufe0f",
                    "prep": "\u2705"}.get(r.kind, "\u2022")
            st.write(f"{icon} {r.text}")

    with col4:
        st.subheader("4 \u00b7 Alerts & proactive communication")
        if alert.eligible:
            st.success("This scenario **qualifies** for a proactive alert.")
        else:
            st.info("This scenario does **not** currently qualify for an alert.")
        for r in alert.reasons:
            st.caption(f"\u2022 {r}")
        if alert.eligible:
            st.write(f"**Timing:** {alert.timing}")
            st.write(f"**Channels:** {', '.join(alert.channels)}")
            if alert.escalation:
                st.write("**Escalation triggers:**")
                for e in alert.escalation:
                    st.write(f"- {e}")

    st.divider()
    st.subheader("5 \u00b7 Customer messages (plain language)")
    tabs = st.tabs(["Pre-storm", "Outage confirmed", "Estimate updated",
                    "Restoration complete"])
    for tab, key in zip(tabs, ["pre_storm", "outage_confirmed",
                               "estimate_updated", "restoration_complete"]):
        with tab:
            st.code(messages[key], language="text")

    st.divider()
    with st.expander("6 \u00b7 Responsible use: privacy, safety, uncertainty, limits"):
        st.markdown(
            "- **Privacy:** customer addresses, meter numbers, crew details, "
            "and precise facility names/locations are blocked from all output "
            "and re-filtered before display.\n"
            "- **Confirmed vs. estimated:** messages separate confirmed facts "
            "from model estimates and customer actions.\n"
            "- **Uncertainty:** risk shows a confidence level; restoration is a "
            "range, never a false-precision promise.\n"
            "- **Model limits:** when history is too thin, the app says so and "
            "falls back to clearly-labeled baselines.\n"
            f"- **LLM polish:** {'ON' if config.llm_enabled() else 'OFF'} "
            "(set OPENAI_API_KEY to enable optional rephrasing of the "
            "deterministic templates)."
        )

    # ---------------- Map (geospatial context) -----------------------------
    if not data.priority_points.empty or not data.damage.empty:
        st.divider()
        st.subheader("Geospatial context")
        st.caption("Damage points and priority-facility areas (facility names "
                   "intentionally omitted).")
        map_df = data.damage.dropna(subset=["lat", "lon"])[["lat", "lon"]].head(2000)
        if not map_df.empty:
            st.map(map_df, size=10)


def render_chat(ctx: ChatContext) -> None:
    """Conversational view: type a question, get a grounded plain-language answer."""
    mode = "LLM-phrased" if config.llm_enabled() else "deterministic"
    st.subheader("\U0001f4ac Chat with the Concierge")
    st.caption(
        f"Ask about your outage risk, restoration time, how to prepare, or "
        f"alerts. Answers reflect the current sidebar scenario and are "
        f"**grounded in the engine** (mode: {mode}). "
        f"Set OPENAI_API_KEY to enable conversational phrasing."
    )

    # Seed the conversation once.
    if "chat_history" not in st.session_state:
        st.session_state.chat_history = [
            {"role": "assistant",
             "content": ("Hi! I'm your outage concierge. Try asking "
                         "\u201cWill I lose power?\u201d or "
                         "\u201cHow long could restoration take?\u201d")}
        ]

    # Quick-question buttons.
    st.write("Quick questions:")
    qcols = st.columns(4)
    quick = [
        ("Will I lose power?", qcols[0]),
        ("How long to restore?", qcols[1]),
        ("How do I prepare?", qcols[2]),
        ("Do I get alerts?", qcols[3]),
    ]
    pending = None
    for text, col in quick:
        if col.button(text, use_container_width=True):
            pending = text

    # Render history.
    for msg in st.session_state.chat_history:
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])

    # Free-text input.
    typed = st.chat_input("Ask the concierge a question...")
    user_q = pending or typed

    if user_q:
        st.session_state.chat_history.append({"role": "user", "content": user_q})
        with st.chat_message("user"):
            st.markdown(user_q)
        intent, answer = respond(user_q, ctx)
        with st.chat_message("assistant"):
            st.markdown(answer)
            st.caption(f"Detected intent: {intent}")
        st.session_state.chat_history.append(
            {"role": "assistant", "content": answer})


if __name__ == "__main__":
    main()
