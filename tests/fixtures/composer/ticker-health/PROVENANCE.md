# Composer responses for the ticker check, real responses

Host `https://api.composer.trade/api/v0.1`, read-only GETs, captured by the PM on the droplet's credentials on
2026-11-16 at about 16:30 UTC.

| File | Request | Notes |
|---|---|---|
| score_8FAXAnQmYi1INDubazeC.json | GET /symphonies/8FAXAnQmYi1INDubazeC/score | body verbatim. 25 asset nodes with ticker SHNY and one if-child whose lhs-val and rhs-val are SHNY. |
| symphony_stats_meta_holdings_kept.json | GET /portfolio/accounts/{account}/symphony-stats-meta | 11 symphonies. TRIMMED: the account id is not recorded; each symphony keeps only `id`, `name`, `asset_class`, `asset_classes`, `holdings`, `is_paused`, `rebalance_frequency`, `last_rebalance_on`, `last_rebalance_attempted_on`; each holding keeps only `ticker` and `allocation`. Dropped symphony keys: `annualized_rate_of_return`, `as_of`, `calmar_ratio`, `cash`, `color`, `community_review_status`, `deposit_adjusted_value`, `description`, `invested_since`, `is_shared`, `last_dollar_change`, `last_percent_change`, `last_semantic_update_at`, `max_drawdown`, `may_rebalance_today`, `net_deposits`, `next_rebalance_on`, `node_count`, `paused_by`, `paused_reason`, `position_id`, `sharpe_ratio`, `simple_return`, `skip_rebalance_today`, `standard_deviation`, `tags`, `tickers`, `time_weighted_return`, `trailing_1m_return`, `trailing_3m_return`, `value`. |
