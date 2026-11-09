"""AI Audit Companion.   Run:  streamlit run app.py"""

import html
import logging
import os
import time

import streamlit as st

import ui_helpers as ui

# Errors go to a log file for the administrator, not onto the page.
logging.basicConfig(filename="audit_app.log", level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("audit_app")

# Change the palette here (also see .streamlit/config.toml).
PRIMARY, INK = "#1F3B4D", "#111111"
STATUS = {  # text colour, background tint, label
    "exception": ("#B3261E", "#FCEBEA", "Exception"),
    "observation": ("#8A5A00", "#FFF4DB", "Observation"),
    "clean": ("#1B7A4B", "#E6F5EC", "No exception"),
}
FOLDERS_PER_REQUEST = 5   # fixed on purpose: not something the user needs to choose
DOMAINS = ["Change Management", "Access Management", "Incident Management"]   # only the first one works today

st.set_page_config(page_title="AI Audit Companion", page_icon="🧾", layout="wide")
st.markdown(f"""
<style>
.hero {{ background: {PRIMARY}; color: white; padding: 1.6rem 1.8rem;
        border-radius: 14px; margin-bottom: 1.2rem; }}
.hero h1 {{ color: white; margin: 0; font-size: 2rem; }}
.hero p {{ margin: .4rem 0 0; opacity: .92; }}
.tile {{ background: #FFFFFF; border: 1px solid #D9DDE0; border-radius: 12px; padding: .9rem 1rem; text-align: center; }}
.tile .n {{ font-size: 2rem; font-weight: 700; line-height: 1.1; }}
.tile .l {{ font-size: .8rem; color: #555555; }}
.row {{ border: 1px solid #D9DDE0; border-left-width: 6px; border-radius: 10px; padding: .7rem 1rem; margin-bottom: .6rem; }}
.row .top {{ display: flex; justify-content: space-between; align-items: center; gap: .6rem; flex-wrap: wrap; }}
.card {{ background: #FFFFFF; border: 1px solid #D9DDE0; border-radius: 14px; padding: 1.1rem 1.2rem; margin-bottom: 1rem; }}
.card h4 {{ margin: 0 0 .6rem; color: {PRIMARY}; font-size: 1.05rem; }}
.step {{ display: flex; gap: .7rem; align-items: flex-start; margin-bottom: .7rem; font-size: .92rem; color: {INK}; }}
.num {{ background: {PRIMARY}; color: white; border-radius: 50%; min-width: 1.6rem; height: 1.6rem; display: flex;
        align-items: center; justify-content: center; font-weight: 700; font-size: .85rem; }}
.chip {{ display: inline-block; background: white; border: 1px solid #C9D1D6; color: {PRIMARY}; font-weight: 600;
        border-radius: 999px; padding: .1rem .7rem; margin: 0 .3rem .3rem 0; font-size: .8rem; }}
.flow {{ display: flex; align-items: center; justify-content: space-between; position: relative;
        margin: 0 0 1.4rem; padding: .2rem .4rem; }}
.flow::before {{ content: ""; position: absolute; left: 6%; right: 6%; top: 1.05rem; height: 2px; background: #D9DDE0; }}
.flow::after {{ content: ""; position: absolute; top: .78rem; left: 6%; width: 10px; height: 10px; border-radius: 50%;
        background: {PRIMARY}; animation: travel 10s linear infinite; }}
.node {{ position: relative; z-index: 1; text-align: center; width: 20%; font-size: .78rem; color: #555555; }}
.node .dot {{ width: 2.1rem; height: 2.1rem; margin: 0 auto .3rem; border-radius: 50%; background: #FFFFFF;
        border: 2px solid #C9D1D6; display: flex; align-items: center; justify-content: center; font-weight: 700;
        animation: light 10s infinite; }}
.node:nth-child(1) .dot {{ animation-delay: 0s; }} .node:nth-child(2) .dot {{ animation-delay: 2s; }}
.node:nth-child(3) .dot {{ animation-delay: 4s; }} .node:nth-child(4) .dot {{ animation-delay: 6s; }}
.node:nth-child(5) .dot {{ animation-delay: 8s; }}
@keyframes light {{ 0%, 20% {{ background: {PRIMARY}; color: #FFFFFF; border-color: {PRIMARY}; transform: scale(1.12); }}
                   21%, 100% {{ background: #FFFFFF; color: #111111; border-color: #C9D1D6; transform: scale(1); }} }}
@keyframes travel {{ from {{ left: 6%; }} to {{ left: 94%; }} }}
/* dropdown: every domain except the first is greyed out and cannot be clicked */
ul[role="listbox"] li[role="option"]:nth-child(n+2) {{ color: #A9B0B5 !important; pointer-events: none; cursor: not-allowed; }}
.tree {{ white-space: pre; font-family: monospace; font-size: .85rem; line-height: 1.5; color: {PRIMARY}; }}
.pills {{ display: flex; gap: .4rem; flex-wrap: wrap; justify-content: flex-end; }}
.pill {{ font-size: .75rem; font-weight: 600; padding: .15rem .6rem; border-radius: 999px; }}
.row ul {{ margin: .4rem 0 0 1rem; padding: 0; color: {INK}; font-size: .9rem; }}
</style>
<div class="hero"><h1>AI Audit Companion</h1>
<p>Upload your evidence and get an audit-ready testing table in minutes.</p></div>
<div class="flow">
<div class="node"><div class="dot">1</div>Upload evidence</div>
<div class="node"><div class="dot">2</div>Read documents</div>
<div class="node"><div class="dot">3</div>Check workflow</div>
<div class="node"><div class="dot">4</div>Check segregation of duties</div>
<div class="node"><div class="dot">5</div>Audit report</div>
</div>
""", unsafe_allow_html=True)


def get_api_key() -> str:
    """The key lives on the server (Streamlit secrets or an environment variable), never on the page."""
    try:
        return st.secrets["GEMINI_API_KEY"]
    except Exception:
        return os.environ.get("GEMINI_API_KEY", "")


# ---------------------------------------------------------------- layout: guide on the left, work on the right
left, right = st.columns([1, 2], gap="large")

with left:
    st.caption("DOMAIN")
    domain = st.selectbox("Domain", DOMAINS, label_visibility="collapsed")
    st.markdown(f"""
<div class="card"><h4>How it works</h4>
<div class="step"><div class="num">1</div><div>Put each change request's evidence in its <b>own folder</b>.</div></div>
<div class="step"><div class="num">2</div><div>Zip all the folders into <b>one ZIP file</b> and upload it.</div></div>
<div class="step"><div class="num">3</div><div>The evidence is read and checked against the audit rules.</div></div>
<div class="step"><div class="num">4</div><div>Review the summary and download the full testing table.</div></div>
</div>
<div class="card"><h4>Accepted files</h4>
<span class="chip">PDF</span><span class="chip">JPG</span><span class="chip">JPEG</span><span class="chip">PNG</span>
<div style="font-size:.85rem;margin-top:.4rem;color:#555555">Other file types are ignored.</div></div>
""", unsafe_allow_html=True)
    st.markdown("""
<div class="card"><h4>Sample of how your ZIP file can look</h4>
<div class="tree">evidence.zip
├── CHG-1001/
│   ├── initiation.pdf
│   ├── development.png
│   └── deployment.jpg
└── CHG-1002/
    └── ...</div></div>
""", unsafe_allow_html=True)

with right:
    if domain != DOMAINS[0]:   # safety net: the other domains are greyed out and should not be selectable
        st.info("This domain is not available yet. Please choose Change Management.")
    else:
        st.subheader("Upload your evidence")
        upload = st.file_uploader("Choose your ZIP file", type=["zip"])
        if st.button("Start evaluation", type="primary", disabled=upload is None):
            key = get_api_key()
            if not key:
                log.error("No API key configured")
                st.error("The service is not set up yet. Please contact the administrator.")
            else:
                os.environ["GEMINI_API_KEY"] = key
                try:
                    with st.spinner("Evaluation in process. This can take a few minutes..."):
                        evaluations, json_text = ui.run_extraction_on_zip(upload.getvalue(), FOLDERS_PER_REQUEST)
                    st.session_state["evaluations"] = evaluations
                    st.session_state["json_text"] = json_text
                except Exception:
                    ref = time.strftime("%H%M%S")
                    log.exception("Evaluation failed (ref %s)", ref)  # full details go to audit_app.log
                    st.error(f"The evaluation could not be completed. Please try again in a few minutes. (Reference {ref})")

        evaluations = st.session_state.get("evaluations")
        if evaluations:
            st.divider()
            st.subheader("Results")
            s = ui.summary(evaluations)
            tiles = [("Total changes", s["changes"], PRIMARY), ("With exceptions", s["with_exceptions"], STATUS["exception"][0]),
                     ("SoD failures", s["sod_failures"], STATUS["exception"][0]),
                     ("Workflow not followed", s["workflow"], STATUS["exception"][0]),
                     ("Unreadable evidence", s["unreadable"], STATUS["observation"][0])]
            for col, (label, n, colour) in zip(st.columns(5), tiles):
                col.markdown(f'<div class="tile"><div class="n" style="color:{colour}">{n}</div><div class="l">{label}</div></div>',
                             unsafe_allow_html=True)

            st.write("")
            chosen = st.multiselect("Filter by exception type", ui.exception_choices(evaluations))
            for ev in ui.filter_evaluations(evaluations, chosen):
                status = ui.change_status(ev)
                colour, tint, label = STATUS[status]
                labels = ev.exception_types if status == "exception" else [label]
                pills = "".join(f'<span class="pill" style="color:{colour};background:{tint}">{html.escape(l)}</span>'
                                for l in labels)
                st.markdown(
                    f'<div class="row" style="border-left-color:{colour}"><div class="top">'
                    f'<b>{html.escape(ev.change_id)}</b><span class="pills">{pills}</span></div></div>',
                    unsafe_allow_html=True)

            st.download_button("Download full testing table (Excel)", ui.excel_bytes(evaluations),
                               file_name="audit_testing_table.xlsx", type="primary",
                               mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

# ---------------------------------------------------------------- advanced
with st.expander("Advanced"):
    saved = st.file_uploader("Open a saved extraction file (no new evaluation is run)", type=["json"], key="saved")
    if saved is not None and st.button("Load saved file"):
        try:
            text = saved.getvalue().decode("utf-8")
            st.session_state["evaluations"] = ui.evaluations_from_extraction_json(text)
            st.session_state["json_text"] = text
            st.rerun()
        except Exception:
            log.exception("Could not read saved extraction file")
            st.error("That file could not be read.")
    if st.session_state.get("json_text"):
        st.download_button("Download extraction file (JSON)", st.session_state["json_text"],
                           file_name="extraction_output.json", mime="application/json")
