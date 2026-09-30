# RD-G2R Market Terminal

A Streamlit EVE Online market dashboard focused on **RD-G2R**.

## Features

### RD-G2R ↔ Jita comparison
- RD-G2R system market aggregates
- Jita system market aggregates
- Gross ISK/unit difference
- Gross margin %
- Configurable hauling cost
- Configurable broker fee
- Configurable sales tax
- Estimated net ISK/unit
- Net ROI %

### Import scanner

The scanner:

1. Gets the current list of item type IDs appearing in Pure Blind.
2. Batches those type IDs into Fuzzwork aggregate requests.
3. Gets RD-G2R and Jita pricing.
4. Calculates gross and net margins.
5. Filters by:
   - RD-G2R volume
   - minimum gross margin
   - minimum net ISK/unit
6. Sorts the results by net profit.
7. Lets you download the results as CSV.

This is deliberately not implemented by downloading the entire Jita order book. Fuzzwork provides precomputed aggregates for regions and systems, making bulk comparison much cheaper and faster.

## Exact structure market

The app also supports an exact Upwell structure market through ESI.

The authenticated ESI structure-market route requires:

- EVE SSO login
- `esi-markets.structure_markets.v1`
- a character with docking access to the structure

Enter the structure ID in the sidebar.

## EVE Developer Portal

Create an EVE developer application and register your callback URL.

For local development:

`http://localhost:8501`

For Streamlit Cloud:

`https://YOUR-APP-NAME.streamlit.app`

Do not publish your client secret.

## Streamlit secrets

Create `.streamlit/secrets.toml` locally:

```toml
EVE_CLIENT_ID = "your-client-id"
EVE_CLIENT_SECRET = "your-client-secret"
```

On Streamlit Cloud, put the same values into the app's Secrets section.

## Local installation

```bash
python -m venv .venv
```

Windows:

```bash
.venv\Scripts\activate
```

Linux/macOS:

```bash
source .venv/bin/activate
```

Then:

```bash
pip install -r requirements.txt
streamlit run app.py
```

## Notes on market freshness

ESI market orders are cached by CCP for five minutes and the market-order route is rate limited. This application caches ESI market data and does not continuously poll it.

Fuzzwork's aggregate service is separate community infrastructure and is refreshed from market snapshots.

## RD-G2R configuration

This project uses:

- RD-G2R system ID: `30001196`
- Jita system ID: `30000142`
- Pure Blind region ID: `10000023`
- The Forge region ID: `10000002`

If CCP ever changes or replaces IDs/endpoints, update the constants in `app.py`.

## Possible future upgrades

- Corporation/character hauling profiles
- Cargo-volume constraints
- Freighter / DST / blockade runner profiles
- ISK per m3
- Maximum investment budget
- Minimum daily volume
- Market depth simulation
- Buy-order arbitrage
- Structure vs Jita vs regional median
- Persistent price history database
- Discord alerts
- Automatic scheduled scans
