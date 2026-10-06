"""Peer clustering: the unsupervised task of the Data Science Lab.

Question: do data-driven peer groups match the official sector labels used for
comparable-company analysis?

Two independent views of "similar":

* K-means on standardized company features: how each stock behaves (volatility,
  beta, average correlation with the market) and how the business looks
  (net margin, ROE, revenue growth).
* Hierarchical clustering (average linkage) on the distance 1 - correlation of
  daily returns: which stocks move together.

The number of clusters is chosen from the data (silhouette score, with the
elbow curve reported alongside), then compared with the sector labels using
the adjusted Rand index. Stability is checked by re-clustering bootstrap
resamples of the return history and two halves of the sample.

Features that do not apply to every sector type (EBITDA margin, debt/equity)
are left out, so banks are clustered on the same features as everyone else.
A missing fundamental is replaced by the cross-sectional median and recorded.
"""

import numpy as np
import pandas as pd
from scipy.cluster.hierarchy import fcluster, linkage
from scipy.spatial.distance import squareform
from sklearn.cluster import KMeans
from sklearn.decomposition import PCA
from sklearn.metrics import adjusted_rand_score, silhouette_score
from sklearn.preprocessing import StandardScaler

from src.ml.evaluation import block_bootstrap_indices

SEED = 42
K_RANGE = range(2, 9)
RETURN_FEATURES = ["volatility", "beta", "market_correlation"]
FUNDAMENTAL_FEATURES = ["net_margin", "roe", "revenue_growth"]
FEATURES = RETURN_FEATURES + FUNDAMENTAL_FEATURES
TRADING_DAYS = 252
BOOTSTRAP_RESAMPLES = 200
BOOTSTRAP_BLOCK = 21


# ---------------------------------------------------------------- features ----

def return_features(returns: pd.DataFrame, market: pd.Series) -> pd.DataFrame:
    """Annualized volatility, beta to the market and correlation with it, per company.

    returns: daily returns, one column per company, on dates shared by all.
    market:  daily market returns on the same dates.
    """
    market_variance = market.var(ddof=1)
    return pd.DataFrame({
        "volatility": returns.std(ddof=1) * np.sqrt(TRADING_DAYS),
        "beta": returns.apply(lambda column: column.cov(market)) / market_variance,
        "market_correlation": returns.apply(lambda column: column.corr(market)),
    })


def fundamental_features(metrics: pd.DataFrame, tickers: list[str]) -> tuple[pd.DataFrame, list]:
    """Latest available annual value of each fundamental feature, per company.

    Returns (features, imputed) where imputed lists (ticker, feature) pairs that
    had no value and were set to the median of the other companies.
    """
    rows = metrics[(metrics["period_type"] == "annual")
                   & metrics["metric_name"].isin(FUNDAMENTAL_FEATURES)
                   & metrics["value"].notna()]
    latest = rows.sort_values("period_end_date").groupby(["ticker", "metric_name"]).tail(1)
    table = latest.pivot(index="ticker", columns="metric_name", values="value")
    table = table.reindex(index=tickers, columns=FUNDAMENTAL_FEATURES).astype("float64")
    imputed = [(ticker, feature) for ticker in table.index for feature in table.columns
               if pd.isna(table.at[ticker, feature])]
    return table.fillna(table.median()), imputed


def standardize(table: pd.DataFrame) -> pd.DataFrame:
    scaled = StandardScaler().fit_transform(table)
    return pd.DataFrame(scaled, index=table.index, columns=table.columns)


# -------------------------------------------------------------- clustering ----

def kmeans_labels(scaled: pd.DataFrame, k: int, seed: int = SEED) -> np.ndarray:
    return KMeans(n_clusters=k, n_init=20, random_state=seed).fit_predict(scaled)


def correlation_distance(returns: pd.DataFrame) -> pd.DataFrame:
    """1 - Pearson correlation: 0 for stocks that move together, 2 for exact opposites."""
    distance = 1 - returns.corr()
    values = distance.to_numpy(copy=True)
    np.fill_diagonal(values, 0.0)
    return pd.DataFrame((values + values.T) / 2, index=distance.index, columns=distance.columns)


def hierarchical_linkage(distance: pd.DataFrame) -> np.ndarray:
    return linkage(squareform(distance.to_numpy(), checks=False), method="average")


