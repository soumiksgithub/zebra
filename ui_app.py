import json
import os
from datetime import datetime
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

import requests
import streamlit as st
from dotenv import load_dotenv

load_dotenv()

# ============================================================
# Config
# ============================================================
DEFAULT_API_BASE = os.getenv("TESTGEN_API_BASE", "http://localhost:8000")
DRAFT_DIR = os.getenv("TESTGEN_UI_DRAFT_DIR", "./ui_drafts")
os.makedirs(DRAFT_DIR, exist_ok=True)

st.set_page_config(
    page_title="QA Agent – Test Generator",
    page_icon="🧪",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ============================================================
# Helpers
# ============================================================
def api_post(base_url: str, path: str, payload: Dict[str, Any], timeout: int = 180) -> Dict[str, Any]:
    url = base_url.rstrip("/") + path
    r = requests.post(url, json=payload, timeout=timeout)
    try:
        data = r.json()
    except Exception:
        data = {"raw_text": r.text}
    if not r.ok:
        raise RuntimeError(f"API Error {r.status_code} at {path}: {data}")
    return data


def api_get(base_url: str, path: str, timeout: int = 60) -> Any:
    url = base_url.rstrip("/") + path
    r = requests.get(url, timeout=timeout)
    ct = (r.headers.get("content-type") or "").lower()
    if "application/json" in ct:
        data = r.json()
    else:
        data = r.text
    if not r.ok:
        raise RuntimeError(f"API Error {r.status_code} at {path}: {data}")
    return data


def pretty(obj: Any) -> str:
    return json.dumps(obj, indent=2, ensure_ascii=False)


def now_stamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def save_draft(name: str, state: Dict[str, Any]) -> str:
    path = os.path.join(DRAFT_DIR, f"{name}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)
    return path


def list_drafts() -> List[str]:
    return sorted([f for f in os.listdir(DRAFT_DIR) if f.endswith(".json")])


def load_draft(filename: str) -> Dict[str, Any]:
    path = os.path.join(DRAFT_DIR, filename)
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def is_valid_http_url(u: str) -> bool:
    try:
        p = urlparse(u.strip())
        return p.scheme in ("http", "https") and bool(p.netloc)
    except Exception:
        return False


def guess_source_type_from_url(u: str) -> str:
    u = (u or "").strip().lower()
    return "pdf" if u.endswith(".pdf") else "web"


def default_project_settings() -> Dict[str, Any]:
    # This maps to backend ProductPack.
    return {
        "product": {
            "name": "RFID Scanner",
            "vendor": "Zebra",
            "domain": "Warehouse RFID",
            "version": None,
        },
        "requirements": [],  # Will be fetched from Jira (preferred)
        "business_rules": {
            "rules_text": (
                "Unauthorized = EPC not in allowed inventory OR duplicate EPC detected.\n"
                "If unauthorized: block transaction + raise alert.\n"
                "RFID retry count = 2.\n"
                "SLA: single-tag read <= 500ms.\n"
            )
        },
        "output": {
            "hierarchy": "Scenario->TestCase",
            "jira": {
                "project_key": "ABC",
                "scenario_issue_type": "Task",
                "testcase_issue_type": "Sub-task",
            },
        },
    }


def requirements_count(pp: Dict[str, Any]) -> int:
    return len(pp.get("requirements") or [])


def scenario_list(sp: Dict[str, Any]) -> List[Dict[str, Any]]:
    if not isinstance(sp, dict):
        return []
    return sp.get("scenarios") or []


def total_testcases(sp: Dict[str, Any]) -> int:
    total = 0
    for s in scenario_list(sp):
        total += len(s.get("test_cases") or [])
    return total


def _init_state():
    ss = st.session_state
    ss.setdefault("api_base", DEFAULT_API_BASE)

    # Human label: project name; backend uses tenant_id
    ss.setdefault("project_name", "rfid-warehouse")

    ss.setdefault("developer_mode", False)

    # Stepper
    ss.setdefault("step", "1) Connect Documentation")

    # Sources for indexing
    ss.setdefault("sources", [])
    ss.setdefault("last_index_result", {})

    # Jira requirement flow
    ss.setdefault("jira_issue_key", "")
    ss.setdefault("fetched_requirements", [])
    ss.setdefault("requirements_last_fetched_at", None)

    # Project settings (ProductPack)
    ss.setdefault("product_pack", default_project_settings())

    # Generated output
    ss.setdefault("scenario_pack", {})
    ss.setdefault("last_jira_result", {})

    # Friendly flags
    ss.setdefault("kb_ready", False)


_init_state()

# ============================================================
# Sidebar (Human-friendly + Advanced tucked away)
# ============================================================
with st.sidebar:
    st.markdown("## 🧪 QA Agent")
    st.caption("Docs + Jira → Scenarios → Test Cases → Jira/Export")

    st.divider()

    st.session_state.project_name = st.text_input(
        "Project name",
        value=st.session_state.project_name,
        help="A simple identifier for your project. (Backend uses this as tenant_id.)",
    )

    st.session_state.developer_mode = st.toggle("Developer mode", value=st.session_state.developer_mode)

    with st.expander("Advanced settings", expanded=False):
        st.session_state.api_base = st.text_input("Backend API Base URL", value=st.session_state.api_base)

    st.divider()

    # Progress checklist
    sources_ok = len(st.session_state.sources) > 0
    kb_ok = bool(st.session_state.last_index_result) or st.session_state.kb_ready
    jira_ok = bool((st.session_state.jira_issue_key or "").strip())
    req_ok = len(st.session_state.fetched_requirements) > 0
    sc_ok = len(scenario_list(st.session_state.scenario_pack)) > 0
    tc_ok = total_testcases(st.session_state.scenario_pack) > 0

    st.markdown("### ✅ Progress")
    st.write(("✅" if sources_ok else "⬜") + " Add documentation sources")
    st.write(("✅" if kb_ok else "⬜") + " Build knowledge base")
    st.write(("✅" if jira_ok else "⬜") + " Link Jira issue")
    st.write(("✅" if req_ok else "⬜") + " Import requirements")
    st.write(("✅" if sc_ok else "⬜") + " Generate scenarios")
    st.write(("✅" if tc_ok else "⬜") + " Generate test cases")

    st.divider()

    # Drafts
    st.markdown("### 💾 Drafts")
    drafts = list_drafts()
    sel = st.selectbox("Load a draft", options=["(none)"] + drafts)
    c1, c2 = st.columns(2)
    with c1:
        if st.button("Load", use_container_width=True, disabled=(sel == "(none)")):
            data = load_draft(sel)
            # Restore expected fields with fallbacks
            st.session_state.sources = data.get("sources", [])
            st.session_state.last_index_result = data.get("last_index_result", {})
            st.session_state.jira_issue_key = data.get("jira_issue_key", "")
            st.session_state.fetched_requirements = data.get("fetched_requirements", [])
            st.session_state.product_pack = data.get("product_pack", default_project_settings())
            st.session_state.scenario_pack = data.get("scenario_pack", {})
            st.session_state.last_jira_result = data.get("last_jira_result", {})
            st.session_state.kb_ready = data.get("kb_ready", False)
            st.success("Draft loaded ✅")
    with c2:
        if st.button("New", use_container_width=True):
            st.session_state.sources = []
            st.session_state.last_index_result = {}
            st.session_state.jira_issue_key = ""
            st.session_state.fetched_requirements = []
            st.session_state.product_pack = default_project_settings()
            st.session_state.scenario_pack = {}
            st.session_state.last_jira_result = {}
            st.session_state.kb_ready = False
            st.success("Fresh start ✨")

    draft_name = st.text_input("Save as", value=f"draft_{st.session_state.project_name}_{now_stamp()}")
    if st.button("Save draft", use_container_width=True):
        state = {
            "sources": st.session_state.sources,
            "last_index_result": st.session_state.last_index_result,
            "jira_issue_key": st.session_state.jira_issue_key,
            "fetched_requirements": st.session_state.fetched_requirements,
            "product_pack": st.session_state.product_pack,
            "scenario_pack": st.session_state.scenario_pack,
            "last_jira_result": st.session_state.last_jira_result,
            "kb_ready": st.session_state.kb_ready,
        }
        p = save_draft(draft_name, state)
        st.success(f"Saved ✅ ({p})")

# ============================================================
# Header
# ============================================================
st.markdown("# 🧪 QA Agent – Test Generator")
st.info("📌 Flow: Add docs → Build knowledge base → Fetch Jira requirements → Generate scenarios → Generate test cases → Export / Push to Jira")

# Stepper selection (top of main)
steps = [
    "1) Connect Documentation",
    "2) Connect Jira & Import Requirements",
    "3) Project Settings (Business Rules + Jira Output)",
    "4) Create Test Scenarios",
    "5) Create Detailed Test Cases",
    "6) Export / Push to Jira",
]
st.session_state.step = st.selectbox("Where do you want to work?", steps, index=steps.index(st.session_state.step))

# ============================================================
# STEP 1: Connect Documentation
# ============================================================
if st.session_state.step == "1) Connect Documentation":
    st.subheader("1) Connect Documentation")
    st.caption("Add your product documentation links. We’ll build a Knowledge Base from these.")

    colA, colB = st.columns([2, 1], gap="large")

    with colA:
        st.markdown("### Add a source")
        source_mode = st.radio("Source type", ["Auto-detect", "PDF URL", "Web Page URL"], horizontal=True)

        url = st.text_input("Documentation URL", placeholder="https://… (PDF link or a docs web page)")
        tags = st.text_input("Tags (optional)", placeholder="rfid, warehouse, security, performance")

        url_str = (url or "").strip()
        url_ok = bool(url_str) and is_valid_http_url(url_str)

        chosen_type: Optional[str] = None
        if url_ok:
            if source_mode == "Auto-detect":
                chosen_type = guess_source_type_from_url(url_str)
            elif source_mode == "PDF URL":
                chosen_type = "pdf"
            else:
                chosen_type = "web"

            inferred = guess_source_type_from_url(url_str)
            if chosen_type == "pdf" and inferred != "pdf":
                st.warning("This doesn’t look like a PDF URL. If it’s a web page, switch to Web Page URL.")
            if chosen_type == "web" and inferred == "pdf":
                st.info("This URL ends with .pdf — you may want to switch to PDF URL.")

        add_disabled = not url_ok
        if st.button("➕ Add source", disabled=add_disabled):
            st.session_state.sources.append(
                {
                    "type": chosen_type or "pdf",
                    "url": url_str,
                    "tags": [t.strip() for t in (tags or "").split(",") if t.strip()],
                }
            )
            st.success("Source added ✅")

        if url_str and not url_ok:
            st.error("Please enter a valid http/https URL.")

        st.markdown("### Your sources")
        if st.session_state.sources:
            st.dataframe(st.session_state.sources, use_container_width=True, hide_index=True)
        else:
            st.info("No sources added yet. Add at least one PDF or web docs page.")

        c1, c2, c3 = st.columns(3)
        with c1:
            if st.button("🧹 Clear sources", use_container_width=True, disabled=not st.session_state.sources):
                st.session_state.sources = []
                st.success("Cleared ✅")
        with c2:
            if st.button("✨ Add sample sources", use_container_width=True):
                st.session_state.sources.extend(
                    [
                        {"type": "web", "url": "https://example.com/rfid-docs/getting-started", "tags": ["sample", "web"]},
                        {"type": "pdf", "url": "https://example.com/rfid-product-guide.pdf", "tags": ["sample", "pdf"]},
                    ]
                )
                st.success("Sample sources added ✅ (replace with your real links)")
        with c3:
            st.download_button(
                "⬇️ Download sources.json",
                data=pretty({"sources": st.session_state.sources}),
                file_name="sources.json",
                mime="application/json",
                use_container_width=True,
                disabled=not st.session_state.sources,
            )

        if st.session_state.developer_mode:
            with st.expander("Developer view: sources payload", expanded=False):
                st.code(pretty({"tenant_id": st.session_state.project_name, "sources": st.session_state.sources}), language="json")

    with colB:
        st.markdown("### Build Knowledge Base")
        st.caption("This reads your docs and prepares the AI to answer with evidence.")

        build_disabled = not st.session_state.sources
        if st.button("🧠 Build knowledge base", type="primary", use_container_width=True, disabled=build_disabled):
            try:
                payload = {"tenant_id": st.session_state.project_name, "sources": st.session_state.sources}
                res = api_post(st.session_state.api_base, "/v1/index", payload, timeout=300)
                st.session_state.last_index_result = res
                st.session_state.kb_ready = True
                st.success("Knowledge base ready ✅")
            except Exception as e:
                st.error(str(e))

        if st.session_state.last_index_result:
            st.markdown("### Last result")
            st.success("Indexing completed.")
            if st.session_state.developer_mode:
                st.code(pretty(st.session_state.last_index_result), language="json")
        else:
            st.info("Build the knowledge base after adding sources.")

# ============================================================
# STEP 2: Connect Jira & Import Requirements
# ============================================================
elif st.session_state.step == "2) Connect Jira & Import Requirements":
    st.subheader("2) Connect Jira & Import Requirements")
    st.caption("Tell us the Jira story/bug and we’ll pull requirements from it.")

    colA, colB = st.columns([1.2, 1], gap="large")

    with colA:
        st.markdown("### Jira issue")
        st.session_state.jira_issue_key = st.text_input(
            "Issue key",
            value=st.session_state.jira_issue_key,
            placeholder="RFID-123",
            help="Example: PROJ-123. Requirements should be in Description/Acceptance Criteria.",
        )

        issue_key = (st.session_state.jira_issue_key or "").strip()

        c1, c2 = st.columns(2)
        with c1:
            fetch_disabled = not issue_key
            if st.button("📥 Fetch requirements", type="primary", use_container_width=True, disabled=fetch_disabled):
                # Preferred: call backend preview endpoint if implemented.
                # Fallback: still store issue_key and rely on /v1/scenarios to fetch server-side.
                try:
                    # If you added GET /v1/jira/requirements/{issue_key}, this will work.
                    reqs = api_get(st.session_state.api_base, f"/v1/jira/requirements/{issue_key}", timeout=120)
                    if isinstance(reqs, list) and reqs:
                        st.session_state.fetched_requirements = reqs
                        st.session_state.requirements_last_fetched_at = datetime.now().strftime("%Y-%m-%d %H:%M")
                        st.success(f"Fetched {len(reqs)} requirements ✅")
                    else:
                        st.warning("No requirements found in Jira issue description.")
                except Exception as e:
                    # Graceful fallback
                    st.warning(
                        "Could not preview requirements from backend. "
                        "No worries — we can still fetch during generation (server-side)."
                    )
                    if st.session_state.developer_mode:
                        st.code(str(e))
        with c2:
            if st.button("🧹 Clear imported requirements", use_container_width=True, disabled=not st.session_state.fetched_requirements):
                st.session_state.fetched_requirements = []
                st.success("Cleared ✅")

        st.markdown("### Imported requirements (preview)")
        if st.session_state.fetched_requirements:
            # Show friendly bullets
            for r in st.session_state.fetched_requirements[:50]:
                st.write(f"- **{r.get('id','REQ')}**: {r.get('statement','')}")
            if len(st.session_state.fetched_requirements) > 50:
                st.caption(f"Showing first 50 of {len(st.session_state.fetched_requirements)}")

            st.divider()
            if st.button("✅ Use these requirements", use_container_width=True):
                st.session_state.product_pack["requirements"] = st.session_state.fetched_requirements
                st.success("Requirements applied to project settings ✅")
        else:
            st.info("Fetch requirements to preview them here. Or skip preview and generate directly (server will fetch during generation).")

        if st.session_state.developer_mode and st.session_state.fetched_requirements:
            with st.expander("Developer view: raw requirements JSON", expanded=False):
                st.code(pretty(st.session_state.fetched_requirements), language="json")

    with colB:
        st.markdown("### Quick checks")
        if not st.session_state.kb_ready and not st.session_state.last_index_result:
            st.warning("Knowledge base not built yet. Go to Step 1 and build it first.")
        else:
            st.success("Knowledge base is ready ✅")

        if issue_key:
            st.success(f"Jira linked: **{issue_key}** ✅")
        else:
            st.info("Add a Jira issue key to link requirements.")

        applied = requirements_count(st.session_state.product_pack)
        st.metric("Requirements in use", applied)

        st.caption("Tip: If requirements show 0 here, you can still generate if backend fetches from Jira using jira_issue_key.")

# ============================================================
# STEP 3: Project Settings
# ============================================================
elif st.session_state.step == "3) Project Settings (Business Rules + Jira Output)":
    st.subheader("3) Project Settings")
    st.caption("Light setup: product name/domain, business rules, and Jira output configuration.")

    pp = st.session_state.product_pack

    col1, col2 = st.columns([1.2, 1], gap="large")
    with col1:
        st.markdown("### Product")
        pp["product"]["name"] = st.text_input("Product name", value=pp["product"].get("name") or "")
        pp["product"]["vendor"] = st.text_input("Vendor (optional)", value=pp["product"].get("vendor") or "")
        pp["product"]["domain"] = st.text_input("Domain", value=pp["product"].get("domain") or "")
        pp["product"]["version"] = st.text_input("Version (optional)", value=pp["product"].get("version") or "")

        st.markdown("### Business rules (optional but powerful)")
        pp["business_rules"]["rules_text"] = st.text_area(
            "Rules text",
            value=pp.get("business_rules", {}).get("rules_text", ""),
            height=220,
            placeholder="Examples: retries, unauthorized definition, performance SLAs, fallback steps…",
        )

        st.markdown("### Requirements currently in use")
        if requirements_count(pp) > 0:
            for r in (pp.get("requirements") or [])[:20]:
                st.write(f"- **{r.get('id')}**: {r.get('statement')}")
            if requirements_count(pp) > 20:
                st.caption(f"Showing first 20 of {requirements_count(pp)}")
        else:
            st.info("No requirements applied yet. Step 2 can import them from Jira, or backend can fetch during generation.")

        if st.session_state.developer_mode:
            with st.expander("Developer view: product_pack JSON", expanded=False):
                st.code(pretty(pp), language="json")

    with col2:
        st.markdown("### Jira output settings")
        jira = pp["output"]["jira"]
        jira["project_key"] = st.text_input("Jira Project Key", value=jira.get("project_key") or "")
        jira["scenario_issue_type"] = st.text_input("Scenario Issue Type", value=jira.get("scenario_issue_type") or "Task")
        jira["testcase_issue_type"] = st.text_input("Test Case Issue Type", value=jira.get("testcase_issue_type") or "Sub-task")

        st.divider()
        st.markdown("### Quality meter")
        issues = []
        if not (pp["product"].get("name") or "").strip():
            issues.append("Product name is missing.")
        if not (pp["product"].get("domain") or "").strip():
            issues.append("Domain is missing.")
        if not (jira.get("project_key") or "").strip():
            issues.append("Jira project key is missing.")

        if issues:
            st.warning("A few things to fix:")
            for i in issues:
                st.write(f"- {i}")
        else:
            st.success("Looks good ✅")

        st.divider()
        st.download_button(
            "⬇️ Download project_settings.json",
            data=pretty(pp),
            file_name="project_settings.json",
            mime="application/json",
            use_container_width=True,
        )

    st.session_state.product_pack = pp

# ============================================================
# STEP 4: Create Test Scenarios
# ============================================================
elif st.session_state.step == "4) Create Test Scenarios":
    st.subheader("4) Create Test Scenarios")
    st.caption("We’ll generate scenario-level test ideas grounded in your docs + Jira requirements.")

    if not (st.session_state.kb_ready or st.session_state.last_index_result):
        st.error("Knowledge base not ready. Go back to Step 1 and build it first.")
        st.stop()

    pp = st.session_state.product_pack
    issue_key = (st.session_state.jira_issue_key or "").strip()
    req_in_use = requirements_count(pp)

    colA, colB = st.columns([1, 1], gap="large")
    with colA:
        st.markdown("### Generation settings")
        max_scenarios = st.slider("How many scenarios?", 5, 60, 20, 5)
        st.caption("Start with 15–20. Increase once the quality looks good.")

        st.markdown("### Requirements source")
        if req_in_use > 0:
            st.success(f"Using {req_in_use} requirements already loaded ✅")
        else:
            if issue_key:
                st.info(f"No requirements loaded in UI. Backend will fetch from Jira issue **{issue_key}** during generation.")
            else:
                st.warning("No requirements loaded AND no Jira issue key. Add a Jira issue key in Step 2.")

        can_generate = bool(issue_key) or (req_in_use > 0)

        if st.button("✨ Create scenarios", type="primary", use_container_width=True, disabled=not can_generate):
            try:
                payload = {
                    "tenant_id": st.session_state.project_name,
                    "product_pack": pp,
                    "max_scenarios": max_scenarios,
                    # NEW Jira-based requirement flow (backend should support these flags)
                    "jira_issue_key": issue_key or None,
                    "fetch_requirements_from_jira": True,
                }
                res = api_post(st.session_state.api_base, "/v1/scenarios", payload, timeout=300)
                st.session_state.scenario_pack = res
                st.success("Scenarios generated ✅")
            except Exception as e:
                st.error(str(e))
                if st.session_state.developer_mode:
                    st.code(pretty({"payload": payload}), language="json")

    with colB:
        sp = st.session_state.scenario_pack
        scs = scenario_list(sp)

        st.markdown("### Results")
        if not scs:
            st.info("No scenarios yet. Generate on the left.")
        else:
            st.metric("Scenarios", len(scs))
            st.metric("Total test cases", total_testcases(sp))
            st.caption("Test cases will be 0 until Step 5.")

            # Friendly browse
            st.divider()
            search = st.text_input("Search scenarios", placeholder="type keyword…")
            show_n = st.slider("Show top", 5, min(30, len(scs)), min(12, len(scs)), 1)

            shown = []
            for s in scs:
                title = (s.get("title") or "")
                obj = (s.get("objective") or "")
                blob = (title + " " + obj).lower()
                if search and search.lower() not in blob:
                    continue
                shown.append(s)

            if not shown:
                st.warning("No matches.")
            else:
                for s in shown[:show_n]:
                    sid = s.get("scenario_id", "(no-id)")
                    title = s.get("title", "(no-title)")
                    tc_cnt = len(s.get("test_cases") or [])
                    ref_cnt = len(s.get("references") or [])
                    with st.expander(f"🧩 {sid} — {title}  •  refs:{ref_cnt}  •  tcs:{tc_cnt}", expanded=False):
                        st.write(s.get("objective") or "—")
                        cols = st.columns(2)
                        with cols[0]:
                            st.caption("Scope")
                            scope = s.get("scope") or []
                            st.write("\n".join([f"- {x}" for x in scope]) if scope else "—")
                        with cols[1]:
                            st.caption("Assumptions")
                            a = s.get("assumptions") or []
                            st.write("\n".join([f"- {x}" for x in a]) if a else "—")

                        if st.session_state.developer_mode:
                            st.divider()
                            st.caption("Raw JSON")
                            st.code(pretty(s), language="json")

# ============================================================
# STEP 5: Create Detailed Test Cases
# ============================================================
elif st.session_state.step == "5) Create Detailed Test Cases":
    st.subheader("5) Create Detailed Test Cases")
    st.caption("Now we expand each scenario into detailed test cases with steps, expected results, and references.")

    if not scenario_list(st.session_state.scenario_pack):
        st.warning("No scenarios found. Generate scenarios in Step 4 first.")
        st.stop()

    pp = st.session_state.product_pack
    issue_key = (st.session_state.jira_issue_key or "").strip()

    colA, colB = st.columns([1, 1], gap="large")
    with colA:
        st.markdown("### Generate")
        st.caption("This can take time depending on model + number of scenarios. (Yes, the AI is thinking… not buffering 😄)")

        if st.button("🧾 Create test cases", type="primary", use_container_width=True):
            try:
                payload = {
                    "tenant_id": st.session_state.project_name,
                    "product_pack": pp,
                    "scenario_pack": st.session_state.scenario_pack,
                    "jira_issue_key": issue_key or None,
                    "fetch_requirements_from_jira": True,
                }
                res = api_post(st.session_state.api_base, "/v1/testcases", payload, timeout=900)
                st.session_state.scenario_pack = res
                st.success("Test cases generated ✅")
            except Exception as e:
                st.error(str(e))
                if st.session_state.developer_mode:
                    st.code(pretty({"payload": payload}), language="json")

        sp = st.session_state.scenario_pack
        st.divider()
        st.metric("Scenarios", len(scenario_list(sp)))
        st.metric("Total test cases", total_testcases(sp))

    with colB:
        st.markdown("### Browse test cases")
        sp = st.session_state.scenario_pack
        scs = scenario_list(sp)
        tc_total = total_testcases(sp)

        if tc_total == 0:
            st.info("No test cases yet. Generate them on the left.")
        else:
            # Build a light list for browsing
            scenario_titles = [f"{s.get('scenario_id','(no-id)')} — {s.get('title','(no-title)')}" for s in scs]
            idx = st.selectbox("Pick a scenario", range(len(scs)), format_func=lambda i: scenario_titles[i])
            s = scs[idx]
            tcs = s.get("test_cases") or []

            tc_type = st.selectbox(
                "Filter by type",
                ["All", "Functional", "Negative", "Edge", "Security", "Performance", "Recovery"],
                index=0,
            )
            q = st.text_input("Search in test case title", placeholder="keyword…")

            filtered = []
            for tc in tcs:
                if tc_type != "All" and tc.get("type") != tc_type:
                    continue
                if q and q.lower() not in (tc.get("title") or "").lower():
                    continue
                filtered.append(tc)

            st.caption(f"Showing {len(filtered)} of {len(tcs)} test cases")

            for tc in filtered[:30]:
                tc_id = tc.get("tc_id", "(no-id)")
                title = tc.get("title", "(no-title)")
                pr = tc.get("priority", "—")
                with st.expander(f"✅ {tc_id} — {title}  •  {tc.get('type','—')}  •  {pr}", expanded=False):
                    st.markdown("**Preconditions**")
                    pre = tc.get("preconditions") or []
                    st.write("\n".join([f"- {x}" for x in pre]) if pre else "—")

                    st.markdown("**Steps**")
                    steps = tc.get("steps") or []
                    if steps:
                        for i, step in enumerate(steps, start=1):
                            st.write(f"{i}. {step}")
                    else:
                        st.write("—")

                    st.markdown("**Expected results**")
                    exp = tc.get("expected_results") or []
                    if exp:
                        for i, er in enumerate(exp, start=1):
                            st.write(f"{i}. {er}")
                    else:
                        st.write("—")

                    refs = tc.get("references") or []
                    st.caption(f"References: {len(refs)}")
                    if refs and not st.session_state.developer_mode:
                        # show compact references
                        for r in refs[:5]:
                            st.write(f"- {r.get('doc','')} • {r.get('section','')}")
                        if len(refs) > 5:
                            st.caption(f"+{len(refs)-5} more")

                    if st.session_state.developer_mode:
                        st.divider()
                        st.code(pretty(tc), language="json")

# ============================================================
# STEP 6: Export / Push to Jira
# ============================================================
elif st.session_state.step == "6) Export / Push to Jira":
    st.subheader("6) Export / Push to Jira")
    st.caption("Download results or push issues to Jira.")

    sp = st.session_state.scenario_pack
    scs = scenario_list(sp)
    tc_total = total_testcases(sp)

    if not scs:
        st.warning("Nothing to export yet. Generate scenarios (and test cases) first.")
        st.stop()

    colA, colB = st.columns([1, 1], gap="large")

    with colA:
        st.markdown("### Download")
        st.download_button(
            "⬇️ scenario_pack.json",
            data=pretty(sp),
            file_name="scenario_pack.json",
            mime="application/json",
            use_container_width=True,
        )

        # Flat CSV
        if st.button("📄 Create flat CSV"):
            lines = ["scenario_id,scenario_title,tc_id,tc_title,tc_type,priority"]
            for s in scs:
                sid = (s.get("scenario_id") or "").replace(",", " ")
                stitle = (s.get("title") or "").replace(",", " ")
                for tc in (s.get("test_cases") or []):
                    lines.append(",".join([
                        sid,
                        stitle,
                        (tc.get("tc_id") or "").replace(",", " "),
                        (tc.get("title") or "").replace(",", " "),
                        (tc.get("type") or "").replace(",", " "),
                        (tc.get("priority") or "").replace(",", " "),
                    ]))
            csv_data = "\n".join(lines)
            st.download_button(
                "⬇️ flat_testcases.csv",
                data=csv_data,
                file_name="flat_testcases.csv",
                mime="text/csv",
                use_container_width=True,
            )

        st.divider()
        st.markdown("### Quick summary")
        st.write(f"- Scenarios: **{len(scs)}**")
        st.write(f"- Test cases: **{tc_total}**")

        if st.session_state.developer_mode:
            with st.expander("Developer view: raw scenario_pack", expanded=False):
                st.code(pretty(sp), language="json")

    with colB:
        st.markdown("### Push to Jira")
        if tc_total == 0:
            st.info("You can push scenarios alone, but typically you’ll want test cases first (Step 5).")

        label_prefix = st.multiselect(
            "Labels (prefix)",
            ["QA", "AutoGen", "RAG", "TestGen", "RFID", "Warehouse", "Security", "Performance"],
            default=["QA", "AutoGen"],
        )

        push_disabled = not scs
        if st.button("🚀 Send to Jira", type="primary", use_container_width=True, disabled=push_disabled):
            try:
                payload = {
                    "product_pack": st.session_state.product_pack,
                    "scenario_pack": sp,
                    "label_prefix": label_prefix,
                }
                res = api_post(st.session_state.api_base, "/v1/jira/push", payload, timeout=600)
                st.session_state.last_jira_result = res
                st.success("Pushed to Jira ✅")
            except Exception as e:
                st.error(str(e))
                if st.session_state.developer_mode:
                    st.code(pretty({"payload": payload}), language="json")

        if st.session_state.last_jira_result:
            st.markdown("### Jira result")
            if not st.session_state.developer_mode:
                scenario_keys = st.session_state.last_jira_result.get("scenario_keys") or []
                testcase_keys = st.session_state.last_jira_result.get("testcase_keys") or []
                st.write(f"Created scenarios: **{len(scenario_keys)}**")
                st.write(f"Created test cases: **{len(testcase_keys)}**")
                st.download_button(
                    "⬇️ jira_result.json",
                    data=pretty(st.session_state.last_jira_result),
                    file_name="jira_result.json",
                    mime="application/json",
                    use_container_width=True,
                )
            else:
                st.code(pretty(st.session_state.last_jira_result), language="json")

# ============================================================
# Footer vibes
# ============================================================
st.divider()
st.caption("Tip: If something fails, turn on Developer mode. It’ll show payloads + raw responses so you can debug like a boss.")
