"""
Cost of Equity & WACC Comparator — USD vs. a selected foreign currency
======================================================================

Streamlit app (Python 3.12) that fetches market data from public sources,
lets the user override any input, and compares the cost of equity, cost of
debt and WACC of a project in USD and in the currency of a selected country.

Run with:  streamlit run app.py
"""

from __future__ import annotations

import io
import re
from datetime import datetime, timezone

import altair as alt
import pandas as pd
import pycountry
import requests
import streamlit as st
from bs4 import BeautifulSoup

# --------------------------------------------------------------------------
# Sources
# --------------------------------------------------------------------------
BETAS_URL = "https://pages.stern.nyu.edu/~adamodar/pc/datasets/betas.xls"
OECD_PAGE_URL = (
    "https://www.oecd.org/en/publications/corporate-tax-statistics-2024_9c27d6e8-en/"
    "full-report/statutory-corporate-income-tax-rates_440f9286.html"
    "#chapter-d1e2342-08e74a33fb"
)
# StatLink (Excel data) behind "Figure 2.1. Statutory corporate income tax rates, 2024"
OECD_STATLINK_FIG21 = "https://stat.link/0qkxo1"
RATINGS_URL = "https://pages.stern.nyu.edu/adamodar/New_Home_Page/datafile/ratings.html"
BONDS_URL = "https://tradingeconomics.com/bonds"

DEFAULT_INDUSTRY = "Software (System & Application)"
DEFAULT_COUNTRY = "United States"
DEFAULT_ERP = 6.5      # %
DEFAULT_DEBT = 30.0    # %
US_FALLBACK_TAX = 21.0  # % US federal statutory rate; used only if the OECD fetch fails

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}
TIMEOUT = 30


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def _get(url: str) -> requests.Response:
    """GET with browser-like headers; falls back to cloudscraper if blocked."""
    resp = requests.get(url, headers=HEADERS, timeout=TIMEOUT, allow_redirects=True)
    if resp.status_code in (403, 429, 503):
        try:
            import cloudscraper  # optional dependency

            scraper = cloudscraper.create_scraper()
            resp = scraper.get(url, timeout=TIMEOUT, allow_redirects=True)
        except ImportError:
            pass
    resp.raise_for_status()
    return resp


# --------------------------------------------------------------------------
# Country-name normalisation (to match OECD and Trading Economics names)
# --------------------------------------------------------------------------
_ALIASES = {
    "south korea": "KOR", "korea": "KOR", "korea, republic of": "KOR",
    "russia": "RUS", "vietnam": "VNM", "viet nam": "VNM", "turkey": "TUR",
    "türkiye": "TUR", "turkiye": "TUR", "czech republic": "CZE", "czechia": "CZE",
    "hong kong": "HKG", "hong kong (china)": "HKG", "hong kong, china": "HKG",
    "macau": "MAC", "macao": "MAC", "macao (china)": "MAC",
    "taiwan": "TWN", "chinese taipei": "TWN", "ivory coast": "CIV",
    "côte d'ivoire": "CIV", "cote d'ivoire": "CIV", "bolivia": "BOL",
    "iran": "IRN", "tanzania": "TZA", "venezuela": "VEN", "moldova": "MDA",
    "slovak republic": "SVK", "slovakia": "SVK", "united states": "USA",
    "united kingdom": "GBR", "china": "CHN", "china (people's republic of)": "CHN",
    "people's republic of china": "CHN", "dr congo": "COD",
    "democratic republic of the congo": "COD", "congo": "COG",
    "cape verde": "CPV", "cabo verde": "CPV", "swaziland": "SWZ", "eswatini": "SWZ",
    "north macedonia": "MKD", "palestine": "PSE", "laos": "LAO", "syria": "SYR",
    "curaçao": "CUW", "curacao": "CUW", "sint maarten": "SXM",
    "bosnia and herzegovina": "BIH", "kosovo": "XKX",
}


@st.cache_data(show_spinner=False)
def to_iso3(name: str) -> str | None:
    if not isinstance(name, str):
        return None
    clean = re.sub(r"[\d*†‡¹²³⁴⁵]+$", "", name.strip()).strip()
    key = clean.lower()
    # Skip aggregates, notes and headers
    if (not clean or len(clean) > 45 or re.search(r"\d", clean)
            or re.search(r"\b(average|total|median|oecd|union|area|world|note|source|region|"
                         r"jurisdiction|rate|figure)\b", key)):
        return None
    if key in _ALIASES:
        return _ALIASES[key]
    if len(clean) == 3 and clean.isupper():
        try:
            return pycountry.countries.get(alpha_3=clean).alpha_3
        except (AttributeError, KeyError):
            return None
    try:
        return pycountry.countries.lookup(clean).alpha_3
    except LookupError:
        pass
    # Names such as "Bolivia, Plurinational State of" or "Iran, Islamic Republic of"
    for c in pycountry.countries:
        for attr in ("name", "common_name", "official_name"):
            val = getattr(c, attr, None)
            if val and val.lower().split(",")[0].strip() == key:
                return c.alpha_3
    return None


