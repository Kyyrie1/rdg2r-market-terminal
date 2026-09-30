import base64
import hashlib
import secrets
import urllib.parse
from datetime import datetime, timezone
from typing import Optional

import pandas as pd
import requests
import streamlit as st

st.set_page_config(
    page_title="RD-G2R Market Terminal",
    page_icon="💰",
    layout="wide",
)

ESI = "https://esi.evetech.net/latest"
SSO = "https://login.eveonline.com/v2/oauth"
FUZZ = "https://market.fuzzwork.co.uk"

# EVE IDs
PURE_BLIND = 10000023
THE_FORGE = 10000002
JITA_SYSTEM = 30000142
RD_G2R_SYSTEM = 30001196  # RD-G2R

UA = "RD-G2R-Market-Terminal/2.0 (EVE third-party market dashboard)"

st.title("💰 RD-G2R Market Terminal")
st.caption("RD-G2R pricing • Jita comparison • hauling margins • structure-market support")

# -----------------------------
# HTTP / API helpers
# -----------------------------

def request_json(url, params=None, headers=None, timeout=30):
    h = {"User-Agent": UA}
    if headers:
        h.update(headers)
    r = requests.get(url, params=params, headers=h, timeout=timeout)
    r.raise_for_status()
    return r.json(), r.headers


@st.cache_data(ttl=3600, show_spinner=False)
def fuzz_aggregates(location_kind: str, types_csv: str):
    """Fuzzwork aggregate data. location_kind can be rdg2r, jita, pureblind."""
    locations = {
        "rdg2r": f"system={RD_G2R_SYSTEM}",
        "jita": f"system={JITA_SYSTEM}",
        "pureblind": f"region={PURE_BLIND}",
        "forge": f"region={THE_FORGE}",
    }
    query = locations[location_kind]
    url = f"{FUZZ}/aggregates/"
    params = {"types": types_csv}
    key, value = query.split("=")
    params[key] = value
    data, _ = request_json(url, params=params)
    return data


@st.cache_data(ttl=300, show_spinner=False)
def esi_region_orders(region_id: int):
    rows = []
    page = 1
    url = f"{ESI}/markets/{region_id}/orders/"
    while True:
        data, headers = request_json(
            url,
            {"order_type": "all", "page": page},
        )
        rows.extend(data)
        pages = int(headers.get("X-Pages", "1"))
        if page >= pages:
            break
        page += 1
        if page > 200:
            break
    return pd.DataFrame(rows)


@st.cache_data(ttl=300, show_spinner=False)
def esi_structure_orders(structure_id: int, access_token: str):
    rows = []
    page = 1
    url = f"{ESI}/markets/structures/{structure_id}/"
    while True:
        data, headers = request_json(
            url,
            {"page": page},
            headers={"Authorization": f"Bearer {access_token}"},
        )
        rows.extend(data)
        pages = int(headers.get("X-Pages", "1"))
        if page >= pages:
            break
        page += 1
        if page > 200:
            break
    return pd.DataFrame(rows)


@st.cache_data(ttl=86400, show_spinner=False)
def esi_search_inventory_type(name: str):
    data, _ = request_json(
        f"{ESI}/search/",
        {"categories": "inventory_type", "search": name, "strict": "false"},
    )
    return data.get("inventory_type", [])


@st.cache_data(ttl=86400, show_spinner=False)
def esi_universe_names(ids):
    ids = list(dict.fromkeys(int(x) for x in ids))
    if not ids:
        return {}
    data, _ = request_json(f"{ESI}/universe/names/", {"ids": ",".join(map(str, ids))})
    return {int(x["id"]): x["name"] for x in data}


@st.cache_data(ttl=86400, show_spinner=False)
def esi_market_types(region_id: int):
    rows = []
    page = 1
    url = f"{ESI}/markets/{region_id}/types/"
    while True:
        data, headers = request_json(url, {"page": page})
        rows.extend(data)
        pages = int(headers.get("X-Pages", "1"))
        if page >= pages:
            break
        page += 1
    return rows


