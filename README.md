Cost of Equity & WACC Comparator — USD vs. Foreign Currency

A Streamlit app (Python 3.12) that works out a project's cost of equity, cost of debt and WACC in USD and in the currency of any selected country. It applies the same tax rate, debt share and equity risk premium to both currencies and shows the results side by side.

Quick start
bash
python3.12 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
streamlit run app.py

The app opens at http://localhost:8501.

Data sources (fetched live, cached, with a 🔄 Refresh button)
Input	Source	What is used
Unlevered industry betas	Damodaran, betas.xls — https://pages.stern.nyu.edu/~adamodar/pc/datasets/betas.xls	"Unlevered beta" (or, optionally, "Unlevered beta corrected for cash") from the Industry Averages sheet
Corporate tax rates	OECD Corporate Tax Statistics 2024, ch. 2 — https://www.oecd.org/en/publications/corporate-tax-statistics-2024_9c27d6e8-en/full-report/statutory-corporate-income-tax-rates_440f9286.html	Combined statutory CIT rates for 2024. The chapter page draws them as a chart, not a table, so the app reads the Excel file behind Figure 2.1 (StatLink https://stat.link/0qkxo1)
USD BBB spread	Damodaran, Ratings, Interest Coverage Ratios and Default Spread — https://pages.stern.nyu.edu/adamodar/New_Home_Page/datafile/ratings.html	Spread for Baa2/BBB in the table for large non-financial service firms
10-year government bond yields	Trading Economics — https://tradingeconomics.com/bonds	Scraped when the site allows it (no API); otherwise pasted in (see below). Each country's quote date is kept

Expand "Data sources, dates and fetch status" in the app to see each source's URL, its data date (for example "January 2026" for Damodaran), when it was fetched, and whether the fetch worked. Bond yields are cached for 6 hours and the other sources for 24 hours. Refresh data clears the cache and fetches everything again.

Importing the bond yields (Trading Economics)

Trading Economics doesn't put its yields table in the page HTML. The table arrives from a second request (/bonds?partial=markets), and that request is protected by an AWS WAF bot check that runs in the visitor's browser and can escalate to a CAPTCHA. Scripts, this app included, usually get an empty HTTP 202 response. The app does not try to defeat that check.

When the scrape returns nothing, the app opens a "📋 Import 10-year yields from Trading Economics (paste)" panel:

Open https://tradingeconomics.com/bonds in your browser.
Select the tables with your mouse, from the Major heading down to the last country, and copy them (Ctrl/Cmd + C).
Paste into the panel. The app reads each country, its yield and its quote date, and records "Pasted by user <time>" in the sources table.

Pasted values replace fetched ones. They stay for the rest of your browser session, and Refresh data doesn't clear them. You can also type any yield by hand in the sidebar.

Missing inputs

The app never substitutes 0% for a missing input. If a required value (beta, either bond yield, a tax rate, a spread) is unavailable, the results are hidden and a message lists what's missing. Fields that couldn't be fetched appear in the sidebar as blank boxes marked "not fetched, enter manually". If the OECD fetch fails, the US tax rate falls back to the 21% federal rate, labelled "fallback".

Features
Selections: industry, target country (every country on the Trading Economics bonds page), beta measure.
Sliders: equity risk premium and target debt share.
Manual overrides (tick "Override …" in the sidebar):
Unlevered beta
Corporate tax rate (US and the selected country)
USD BBB spread
10-year government bond yield (US and the selected country)
Foreign BBB spread. When the override is off, the app calculates this spread automatically (see below).
Project tax-rate switch (panel above the results): choose the United States rate or the selected country's rate. Each option shows its current value. The rate you pick is the single project tax rate used in both the USD and the foreign-currency calculations, and the panel shows it as "Rate applied to both currencies".
The switch is greyed out while the target country is the United States, because both options would be the same rate. Pick a foreign country to enable it.
Your choice stays the same when you change country.
If OECD has no rate for the selected country, the app uses the US rate and says so. Tick the override in the sidebar to enter a rate yourself.
Side-by-side results: cost of equity, pre-tax and after-tax cost of debt, and WACC in USD and in the foreign currency, with the difference in percentage points.
Method and assumptions panel.
All-industry comparison: a sortable table and a bar chart of WACC in both currencies for every Damodaran industry, with the selected industry highlighted.
Downloads: an Excel workbook (Results, Inputs, All industries, Sources), a results CSV and an all-industry CSV.
Defaults
Setting	Default
Industry	Software (System & Application)
Selected country	United States
Tax rate	US
Equity risk premium	6.5%
Debt financing	30%
Methodology

All rates are annual and in decimal form inside the app.

Relevered beta (Hamada): βL = βU × [1 + (1 − t) × D/E], with D/E = d / (1 − d)
Cost of equity, USD (CAPM): Re = Rf + βL × ERP
Cost of equity, foreign currency: (1 + Res) = (1 + Re) × (1 + Rfs) / (1 + Rf) A direct local CAPM figure (Rfs + βL × ERP) is also shown, for reference only.
Pre-tax cost of debt: Rd = Rf + USD BBB spread; Rds = Rfs + foreign BBB spread
Automatic foreign BBB spread: (1 + Rds) = (1 + Rd) × (1 + Rfs) / (1 + Rf), then spread = Rds − Rfs
WACC: WACC = (1 − d) × Re + d × Rd × (1 − t)

where d = target debt share, t = project corporate tax rate, Rf / Rfs = US / foreign 10-year government bond yield.

Main assumptions
Damodaran's betas are for US industries and are applied to the project in both currencies.
Ten-year government yields stand in for the risk-free rate. They include any sovereign risk of the country, and no separate country risk premium is added.
The foreign-currency figures express the same project risk in another currency, using relative interest-rate (inflation) parity.
The tax rate is the OECD combined statutory rate (central plus sub-central government) for 2024.
Every input is a snapshot taken on the dates shown in the app.
Maintenance notes

The data is scraped, so a source changing its page layout can break that source's parser. Each parser is a separate function at the top of app.py:

fetch_betas() — finds the "Industry Name" header row in the .xls file.
fetch_tax_rates() — follows the Figure 2.1 StatLink and reads jurisdiction and rate rows. For each country it uses the "Combined" column if there is one; otherwise it takes the largest rate in the row. To use a newer OECD edition, update OECD_PAGE_URL and OECD_STATLINK_FIG21.
fetch_bbb_spread() — takes the first Baa2/BBB row, which is the non-financial table.
fetch_bond_yields() / parse_te_html() — try /bonds and /bonds?partial=markets, reading rows that link to /<country>/government-bond-yield and taking their #p (yield) and #date cells. parse_te_paste() handles text copied from the page.

Country names are matched across sources by ISO-3 code (pycountry plus an alias list), so "South Korea" (Trading Economics) matches "Korea" (OECD).

For analysis and education only — not investment advice.