# --------------------------------------------------------------------------
# Fetchers (cached; the Refresh button clears the cache)
# --------------------------------------------------------------------------
@st.cache_data(ttl=24 * 3600, show_spinner=False)
def fetch_betas() -> dict:
    """Damodaran industry betas (US) from betas.xls."""
    meta = {"source": BETAS_URL, "fetched": _now(), "ok": False, "date": "n/a", "error": ""}
    try:
        resp = _get(BETAS_URL)
        raw_sheets = pd.read_excel(io.BytesIO(resp.content), sheet_name=None, header=None)
        sheet_name = next(
            (s for s in raw_sheets if "industry" in s.lower() and "average" in s.lower()),
            next(iter(raw_sheets)),
        )
        raw = raw_sheets[sheet_name]

        # Locate header row ("Industry Name")
        hdr_idx = next(
            i for i, row in raw.iterrows()
            if any(isinstance(v, str) and v.strip().lower() == "industry name" for v in row)
        )
        # Look for a "Date updated" stamp above the table
        for i in range(hdr_idx):
            vals = [v for v in raw.iloc[i].tolist() if pd.notna(v)]
            for j, v in enumerate(vals):
                if isinstance(v, str) and "date" in v.lower():
                    nxt = vals[j + 1] if j + 1 < len(vals) else None
                    if isinstance(nxt, (datetime, pd.Timestamp)):
                        meta["date"] = pd.Timestamp(nxt).strftime("%Y-%m-%d")
                    elif nxt is not None:
                        meta["date"] = str(nxt)
                    else:
                        meta["date"] = v.strip()
        if meta["date"] == "n/a" and resp.headers.get("Last-Modified"):
            meta["date"] = f"File last modified {resp.headers['Last-Modified']}"

        df = raw.iloc[hdr_idx + 1:].copy()
        df.columns = [str(c).strip() for c in raw.iloc[hdr_idx]]
        df = df[df["Industry Name"].notna()]
        df["Industry Name"] = df["Industry Name"].astype(str).str.strip()

        def find_col(*must: str, exclude: tuple[str, ...] = ()) -> str | None:
            for c in df.columns:
                lc = c.lower()
                if all(m in lc for m in must) and not any(e in lc for e in exclude):
                    return c
            return None

        col_unlev = find_col("unlevered beta", exclude=("cash",))
        col_unlev_cash = find_col("unlevered beta", "cash")
        col_firms = find_col("number of firms")
        col_de = find_col("d/e")
        out = pd.DataFrame({
            "Industry": df["Industry Name"],
            "Number of firms": pd.to_numeric(df[col_firms], errors="coerce") if col_firms else None,
            "Unlevered beta": pd.to_numeric(df[col_unlev], errors="coerce"),
            "Unlevered beta (cash-corrected)": (
                pd.to_numeric(df[col_unlev_cash], errors="coerce") if col_unlev_cash else None
            ),
            "Industry D/E": pd.to_numeric(df[col_de], errors="coerce") if col_de else None,
        }).dropna(subset=["Unlevered beta"])
        meta["ok"] = True
        return {"data": out.reset_index(drop=True), "meta": meta}
    except Exception as exc:  # noqa: BLE001
        meta["error"] = f"{type(exc).__name__}: {exc}"
        return {"data": pd.DataFrame(), "meta": meta}