@st.cache_data(ttl=86400, show_spinner=False)
def esi_history(region_id: int, type_id: int):
    data, _ = request_json(
        f"{ESI}/markets/{region_id}/history/",
        {"type_id": type_id},
    )
    return pd.DataFrame(data)


def fmt_isk(v):
    if v is None or pd.isna(v):
        return "-"
    v = float(v)
    if abs(v) >= 1e12:
        return f"{v/1e12:,.2f}T"
    if abs(v) >= 1e9:
        return f"{v/1e9:,.2f}B"
    if abs(v) >= 1e6:
        return f"{v/1e6:,.2f}M"
    if abs(v) >= 1e3:
        return f"{v/1e3:,.2f}K"
    return f"{v:,.2f}"


def pct(v):
    return "-" if v is None or pd.isna(v) else f"{float(v):,.2f}%"


def aggregate_frame(raw):
    rows = []
    for tid, value in raw.items():
        buy = value.get("buy", {})
        sell = value.get("sell", {})
        rows.append({
            "type_id": int(tid),
            "rd_buy": float(buy.get("max", 0) or 0),
            "rd_buy_volume": float(buy.get("volume", 0) or 0),
            "rd_buy_orders": int(float(buy.get("orderCount", 0) or 0)),
            "rd_sell": float(sell.get("min", 0) or 0),
            "rd_sell_volume": float(sell.get("volume", 0) or 0),
            "rd_sell_orders": int(float(sell.get("orderCount", 0) or 0)),
        })
    return pd.DataFrame(rows)


# -----------------------------
# OAuth helpers
# -----------------------------

def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def oauth_state():
    return secrets.token_urlsafe(32)


def pkce_pair():
    verifier = b64url(secrets.token_bytes(32))
    challenge = b64url(hashlib.sha256(verifier.encode()).digest())
    return verifier, challenge


def get_redirect_uri():
    # Streamlit Cloud / local URL visible to the app.
    return st.session_state.get(
        "redirect_uri",
        st.text_input(
            "OAuth redirect URI",
            value="http://localhost:8501",
            help="Register this exact URL in the EVE Developer Portal.",
        ),
    )


def oauth_login(client_id, redirect_uri, scopes):
    verifier, challenge = pkce_pair()
    state = oauth_state()
    st.session_state["oauth_verifier"] = verifier
    st.session_state["oauth_state"] = state
    st.session_state["oauth_redirect"] = redirect_uri

    params = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "scope": " ".join(scopes),
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }
    return f"{SSO}/authorize?{urllib.parse.urlencode(params)}"