def hierarchical_labels(distance: pd.DataFrame, k: int) -> np.ndarray:
    return fcluster(hierarchical_linkage(distance), t=k, criterion="maxclust") - 1


def choose_k(scaled: pd.DataFrame, distance: pd.DataFrame, seed: int = SEED) -> pd.DataFrame:
    """Silhouette score for each k and method, plus K-means inertia for the elbow curve."""
    rows = []
    for k in K_RANGE:
        if k >= len(scaled):
            break
        model = KMeans(n_clusters=k, n_init=20, random_state=seed).fit(scaled)
        rows.append({"method": "kmeans", "k": k, "inertia": float(model.inertia_),
                     "silhouette": float(silhouette_score(scaled, model.labels_))})
        labels = hierarchical_labels(distance, k)
        score = (float(silhouette_score(distance.to_numpy(), labels, metric="precomputed"))
                 if len(set(labels)) > 1 else np.nan)
        rows.append({"method": "hierarchical", "k": k, "inertia": np.nan, "silhouette": score})
    return pd.DataFrame(rows)


def best_k(selection: pd.DataFrame, method: str) -> int:
    rows = selection[selection["method"] == method].dropna(subset=["silhouette"])
    return int(rows.loc[rows["silhouette"].idxmax(), "k"])


# -------------------------------------------------------------- validation ----

def stability(returns: pd.DataFrame, market: pd.Series, fundamentals: pd.DataFrame,
              base: dict, k: dict, seed: int = SEED, resamples: int = BOOTSTRAP_RESAMPLES) -> dict:
    """How much do the clusters change when the return history is resampled?

    Block-bootstrap resamples of the trading days (blocks of 21 days) are
    re-clustered with the same k, and each result is compared with the
    full-sample clusters by adjusted Rand index (1 = identical grouping).
    The same is done for the first and second half of the sample.
    Fundamentals are held fixed: only the return-based inputs are resampled.
    """
    rng = np.random.default_rng(seed)

    def recluster(sample: pd.DataFrame, sample_market: pd.Series) -> dict:
        table = standardize(return_features(sample, sample_market).join(fundamentals))
        return {"kmeans": kmeans_labels(table, k["kmeans"], seed),
                "hierarchical": hierarchical_labels(correlation_distance(sample),
                                                    k["hierarchical"])}

    scores = {"kmeans": [], "hierarchical": []}
    for _ in range(resamples):
        index = block_bootstrap_indices(len(returns), BOOTSTRAP_BLOCK, rng)
        labels = recluster(returns.iloc[index], market.iloc[index])
        for method in scores:
            scores[method].append(adjusted_rand_score(base[method], labels[method]))

    half = len(returns) // 2
    halves = [recluster(returns.iloc[:half], market.iloc[:half]),
              recluster(returns.iloc[half:], market.iloc[half:])]
    out = {}
    for method, values in scores.items():
        out[method] = {
            "bootstrap_mean_ari": float(np.mean(values)),
            "bootstrap_p05_ari": float(np.quantile(values, 0.05)),
            "bootstrap_p95_ari": float(np.quantile(values, 0.95)),
            "resamples": resamples,
            "first_half_vs_full_ari": float(adjusted_rand_score(base[method], halves[0][method])),
            "second_half_vs_full_ari": float(adjusted_rand_score(base[method], halves[1][method])),
            "first_vs_second_half_ari": float(adjusted_rand_score(halves[0][method],
                                                                  halves[1][method])),
        }
    return out


def describe_mismatches(assignments: pd.DataFrame, method: str) -> list[str]:
    """Plain statements of where clusters and sectors disagree. Generated from the labels."""
    column = f"{method}_cluster"
    lines = []
    for cluster, group in assignments.groupby(column):
        counts = group["sector"].value_counts()
        main = counts.index[0]
        others = group[group["sector"] != main]
        members = ", ".join(group["ticker"].str.replace(".NS", "", regex=False))
        if others.empty:
            lines.append(f"Cluster {cluster} contains only {main} companies ({members}).")
        else:
            outside = ", ".join(f"{t.replace('.NS', '')} ({s})"
                                for t, s in zip(others["ticker"], others["sector"]))
            lines.append(f"Cluster {cluster} is mostly {main} ({counts.iloc[0]} of {len(group)}) "
                         f"and also contains {outside}.")
    for sector, group in assignments.groupby("sector"):
        spread = group[column].nunique()
        if spread > 1:
            lines.append(f"{sector} companies are spread across {spread} clusters.")
    return lines