@st.cache_data(ttl=24 * 3600, show_spinner=False)
def fetch_tax_rates() -> dict:
    """OECD Corporate Tax Statistics 2024, Figure 2.1 (statutory CIT rates, 2024).

    The OECD chapter page draws its rates as a chart, not an HTML table.
    The figure's numbers are published through its StatLink Excel file,
    which is downloaded and parsed here.
    """
    meta = {
        "source": f"{OECD_PAGE_URL} (data: Figure 2.1 StatLink {OECD_STATLINK_FIG21})",
        "fetched": _now(), "ok": False,
        "date": "Corporate Tax Statistics 2024 — combined statutory CIT rates for 2024",
        "error": "",
    }
    try:
        resp = _get(OECD_STATLINK_FIG21)
        content = resp.content
        ctype = resp.headers.get("Content-Type", "")
        if "html" in ctype.lower() or content[:2] != b"PK":
            # Landing page: find the actual spreadsheet link
            soup = BeautifulSoup(resp.text, "lxml")
            link = next(
                (a["href"] for a in soup.find_all("a", href=True)
                 if re.search(r"\.xlsx?($|\?)", a["href"], re.I)),
                None,
            )
            if not link:
                raise ValueError("StatLink did not return a spreadsheet")
            content = _get(requests.compat.urljoin(resp.url, link)).content

        sheets = pd.read_excel(io.BytesIO(content), sheet_name=None, header=None)
        records: dict[str, tuple[str, float]] = {}
        for raw in sheets.values():
            combined_col = None
            for i in range(min(len(raw), 40)):
                for j, v in enumerate(raw.iloc[i].tolist()):
                    if isinstance(v, str) and "combined" in v.lower():
                        combined_col = j
                if combined_col is not None:
                    break
            for _, row in raw.iterrows():
                vals = row.tolist()
                name = next((v for v in vals if isinstance(v, str) and v.strip()), None)
                iso = to_iso3(name) if name else None
                if not iso:
                    continue
                nums = [v for v in vals if isinstance(v, (int, float)) and pd.notna(v)
                        and not isinstance(v, bool)]
                nums = [float(v) for v in nums if 0 <= float(v) <= 100]
                if not nums:
                    continue
                if combined_col is not None and isinstance(vals[combined_col], (int, float)) \
                        and pd.notna(vals[combined_col]):
                    rate = float(vals[combined_col])
                else:
                    # Combined rate = central + sub-central, so it is the largest
                    rate = max(nums)
                records.setdefault(iso, (name.strip(), rate))

        if not records:
            raise ValueError("No jurisdiction rates found in the StatLink file")
        df = pd.DataFrame(
            [(iso, n, r) for iso, (n, r) in records.items()],
            columns=["ISO3", "Jurisdiction", "Rate"],
        )
        if df["Rate"].max() <= 1.0:  # stored as fractions
            df["Rate"] = df["Rate"] * 100
        meta["ok"] = True
        return {"data": df, "meta": meta}
    except Exception as exc:  # noqa: BLE001
        meta["error"] = f"{type(exc).__name__}: {exc}"
        return {"data": pd.DataFrame(columns=["ISO3", "Jurisdiction", "Rate"]), "meta": meta}


@st.cache_data(ttl=24 * 3600, show_spinner=False)
def fetch_bbb_spread() -> dict:
    """Damodaran synthetic-rating table — Baa2/BBB spread for large non-financial firms."""
    meta = {"source": RATINGS_URL, "fetched": _now(), "ok": False, "date": "n/a", "error": ""}
    try:
        resp = _get(RATINGS_URL)
        soup = BeautifulSoup(resp.text, "lxml")
        text = soup.get_text(" ", strip=True)
        m = re.search(r"Data used is as of\s+([A-Za-z]+\s+\d{4})", text)
        if m:
            meta["date"] = m.group(1)
        else:
            m = re.search(r"Date of Analysis:?\s*([^.]{3,60})", text)
            if m:
                meta["date"] = m.group(1).strip()

        spread = None
        # The first Baa2/BBB row belongs to the non-financial table, which is
        # the left-hand table on the page.
        for tr in soup.find_all("tr"):
            cells = [c.get_text(" ", strip=True) for c in tr.find_all(["td", "th"])]
            for k, c in enumerate(cells):
                if re.fullmatch(r"Baa2\s*/\s*BBB", c):
                    for nxt in cells[k + 1:]:
                        pm = re.search(r"(-?\d+(?:\.\d+)?)\s*%", nxt)
                        if pm:
                            spread = float(pm.group(1))
                            break
                    break
            if spread is not None:
                break
        if spread is None:
            raise ValueError("Baa2/BBB row not found")
        meta["ok"] = True
        return {"data": spread, "meta": meta}
    except Exception as exc:  # noqa: BLE001
        meta["error"] = f"{type(exc).__name__}: {exc}"
        return {"data": None, "meta": meta}


@st.cache_data(ttl=6 * 3600, show_spinner=False)
def fetch_bond_yields() -> dict:
    """Scrape 10-year government bond yields from Trading Economics (no API)."""
    meta = {"source": BONDS_URL, "fetched": _now(), "ok": False, "date": "per-row quote date", "error": ""}
    try:
        resp = _get(BONDS_URL)
        soup = BeautifulSoup(resp.text, "lxml")
        today = datetime.now()
        rows = {}
        for tr in soup.select("table tr"):
            a = tr.find("a", href=re.compile(r"/government-bond-yield$"))
            if not a:
                continue
            country = a.get_text(" ", strip=True)
            if country in rows:
                continue
            p_td = tr.find("td", id="p")
            if p_td is None:
                tds = tr.find_all("td")
                p_td = tds[2] if len(tds) > 2 else None
            try:
                yld = float(p_td.get_text(strip=True).replace(",", ""))
            except (AttributeError, ValueError):
                continue
            d_td = tr.find("td", id="date")
            raw_date = d_td.get_text(strip=True) if d_td else ""
            quote_date = raw_date
            # Dates look like "Nov/13" (no year) or a time like "10:15"
            dm = re.fullmatch(r"([A-Za-z]{3})/(\d{1,2})", raw_date)
            if dm:
                try:
                    dt = datetime.strptime(f"{dm.group(1)} {dm.group(2)} {today.year}", "%b %d %Y")
                    if dt > today:
                        dt = dt.replace(year=today.year - 1)
                    quote_date = dt.strftime("%Y-%m-%d")
                except ValueError:
                    pass
            elif re.fullmatch(r"\d{1,2}:\d{2}(:\d{2})?", raw_date):
                quote_date = f"{today:%Y-%m-%d} {raw_date} (intraday)"
            rows[country] = {"Country": country, "10Y yield (%)": yld, "Quote date": quote_date}
        if not rows:
            raise ValueError("No bond rows parsed (page layout changed or access blocked)")
        df = pd.DataFrame(rows.values()).sort_values("Country").reset_index(drop=True)
        df["ISO3"] = df["Country"].map(to_iso3)
        meta["ok"] = True
        return {"data": df, "meta": meta}
    except Exception as exc:  # noqa: BLE001
        meta["error"] = f"{type(exc).__name__}: {exc}"
        return {"data": pd.DataFrame(columns=["Country", "10Y yield (%)", "Quote date", "ISO3"]),
                "meta": meta}


