import argparse
import pandas as pd
import yfinance as yf
import time


def safe_get(info, key, default=None):
    val = info.get(key, default)
    if val in [None, "None"]:
        return default
    return val


def fetch_financials(ticker):
    try:
        tk = yf.Ticker(ticker)
        info = tk.info

        revenue = safe_get(info, "totalRevenue")
        ebitda = safe_get(info, "ebitda")
        debt = safe_get(info, "totalDebt")
        cash = safe_get(info, "totalCash")

        nd_ebitda = None
        ebitda_margin = None

        if revenue and ebitda:
            ebitda_margin = ebitda / revenue

        if debt and cash and ebitda and ebitda != 0:
            nd_ebitda = (debt - cash) / ebitda

        return {
            "Revenue": revenue,
            "EBITDA": ebitda,
            "EBITDA_Margin": ebitda_margin,
            "ND_EBITDA": nd_ebitda,
        }

    except Exception as e:
        print(f"Error fetching {ticker}: {e}")
        return {}


def update_file(input_path, output_path):
    df = pd.read_excel(input_path)

    if "Ticker" not in df.columns:
        raise ValueError("Excel must contain a 'Ticker' column.")

    results = []

    for t in df["Ticker"]:
        print(f"Updating {t}...")
        data = fetch_financials(t)
        results.append(data)
        time.sleep(0.5)

    res_df = pd.DataFrame(results)

    for col in res_df.columns:
        df[col] = res_df[col]

    df.to_excel(output_path, index=False)
    print("Update complete.")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)

    args = parser.parse_args()
    update_file(args.input, args.output)


if __name__ == "__main__":
    main()