def oauth_exchange(code, client_id, redirect_uri):
    verifier = st.session_state.get("oauth_verifier")
    if not verifier:
        raise RuntimeError("OAuth verifier is missing. Start login again.")

    r = requests.post(
        f"{SSO}/token",
        data={
            "grant_type": "authorization_code",
            "code": code,
            "client_id": client_id,
            "code_verifier": verifier,
            "redirect_uri": redirect_uri,
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=30,
    )
    r.raise_for_status()
    return r.json()


# -----------------------------
# Sidebar
# -----------------------------

with st.sidebar:
    st.header("⚙️ Configuration")

    if st.button("Clear cached market data"):
        st.cache_data.clear()
        st.rerun()

    st.subheader("Margin scanner")
    rd_min = st.number_input("Minimum RD-G2R sell volume", min_value=0, value=1, step=1)
    min_margin = st.number_input("Minimum gross margin %", min_value=-100.0, value=10.0, step=1.0)
    min_profit = st.number_input("Minimum ISK profit / unit", min_value=0.0, value=100000.0, step=100000.0)
    max_rows = st.slider("Scanner results", 10, 200, 50)

    st.subheader("Hauling")
    haul_fee = st.number_input("Hauling cost / unit", min_value=0.0, value=0.0, step=1000.0)
    sales_tax = st.number_input("Sales tax %", min_value=0.0, value=3.5, step=0.1)
    broker_fee = st.number_input("Broker fee %", min_value=0.0, value=3.0, step=0.1)

    st.subheader("Authenticated structure")
    structure_id = st.text_input(
        "Structure ID (optional)",
        help="If you have a specific Upwell structure in RD-G2R, enter its structure ID here.",
    )

    st.divider()
    st.caption("Bulk comparisons use Fuzzwork aggregates; exact private structure markets use authenticated ESI.")

# -----------------------------
# Handle OAuth callback
# -----------------------------

query = st.query_params
if "code" in query:
    code = query["code"]
    state = query.get("state", "")
    if state and state == st.session_state.get("oauth_state"):
        try:
            client_id = st.secrets["EVE_CLIENT_ID"]
            redirect_uri = st.session_state.get("oauth_redirect", "http://localhost:8501")
            token = oauth_exchange(code, client_id, redirect_uri)
            st.session_state["access_token"] = token["access_token"]
            st.query_params.clear()
            st.success("EVE account authenticated.")
            st.rerun()
        except Exception as e:
            st.error(f"OAuth failed: {e}")
    else:
        st.error("OAuth state verification failed. Start login again.")

# -----------------------------
# Tabs
# -----------------------------

tabs = st.tabs([
    "📊 Dashboard",
    "🔎 Item",
    "🚚 Import Scanner",
    "📚 Structure Market",
    "📈 History",
])

# -----------------------------
# Dashboard
# -----------------------------

with tabs[0]:
    st.subheader("RD-G2R ↔ Jita overview")

    st.write(
        "This view compares the RD-G2R system market against Jita using precomputed "
        "market aggregates. Fuzzwork documents that its aggregate API accepts a system "
        "or region and returns buy/sell statistics for requested type IDs."
    )

    st.metric("RD-G2R", "30001196")
    st.metric("Jita", "30000142")

    st.info(
        "Use the Import Scanner to find profitable items. It intentionally uses "
        "bulk aggregates rather than pulling the entire Jita order book."
    )

# -----------------------------
# Item search
# -----------------------------

with tabs[1]:
    st.subheader("Item comparison")

    q = st.text_input("Search an item", placeholder="Ishtar, PLEX, Tritanium...")
    if q:
        ids = esi_search_inventory_type(q)
        if not ids:
            st.warning("No matching item found.")
        else:
            # Resolve names so search results are usable.
            names = esi_universe_names(ids[:20])
            choices = [(tid, names.get(tid, str(tid))) for tid in ids[:20]]
            chosen = st.selectbox(
                "Select item",
                choices,
                format_func=lambda x: f"{x[1]} ({x[0]})",
            )
            type_id = chosen[0]

            with st.spinner("Loading market data..."):
                rd = fuzz_aggregates("rdg2r", str(type_id))
                ji = fuzz_aggregates("jita", str(type_id))

            rdf = aggregate_frame(rd)
            jif = aggregate_frame(ji)

            if rdf.empty:
                st.warning("No RD-G2R aggregate data found.")
            else:
                r = rdf.iloc[0]
                j = jif.iloc[0] if not jif.empty else None

                rd_sell = r["rd_sell"]
                jita_sell = j["rd_sell"] if j is not None else 0
                gross = rd_sell - jita_sell
                gross_pct = gross / jita_sell * 100 if jita_sell else 0
                net = gross - haul_fee - (rd_sell * (sales_tax + broker_fee) / 100)

                a, b, c, d = st.columns(4)
                a.metric("RD-G2R sell", fmt_isk(rd_sell))
                b.metric("Jita sell", fmt_isk(jita_sell))
                c.metric("Gross difference", fmt_isk(gross), pct(gross_pct))
                d.metric("Estimated net / unit", fmt_isk(net))

                st.dataframe(
                    pd.DataFrame([{
                        "Item": names.get(type_id, str(type_id)),
                        "Type ID": type_id,
                        "RD-G2R sell": rd_sell,
                        "RD-G2R sell volume": r["rd_sell_volume"],
                        "RD-G2R sell orders": r["rd_sell_orders"],
                        "Jita sell": jita_sell,
                        "Jita sell volume": j["rd_sell_volume"] if j is not None else 0,
                        "Gross margin %": gross_pct,
                        "Net after configured costs": net,
                    }]).style.format({
                        "RD-G2R sell": fmt_isk,
                        "Jita sell": fmt_isk,
                        "Gross margin %": "{:.2f}%",
                        "Net after configured costs": fmt_isk,
                    }),
                    use_container_width=True,
                    hide_index=True,
                )

# -----------------------------
# Import scanner
# -----------------------------

with tabs[2]:
    st.subheader("🚚 Jita → RD-G2R Import Scanner")

    st.write(
        "The scanner starts with items currently present in Pure Blind, then compares "
        "their RD-G2R and Jita aggregates. This keeps the number of external requests "
        "small while still covering items relevant to the region."
    )

    if st.button("Run import scan", type="primary"):
        with st.spinner("Getting the current Pure Blind type list..."):
            type_ids = esi_market_types(PURE_BLIND)

        # Keep the scanner reasonably bounded. The user can rerun it after narrowing.
        # We fetch in chunks from Fuzzwork.
        type_ids = list(dict.fromkeys(int(x) for x in type_ids))
        st.write(f"Candidate market types in Pure Blind: **{len(type_ids):,}**")

        progress = st.progress(0)
        result_parts = []

        chunk_size = 100
        for i in range(0, len(type_ids), chunk_size):
            chunk = type_ids[i:i + chunk_size]
            csv = ",".join(map(str, chunk))

            rd_raw = fuzz_aggregates("rdg2r", csv)
            ji_raw = fuzz_aggregates("jita", csv)

            rd = aggregate_frame(rd_raw).set_index("type_id")
            ji = aggregate_frame(ji_raw).set_index("type_id")

            common = rd.index.intersection(ji.index)
            if len(common):
                x = pd.DataFrame(index=common)
                x["rd_sell"] = rd.loc[common, "rd_sell"]
                x["rd_volume"] = rd.loc[common, "rd_sell_volume"]
                x["rd_orders"] = rd.loc[common, "rd_sell_orders"]
                x["jita_sell"] = ji.loc[common, "rd_sell"]
                x["jita_volume"] = ji.loc[common, "rd_sell_volume"]
                x["gross_profit"] = x["rd_sell"] - x["jita_sell"]
                x["gross_margin_pct"] = x["gross_profit"] / x["jita_sell"] * 100
                x["net_profit"] = (
                    x["gross_profit"]
                    - haul_fee
                    - (x["rd_sell"] * (sales_tax + broker_fee) / 100)
                )
                x["roi_pct"] = x["net_profit"] / x["jita_sell"] * 100
                result_parts.append(x.reset_index())

            progress.progress(min((i + chunk_size) / len(type_ids), 1.0))

        if result_parts:
            scan = pd.concat(result_parts, ignore_index=True)
            scan = scan[
                (scan["rd_volume"] >= rd_min)
                & (scan["gross_margin_pct"] >= min_margin)
                & (scan["net_profit"] >= min_profit)
            ].sort_values(
                ["net_profit", "gross_margin_pct"],
                ascending=False,
            ).head(max_rows)

            if scan.empty:
                st.warning("No items matched the current scanner settings.")
            else:
                names = esi_universe_names(scan["type_id"].tolist())
                scan.insert(
                    1,
                    "Item",
                    scan["type_id"].map(lambda x: names.get(int(x), str(x))),
                )

                display = scan.rename(columns={
                    "type_id": "Type ID",
                    "rd_sell": "RD-G2R sell",
                    "rd_volume": "RD-G2R volume",
                    "rd_orders": "RD-G2R orders",
                    "jita_sell": "Jita sell",
                    "jita_volume": "Jita volume",
                    "gross_profit": "Gross ISK/unit",
                    "gross_margin_pct": "Gross margin %",
                    "net_profit": "Net ISK/unit",
                    "roi_pct": "Net ROI %",
                })

                st.dataframe(
                    display.style.format({
                        "RD-G2R sell": fmt_isk,
                        "Jita sell": fmt_isk,
                        "Gross ISK/unit": fmt_isk,
                        "Net ISK/unit": fmt_isk,
                        "Gross margin %": "{:.2f}%",
                        "Net ROI %": "{:.2f}%",
                        "RD-G2R volume": "{:,.0f}",
                        "Jita volume": "{:,.0f}",
                    }),
                    use_container_width=True,
                    hide_index=True,
                )

                csv_data = display.to_csv(index=False).encode()
                st.download_button(
                    "Download opportunities CSV",
                    csv_data,
                    "rdg2r_import_opportunities.csv",
                    "text/csv",
                )
        else:
            st.warning("No market data returned.")

# -----------------------------
# Structure market
# -----------------------------

with tabs[3]:
    st.subheader("📚 Exact Upwell structure market")

    st.write(
        "For an exact player structure, ESI requires an authenticated character with "
        "docking access and the `esi-markets.structure_markets.v1` scope."
    )

    client_id = st.secrets.get("EVE_CLIENT_ID", "")
    access_token = st.session_state.get("access_token")

    if not client_id:
        st.warning(
            "Add `EVE_CLIENT_ID` and `EVE_CLIENT_SECRET` to Streamlit secrets "
            "after creating an EVE Developer application."
        )
    else:
        redirect_uri = st.text_input(
            "Registered OAuth callback",
            value="http://localhost:8501",
        )

        scopes = [
            "esi-markets.structure_markets.v1",
            "esi-universe.read_structures.v1",
        ]

        if not access_token:
            login_url = oauth_login(client_id, redirect_uri, scopes)
            st.link_button("Log in with EVE Online", login_url)
        else:
            st.success("Authenticated with EVE SSO.")

            if structure_id:
                try:
                    sid = int(structure_id)
                    with st.spinner("Loading structure market..."):
                        sdf = esi_structure_orders(sid, access_token)

                    if sdf.empty:
                        st.info("No active market orders returned.")
                    else:
                        names = esi_universe_names(sdf["type_id"].unique().tolist())

                        sdf["item"] = sdf["type_id"].map(
                            lambda x: names.get(int(x), str(x))
                        )

                        sells = sdf[~sdf["is_buy_order"]].sort_values("price")
                        buys = sdf[sdf["is_buy_order"]].sort_values(
                            "price", ascending=False
                        )

                        left, right = st.columns(2)

                        with left:
                            st.markdown("### Sell orders")
                            st.dataframe(
                                sells[
                                    ["item", "type_id", "price", "volume_remain",
                                     "min_volume", "range", "issued"]
                                ].head(200),
                                use_container_width=True,
                                hide_index=True,
                            )

                        with right:
                            st.markdown("### Buy orders")
                            st.dataframe(
                                buys[
                                    ["item", "type_id", "price", "volume_remain",
                                     "min_volume", "range", "issued"]
                                ].head(200),
                                use_container_width=True,
                                hide_index=True,
                            )

                except Exception as e:
                    st.error(f"Structure market request failed: {e}")
            else:
                st.info("Enter an Upwell structure ID in the sidebar to load its exact market.")

# -----------------------------
# History
# -----------------------------

with tabs[4]:
    st.subheader("📈 Pure Blind price history")

    history_query = st.text_input(
        "History item",
        placeholder="e.g. Ishtar",
        key="history_query",
    )

    if history_query:
        ids = esi_search_inventory_type(history_query)
        if ids:
            names = esi_universe_names(ids[:20])
            chosen = st.selectbox(
                "Item",
                [(x, names.get(x, str(x))) for x in ids[:20]],
                format_func=lambda x: f"{x[1]} ({x[0]})",
                key="history_item",
            )
            tid = chosen[0]

            hist = esi_history(PURE_BLIND, int(tid))
            if hist.empty:
                st.info("No history available.")
            else:
                hist["date"] = pd.to_datetime(hist["date"])
                st.line_chart(
                    hist.set_index("date")[["average", "highest", "lowest"]]
                )
                st.bar_chart(hist.set_index("date")[["volume"]])

st.divider()
st.caption(
    "ESI = official EVE third-party API. Fuzzwork aggregates are community market "
    "data refreshed from market snapshots. Exact private structure orders come from ESI."
)