# --------------------------------------------------------------------------
# Finance
# --------------------------------------------------------------------------
def relever_beta(beta_u: float, d: float, t: float) -> float:
    """Hamada: βL = βU × [1 + (1 − t) × D/E], with D/E = d / (1 − d)."""
    de = d / (1 - d) if d < 1 else float("inf")
    return beta_u * (1 + (1 - t) * de)


def compute(beta_u, erp, d, t, rf_us, rf_fx, spread_us, spread_fx) -> dict:
    """All rates as decimals. Returns results for USD and the foreign currency."""
    beta_l = relever_beta(beta_u, d, t)
    re_usd = rf_us + beta_l * erp                                    # CAPM
    re_fx = (1 + re_usd) * (1 + rf_fx) / (1 + rf_us) - 1             # parity conversion
    re_fx_local_capm = rf_fx + beta_l * erp                          # alternative
    rd_usd = rf_us + spread_us
    rd_fx = rf_fx + spread_fx
    wacc_usd = (1 - d) * re_usd + d * rd_usd * (1 - t)
    wacc_fx = (1 - d) * re_fx + d * rd_fx * (1 - t)
    return {
        "beta_l": beta_l, "re_usd": re_usd, "re_fx": re_fx, "re_fx_local_capm": re_fx_local_capm,
        "rd_usd": rd_usd, "rd_fx": rd_fx,
        "rd_usd_at": rd_usd * (1 - t), "rd_fx_at": rd_fx * (1 - t),
        "wacc_usd": wacc_usd, "wacc_fx": wacc_fx,
    }


def implied_fx_spread(rf_us: float, rf_fx: float, spread_us: float) -> float:
    """Foreign BBB spread implied by converting the USD cost of debt with the
    same parity relation used for equity:
        (1 + Rd_fx) = (1 + Rd_usd) × (1 + Rf_fx) / (1 + Rf_us);  spread_fx = Rd_fx − Rf_fx
    """
    rd_usd = rf_us + spread_us
    rd_fx = (1 + rd_usd) * (1 + rf_fx) / (1 + rf_us) - 1
    return rd_fx - rf_fx


def pct(x: float | None, nd: int = 2) -> str:
    return "—" if x is None or pd.isna(x) else f"{x * 100:.{nd}f}%"


# --------------------------------------------------------------------------
# UI helpers
# --------------------------------------------------------------------------
def override_input(label: str, fetched: float | None, key: str, *, step: float = 0.01,
                   fmt: str = "%.2f", help_txt: str = "", suffix: str = "%",
                   source_note: str = "fetched") -> tuple[float, bool]:
    """A checkbox + number input. Returns (value, overridden)."""
    has_fetch = fetched is not None and not pd.isna(fetched)
    shown = f"{fetched:{fmt[1:]}}{suffix}" if has_fetch else "not available"
    over = st.checkbox(
        f"Override {label}", key=f"ov_{key}", value=not has_fetch,
        help=help_txt or f"Fetched value: {shown}",
    )
    if over:
        val = st.number_input(
            f"{label} ({suffix.strip() or 'value'})", value=float(fetched) if has_fetch else 0.0,
            step=step, format=fmt, key=f"in_{key}",
        )
        return float(val), True
    st.caption(f"{label}: **{shown}** ({source_note})")
    return float(fetched) if has_fetch else 0.0, False


def build_excel(sheets: dict[str, pd.DataFrame]) -> bytes:
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as xw:
        for name, df in sheets.items():
            df.to_excel(xw, sheet_name=name[:31], index=False)
            ws = xw.sheets[name[:31]]
            for col in ws.columns:
                width = max(len(str(c.value)) if c.value is not None else 0 for c in col)
                ws.column_dimensions[col[0].column_letter].width = min(max(12, width + 2), 80)
    return buf.getvalue()


