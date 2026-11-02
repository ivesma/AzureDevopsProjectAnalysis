"""Streamlit dashboard over one or more assessment runs.

    pip install "azdo-analyser[dashboard]"
    streamlit run dashboard/streamlit_app.py -- ./assessment
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd
import streamlit as st

st.set_page_config(page_title="Azure DevOps Assessment", layout="wide")

root = Path(sys.argv[1] if len(sys.argv) > 1 else "./assessment")
export_file = root / "Json" / "full-export.json"
if not export_file.exists():
    st.error(f"No assessment found at {export_file}. Run azdo-analyse first, then pass the output folder.")
    st.stop()

data = json.loads(export_file.read_text(encoding="utf-8"))
meta, analysis, snapshot = data["metadata"], data["analysis"], data["snapshot"]
findings = pd.DataFrame(data["findings"])

st.title(f"{meta['project']} · Azure DevOps assessment")
st.caption(f"{meta['organisation']} · generated {meta['generated_at']}")

risk, debt = analysis.get("risk", {}), analysis.get("technical_debt", {})
activity = analysis.get("metrics", {}).get("activity", {})
cols = st.columns(5)
cols[0].metric("Risk score", risk.get("score"), help=f"Grade {risk.get('grade')} (lower is better)")
cols[1].metric("Technical debt", debt.get("score"), help=f"Grade {debt.get('grade')}")
cols[2].metric("Build success", f"{(activity.get('build_success_rate') or 0):.0%}")
cols[3].metric("Deployments / week", activity.get("deployments_per_week"))
cols[4].metric("Findings", len(findings))

tab_findings, tab_activity, tab_inventory, tab_trends = st.tabs(["Findings", "Activity", "Inventory", "Trends"])

with tab_findings:
    if findings.empty:
        st.info("No findings.")
    else:
        sev = st.multiselect("Severity", sorted(findings["severity"].unique()), default=sorted(findings["severity"].unique()))
        cat = st.multiselect("Category", sorted(findings["category"].unique()))
        view = findings[findings["severity"].isin(sev)]
        if cat:
            view = view[view["category"].isin(cat)]
        st.bar_chart(view.groupby("category").size())
        st.dataframe(view[["severity", "category", "resource_type", "resource", "title", "recommendation"]],
                     use_container_width=True, hide_index=True)

with tab_activity:
    for key, label in (("commits_by_week", "Commits per week"), ("builds_by_week", "Pipeline runs per week"),
                       ("deployments_by_week", "Deployments per week")):
        series = activity.get(key) or {}
        st.subheader(label)
        if series:
            st.bar_chart(pd.Series(series))
        else:
            st.caption("No data in window.")

with tab_inventory:
    st.subheader("Pipelines")
    st.dataframe(pd.DataFrame([{"name": p["name"], "type": p["type"], "folder": p["folder"],
                                "runs": p["runs"].get("total"), "success_rate": p["runs"].get("success_rate")}
                               for p in snapshot.get("pipelines", [])]), use_container_width=True, hide_index=True)
    st.subheader("Repositories")
    st.dataframe(pd.DataFrame([{"name": r["name"], "last_commit": r.get("last_commit_date"),
                                "commits": r.get("commits_in_window"), "policies": r["default_branch_policies"]["count"]}
                               for r in snapshot.get("repositories", [])]), use_container_width=True, hide_index=True)

with tab_trends:
    history = sorted((root / "History").glob("run-*.json"))
    rows = [json.loads(p.read_text(encoding="utf-8")) for p in history]
    if len(rows) < 2:
        st.info("Run the assessment again into the same folder to see trends.")
    else:
        df = pd.DataFrame([{"run": r["generated_at"], "risk": r.get("risk_score"), "debt": r.get("technical_debt")}
                           for r in rows]).set_index("run")
        st.subheader("Risk score over time")
        st.line_chart(df["risk"])
        st.subheader("Technical debt over time")
        st.line_chart(df["debt"])
        drift = (analysis.get("trends") or {}).get("drift") or {}
        st.subheader("Architectural drift since previous run")
        st.json(drift) if drift else st.caption("No dependency changes.")