# -------------------------------------------------------------- experiment ----

def run_experiment(returns: pd.DataFrame, market: pd.Series, metrics: pd.DataFrame,
                   sectors: pd.Series, seed: int = SEED) -> dict:
    """Cluster the universe and compare the result with the sector labels.

    returns: daily returns, one column per company, aligned dates.
    market:  market returns on the same dates.
    metrics: core.metrics rows (for the fundamental features).
    sectors: sector label per ticker.
    """
    tickers = list(returns.columns)
    fundamentals, imputed = fundamental_features(metrics, tickers)
    # a fundamental no company has a value for cannot be imputed: leave it out
    unavailable = [c for c in fundamentals.columns if fundamentals[c].isna().all()]
    fundamentals = fundamentals.drop(columns=unavailable)
    imputed = [pair for pair in imputed if pair[1] not in unavailable]
    used = [f for f in FEATURES if f not in unavailable]
    raw = return_features(returns, market).join(fundamentals)[used]
    scaled = standardize(raw)
    distance = correlation_distance(returns)

    selection = choose_k(scaled, distance, seed)
    k = {"kmeans": best_k(selection, "kmeans"), "hierarchical": best_k(selection, "hierarchical")}
    labels = {"kmeans": kmeans_labels(scaled, k["kmeans"], seed),
              "hierarchical": hierarchical_labels(distance, k["hierarchical"])}
    n_sectors = sectors.nunique()
    at_sector_count = {"kmeans": kmeans_labels(scaled, n_sectors, seed),
                       "hierarchical": hierarchical_labels(distance, n_sectors)}

    components = PCA(n_components=2, random_state=seed).fit(scaled)
    projected = components.transform(scaled)
    assignments = pd.DataFrame({
        "ticker": tickers, "sector": sectors.reindex(tickers).to_numpy(),
        "kmeans_cluster": labels["kmeans"], "hierarchical_cluster": labels["hierarchical"],
        "kmeans_cluster_at_sector_count": at_sector_count["kmeans"],
        "hierarchical_cluster_at_sector_count": at_sector_count["hierarchical"],
        "pc1": projected[:, 0], "pc2": projected[:, 1],
    })
    sector_labels = assignments["sector"]
    summary = {}
    for method in ("kmeans", "hierarchical"):
        summary[method] = {
            "k": k[method],
            "silhouette": float(selection[(selection["method"] == method)
                                          & (selection["k"] == k[method])]["silhouette"].item()),
            "ari_vs_sector": float(adjusted_rand_score(sector_labels, labels[method])),
            "ari_vs_sector_at_sector_count": float(adjusted_rand_score(sector_labels,
                                                                       at_sector_count[method])),
        }
    summary["kmeans_vs_hierarchical_ari"] = float(adjusted_rand_score(labels["kmeans"],
                                                                      labels["hierarchical"]))
    return {
        "assignments": assignments,
        "features": raw.reset_index(names="ticker"),
        "selection": selection,
        "crosstab": {method: pd.crosstab(assignments["sector"], assignments[f"{method}_cluster"])
                     for method in ("kmeans", "hierarchical")},
        "linkage": hierarchical_linkage(distance),
        "linkage_labels": tickers,
        "stability": stability(returns, market, fundamentals, labels, k, seed),
        "summary": summary,
        "mismatches": {method: describe_mismatches(assignments, method)
                       for method in ("kmeans", "hierarchical")},
        "params": {
            "seed": seed, "features": used, "features_unavailable": unavailable,
            "k_range": [K_RANGE.start, K_RANGE.stop - 1],
            "kmeans_n_init": 20, "linkage": "average", "distance": "1 - correlation",
            "bootstrap_resamples": BOOTSTRAP_RESAMPLES, "bootstrap_block_days": BOOTSTRAP_BLOCK,
            "n_companies": len(tickers), "n_days": int(len(returns)), "n_sectors": int(n_sectors),
            "imputed": [list(pair) for pair in imputed],
            "pca_explained_variance": [float(v) for v in components.explained_variance_ratio_],
        },
    }