# --------------------------------------------------------------------------
# App
# --------------------------------------------------------------------------
def main() -> None:
    st.set_page_config(page_title="Cost of Capital: USD vs Foreign Currency", layout="wide")
    st.title("Cost of Equity & WACC — USD vs. foreign currency")
    st.caption(
        "CAPM cost of equity, BBB cost of debt and WACC for a project, computed in USD and in "
        "the currency of a selected country with the same tax rate, debt share and equity risk premium."
    )

    top = st.columns([1, 5])
    if top[0].button("🔄 Refresh data", help="Clear the cache and fetch all sources again"):
        st.cache_data.clear()
        st.rerun()

    with st.spinner("Fetching market data…"):
        betas = fetch_betas()
        taxes = fetch_tax_rates()
        bbb = fetch_bbb_spread()
        bonds = fetch_bond_yields()

    sources_df = pd.DataFrame([
        {"Dataset": "Industry unlevered betas (US)", "Source": betas["meta"]["source"],
         "Data date": betas["meta"]["date"], "Fetched": betas["meta"]["fetched"],
         "Status": "OK" if betas["meta"]["ok"] else f"FAILED – {betas['meta']['error']}"},
        {"Dataset": "Statutory corporate income tax rates", "Source": taxes["meta"]["source"],
         "Data date": taxes["meta"]["date"], "Fetched": taxes["meta"]["fetched"],
         "Status": "OK" if taxes["meta"]["ok"] else f"FAILED – {taxes['meta']['error']}"},
        {"Dataset": "USD Baa2/BBB default spread (large non-financial firms)",
         "Source": bbb["meta"]["source"], "Data date": bbb["meta"]["date"],
         "Fetched": bbb["meta"]["fetched"],
         "Status": "OK" if bbb["meta"]["ok"] else f"FAILED – {bbb['meta']['error']}"},
        {"Dataset": "10-year government bond yields", "Source": bonds["meta"]["source"],
         "Data date": bonds["meta"]["date"], "Fetched": bonds["meta"]["fetched"],
         "Status": "OK" if bonds["meta"]["ok"] else f"FAILED – {bonds['meta']['error']}"},
    ])
    failed = sources_df[sources_df["Status"] != "OK"]
    if not failed.empty:
        st.warning(
            "Some sources could not be fetched: " + ", ".join(failed["Dataset"]) +
            ". Use the manual overrides in the sidebar for those inputs, or press Refresh."
        )

    with st.expander("📚 Data sources, dates and fetch status", expanded=False):
        st.dataframe(sources_df, hide_index=True, width="stretch")

    beta_df: pd.DataFrame = betas["data"]
    tax_df: pd.DataFrame = taxes["data"]
    bond_df: pd.DataFrame = bonds["data"]

    # ---------------- Sidebar: selections ----------------
    sb = st.sidebar
    sb.header("Project settings")

    industries = beta_df["Industry"].tolist() if not beta_df.empty else [DEFAULT_INDUSTRY]
    ind_idx = industries.index(DEFAULT_INDUSTRY) if DEFAULT_INDUSTRY in industries else 0
    industry = sb.selectbox("Industry", industries, index=ind_idx)

    countries = bond_df["Country"].tolist() if not bond_df.empty else []
    if not countries and not tax_df.empty:
        countries = sorted(tax_df["Jurisdiction"].tolist())
    if DEFAULT_COUNTRY not in countries:
        countries = [DEFAULT_COUNTRY] + countries
    country = sb.selectbox("Target country", countries, index=countries.index(DEFAULT_COUNTRY))
    country_iso = to_iso3(country)

    beta_variant = sb.radio(
        "Damodaran beta measure",
        ["Unlevered beta", "Unlevered beta (cash-corrected)"],
        help="Damodaran publishes an unlevered beta and one also corrected for cash holdings.",
    )
    erp = sb.slider("Equity risk premium (%)", 0.0, 15.0, DEFAULT_ERP, 0.1) / 100
    d = sb.slider("Target debt share D/(D+E) (%)", 0.0, 90.0, DEFAULT_DEBT, 1.0) / 100

    # Project tax-rate switch
    tax_choice = sb.radio(
        "Project corporate tax rate",
        ["United States", f"{country} (selected country)"] if country != DEFAULT_COUNTRY
        else ["United States"],
        horizontal=False,
        help="The same tax rate is applied to both the USD and the foreign-currency calculation.",
    )
    use_fx_tax = tax_choice != "United States"

    # ---------------- Fetched values ----------------
    def lookup_tax(iso: str | None) -> float | None:
        if iso and not tax_df.empty:
            hit = tax_df.loc[tax_df["ISO3"] == iso, "Rate"]
            if not hit.empty:
                return float(hit.iloc[0])
        return None

    def lookup_yield(name: str) -> tuple[float | None, str]:
        if not bond_df.empty:
            hit = bond_df.loc[bond_df["Country"] == name]
            if not hit.empty:
                return float(hit["10Y yield (%)"].iloc[0]), str(hit["Quote date"].iloc[0])
        return None, "n/a"

    beta_row = beta_df.loc[beta_df["Industry"] == industry] if not beta_df.empty else pd.DataFrame()
    fetched_beta = (
        float(beta_row[beta_variant].iloc[0])
        if not beta_row.empty and pd.notna(beta_row[beta_variant].iloc[0]) else None
    )
    fetched_tax_us = lookup_tax("USA")
    fetched_tax_fx = lookup_tax(country_iso)
    fetched_rf_us, rf_us_date = lookup_yield(DEFAULT_COUNTRY)
    fetched_rf_fx, rf_fx_date = lookup_yield(country)
    fetched_spread = bbb["data"]

    # ---------------- Sidebar: overrides ----------------
    sb.header("Inputs & manual overrides")
    with sb.container(border=True):
        beta_u, ov_beta = override_input(
            "Unlevered beta", fetched_beta, f"beta_{industry}_{beta_variant}",
            step=0.01, fmt="%.4f", suffix="",
        )
    with sb.container(border=True):
        if fetched_tax_us is None and not taxes["meta"]["ok"]:
            st.caption(f"OECD fetch failed — US default set to the {US_FALLBACK_TAX:.0f}% federal rate.")
        t_us, ov_tus = override_input(
            "US corporate tax rate", fetched_tax_us if fetched_tax_us is not None else US_FALLBACK_TAX
            if not taxes["meta"]["ok"] else None, "tax_us",
            source_note="fetched" if fetched_tax_us is not None else "fallback",
        )
        if country != DEFAULT_COUNTRY:
            t_fx, ov_tfx = override_input(f"{country} corporate tax rate", fetched_tax_fx,
                                          f"tax_fx_{country}")
        else:
            t_fx, ov_tfx = t_us, ov_tus
    with sb.container(border=True):
        spread_us, ov_sus = override_input("USD BBB spread", fetched_spread, "spread_us")
    with sb.container(border=True):
        rf_us, ov_rfus = override_input("US 10Y government bond yield", fetched_rf_us, "rf_us")
        if country != DEFAULT_COUNTRY:
            rf_fx, ov_rffx = override_input(f"{country} 10Y government bond yield",
                                            fetched_rf_fx, f"rf_fx_{country}")
        else:
            rf_fx, ov_rffx = rf_us, ov_rfus
    with sb.container(border=True):
        auto_spread_fx = implied_fx_spread(rf_us / 100, rf_fx / 100, spread_us / 100) * 100
        ov_sfx = st.checkbox(
            f"Override {country} BBB spread", key=f"ov_spread_fx_{country}", value=False,
            help="When off, the foreign spread is calculated automatically by converting the "
                 "USD cost of debt with the same parity relation used for equity.",
        )
        if ov_sfx:
            spread_fx = float(st.number_input(
                f"{country} BBB spread (%)", value=round(auto_spread_fx, 4), step=0.01,
                format="%.2f", key=f"in_spread_fx_{country}",
            ))
        else:
            spread_fx = auto_spread_fx
            st.caption(f"{country} BBB spread: **{auto_spread_fx:.2f}%** (automatic)")

    t = (t_fx if use_fx_tax else t_us) / 100
    if use_fx_tax and fetched_tax_fx is None and not ov_tfx:
        st.error(f"No OECD tax rate found for {country}. Enable the override and enter a rate.")

    res = compute(beta_u, erp, d, t, rf_us / 100, rf_fx / 100, spread_us / 100, spread_fx / 100)

    # ---------------- Results ----------------
    fx_label = f"{country} currency" if country != DEFAULT_COUNTRY else "Selected country (= USD)"
    st.subheader(f"Results — {industry}")
    st.caption(
        f"Project tax rate: **{t * 100:.2f}%** ({'selected country' if use_fx_tax else 'United States'}) · "
        f"Debt share: **{d * 100:.0f}%** · ERP: **{erp * 100:.2f}%** · "
        f"Relevered equity beta: **{res['beta_l']:.3f}**"
    )

    c1, c2, c3 = st.columns(3)
    c1.metric("Cost of equity", pct(res["re_usd"]), help="USD")
    c1.metric(f"Cost of equity — {fx_label}", pct(res["re_fx"]),
              delta=f"{(res['re_fx'] - res['re_usd']) * 100:+.2f} pp vs USD", delta_color="off")
    c2.metric("Pre-tax cost of debt — USD", pct(res["rd_usd"]))
    c2.metric(f"Pre-tax cost of debt — {fx_label}", pct(res["rd_fx"]),
              delta=f"{(res['rd_fx'] - res['rd_usd']) * 100:+.2f} pp vs USD", delta_color="off")
    c3.metric("WACC — USD", pct(res["wacc_usd"]))
    c3.metric(f"WACC — {fx_label}", pct(res["wacc_fx"]),
              delta=f"{(res['wacc_fx'] - res['wacc_usd']) * 100:+.2f} pp vs USD", delta_color="off")

    src = lambda ov: "Manual" if ov else "Fetched"  # noqa: E731
    results_df = pd.DataFrame([
        ["Risk-free rate (10Y govt. bond)", pct(rf_us / 100), pct(rf_fx / 100),
         f"{src(ov_rfus)} · {rf_us_date} / {src(ov_rffx)} · {rf_fx_date}"],
        ["Unlevered beta", f"{beta_u:.4f}", f"{beta_u:.4f}", f"{src(ov_beta)} · Damodaran ({beta_variant})"],
        ["Relevered equity beta", f"{res['beta_l']:.4f}", f"{res['beta_l']:.4f}", "Hamada"],
        ["Equity risk premium", pct(erp), pct(erp), "User input"],
        ["Cost of equity (Re)", pct(res["re_usd"]), pct(res["re_fx"]),
         "USD: CAPM · Foreign: parity conversion"],
        ["  memo: foreign Re by direct local CAPM", "", pct(res["re_fx_local_capm"]),
         "Rf_fx + β × ERP (for reference)"],
        ["BBB default spread", pct(spread_us / 100), pct(spread_fx / 100),
         f"{src(ov_sus)} / {'Manual' if ov_sfx else 'Automatic (parity)'}"],
        ["Pre-tax cost of debt (Rd)", pct(res["rd_usd"]), pct(res["rd_fx"]), "Rf + spread"],
        ["Corporate tax rate (t)", pct(t), pct(t),
         f"{'Selected country' if use_fx_tax else 'US'} rate · "
         f"{src(ov_tfx if use_fx_tax else ov_tus)}"],
        ["After-tax cost of debt", pct(res["rd_usd_at"]), pct(res["rd_fx_at"]), "Rd × (1 − t)"],
        ["Target debt share (d)", pct(d, 0), pct(d, 0), "User input"],
        ["WACC", pct(res["wacc_usd"]), pct(res["wacc_fx"]), "(1 − d)·Re + d·Rd·(1 − t)"],
    ], columns=["Item", "USD", fx_label, "Source / note"])
    st.dataframe(results_df, hide_index=True, width="stretch")

    with st.expander("🧮 Method and main assumptions", expanded=True):
        st.markdown(f"""
**Formulas**

* **Relevered beta (Hamada):** βL = βU × [1 + (1 − t) × D/E], with D/E = d / (1 − d) = {d / (1 - d) if d < 1 else float('inf'):.3f}
* **Cost of equity, USD (CAPM):** Re = Rf + βL × ERP
* **Cost of equity, foreign currency (parity):** (1 + Res) = (1 + Re) × (1 + Rfs) / (1 + Rf)
* **Pre-tax cost of debt:** Rd = Rf + BBB spread (USD) and Rds = Rfs + foreign BBB spread
* **Automatic foreign BBB spread (when override is off):** (1 + Rds) = (1 + Rd) × (1 + Rfs) / (1 + Rf), then spread = Rds − Rfs
* **WACC:** (1 − d) × Re + d × Rd × (1 − t)

**Main assumptions**

1. Industry betas come from Damodaran's **US** industry dataset and apply to the project in both currencies; the
   same relevered beta, ERP, debt share and tax rate are used for both currencies.
2. Ten-year government bond yields stand in for the risk-free rate in each currency. They include any
   sovereign default risk of the country; no separate country risk premium is added.
3. The foreign cost of equity is the USD cost of equity converted with the ratio of the two risk-free rates
   (a relative interest-rate/inflation parity view). It is the same project risk in another currency, not a
   higher-risk project. The direct local CAPM figure is shown only for reference.
4. The USD BBB spread is Damodaran's spread for a Baa2/BBB rating for large non-financial service firms.
   Unless overridden, the foreign spread is implied by the same parity conversion.
5. The tax rate is the OECD **combined statutory** corporate rate (central + sub-central) for 2024, applied
   to both calculations. Use the sidebar switch to pick the US or the selected-country rate.
6. Inputs are market snapshots taken on the fetch dates shown under *Data sources*; yields move daily.
""")

    # ---------------- All-industry comparison ----------------
    st.subheader("All-industry comparison")
    st.caption(
        f"Same country ({country}), tax rate, debt share, ERP, risk-free rates and spreads. "
        "Only the unlevered beta changes by industry."
    )
    comp_df = pd.DataFrame()
    if not beta_df.empty:
        rows = []
        for _, r in beta_df.iterrows():
            bu = r[beta_variant]
            if pd.isna(bu):
                continue
            if r["Industry"] == industry and ov_beta:
                bu = beta_u
            x = compute(float(bu), erp, d, t, rf_us / 100, rf_fx / 100,
                        spread_us / 100, spread_fx / 100)
            rows.append({
                "Industry": r["Industry"], "Unlevered beta": round(float(bu), 4),
                "Levered beta": round(x["beta_l"], 4),
                "Re USD (%)": round(x["re_usd"] * 100, 2), f"Re {country} (%)": round(x["re_fx"] * 100, 2),
                "Rd USD (%)": round(x["rd_usd"] * 100, 2), f"Rd {country} (%)": round(x["rd_fx"] * 100, 2),
                "WACC USD (%)": round(x["wacc_usd"] * 100, 2),
                f"WACC {country} (%)": round(x["wacc_fx"] * 100, 2),
                "WACC diff (pp)": round((x["wacc_fx"] - x["wacc_usd"]) * 100, 2),
            })
        comp_df = pd.DataFrame(rows)

        sort_by = st.selectbox("Sort by", ["WACC USD (%)", "Industry", "Unlevered beta"], index=0)
        comp_view = comp_df.sort_values(sort_by, ascending=(sort_by == "Industry"))

        def highlight(row):
            return ["background-color: rgba(255, 200, 0, 0.25)" if row["Industry"] == industry else ""
                    for _ in row]

        st.dataframe(comp_view.style.apply(highlight, axis=1), hide_index=True,
                     width="stretch", height=420)

        chart_df = comp_df.melt(
            id_vars=["Industry"], value_vars=["WACC USD (%)", f"WACC {country} (%)"],
            var_name="Currency", value_name="WACC (%)",
        )
        chart_df["Currency"] = chart_df["Currency"].str.replace(r"WACC | \(%\)", "", regex=True)
        order = comp_df.sort_values("WACC USD (%)", ascending=False)["Industry"].tolist()
        chart = (
            alt.Chart(chart_df)
            .mark_bar()
            .encode(
                y=alt.Y("Industry:N", sort=order, title=None, axis=alt.Axis(labelLimit=260)),
                x=alt.X("WACC (%):Q"),
                yOffset="Currency:N",
                color=alt.Color("Currency:N", legend=alt.Legend(orient="top")),
                tooltip=["Industry", "Currency", alt.Tooltip("WACC (%):Q", format=".2f")],
            )
            .properties(height=max(600, 22 * len(order)))
        )
        st.altair_chart(chart, width="stretch")
    else:
        st.info("Industry betas are unavailable, so the all-industry comparison cannot be built.")

    # ---------------- Downloads ----------------
    st.subheader("Download results")
    inputs_df = pd.DataFrame([
        ["Industry", industry], ["Target country", country],
        ["Beta measure", beta_variant], ["Unlevered beta", beta_u],
        ["Equity risk premium (%)", erp * 100], ["Target debt share (%)", d * 100],
        ["Project tax rate used", "Selected country" if use_fx_tax else "United States"],
        ["US tax rate (%)", t_us], [f"{country} tax rate (%)", t_fx],
        ["US 10Y yield (%)", rf_us], [f"{country} 10Y yield (%)", rf_fx],
        ["USD BBB spread (%)", spread_us], [f"{country} BBB spread (%)", spread_fx],
        [f"{country} BBB spread mode", "Manual" if ov_sfx else "Automatic (parity)"],
        ["Generated", _now()],
    ], columns=["Input", "Value"])
    inputs_df["Value"] = inputs_df["Value"].astype(str)

    stamp = datetime.now().strftime("%Y%m%d_%H%M")
    safe = re.sub(r"[^A-Za-z0-9]+", "_", f"{industry}_{country}").strip("_")
    dl1, dl2, dl3 = st.columns(3)
    dl1.download_button(
        "⬇️ Excel workbook (all sheets)",
        data=build_excel({
            "Results": results_df, "Inputs": inputs_df, "All industries": comp_df,
            "Sources": sources_df,
        }),
        file_name=f"wacc_{safe}_{stamp}.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    dl2.download_button(
        "⬇️ Results (CSV)", data=results_df.to_csv(index=False).encode("utf-8"),
        file_name=f"wacc_results_{safe}_{stamp}.csv", mime="text/csv",
    )
    dl3.download_button(
        "⬇️ All-industry comparison (CSV)", data=comp_df.to_csv(index=False).encode("utf-8"),
        file_name=f"wacc_all_industries_{safe}_{stamp}.csv", mime="text/csv",
        disabled=comp_df.empty,
    )

    # ---------------- Raw data ----------------
    with st.expander("🔎 Raw fetched data"):
        tabs = st.tabs(["Betas", "Tax rates", "Bond yields"])
        tabs[0].dataframe(beta_df, hide_index=True, width="stretch")
        tabs[1].dataframe(tax_df, hide_index=True, width="stretch")
        tabs[2].dataframe(bond_df, hide_index=True, width="stretch")
        st.caption(f"USD Baa2/BBB spread: {fetched_spread if fetched_spread is not None else 'n/a'}%")

    st.caption("For analysis and education only — not investment advice.")


if __name__ == "__main__":
    main()
