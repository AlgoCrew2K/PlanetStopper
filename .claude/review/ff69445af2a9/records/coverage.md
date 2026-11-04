# Coverage record: C2 batch A (RED ff69445af2a9)

| S id | Test (file::name) |
|---|---|
| S-1.2 | test_target_session::test_weekend_run_targets_the_first_trading_day_after_the_weekend, ::test_weekend_before_a_monday_holiday_skips_the_holiday |
| S-1.3 | test_target_session::test_night_before_a_weekday_holiday_targets_the_session_after_it |
| S-1.4 | test_target_session::test_04_30_utc_run_targets_the_trading_day_of_the_utc_date_in_both_offsets; test_sip_fetch::test_job_requests_start_from_the_window_of_the_session_derived_from_its_start_instant |
| S-1.5 | test_target_session::test_run_at_ten_et_on_a_trading_day_targets_the_next_trading_day, ::test_the_open_boundary_is_strict, hypothesis ::test_target_is_the_earliest_trading_day_opening_after_the_instant |
| S-2.1 | test_label_computation::test_series_equals_weighted_close_to_close_returns_of_the_captured_sip_closes, ::test_series_from_a_fetched_captured_page_equals_the_independent_computation |
| S-2.2 | test_label_computation::test_golden_price_history_gets_the_fixtures_own_expected_label, ::test_the_three_golden_histories_give_three_different_labels |
| S-2.3 | test_label_computation::test_two_percent_daily_std_is_labelled_with_fractions_not_percent |
| S-2.4 | test_label_computation::test_bars_dated_on_or_after_the_target_session_never_enter_the_series, ::test_the_label_is_independent_of_any_bar_dated_on_or_after_the_target; test_sip_fetch::test_bars_dated_d_and_after_returned_by_the_source_never_enter_the_stored_label, ::test_a_partial_bar_for_a_session_still_in_progress_is_never_used |
| S-2.5 | test_label_computation::test_with_25_computable_sessions_the_series_has_exactly_the_20_most_recent, ::test_changing_the_four_oldest_closes_leaves_series_and_label_unchanged |
| S-2.6 | test_label_computation::test_each_session_return_is_the_allocation_weighted_sum_of_close_to_close_returns |
| S-2.7 | test_label_computation::test_stored_holdings_price_every_session_even_when_an_earlier_set_differed |
| S-2.8 | test_label_computation::test_a_holding_with_no_bar_in_a_successful_response_leaves_no_computable_session; test_sip_fetch::test_a_200_that_omits_a_requested_ticker_gives_it_no_bars_and_no_failure |
| S-2.9 | test_replay_parity::test_replay_resolution_for_every_date_is_unchanged_by_a_job_run_in_the_same_process |
| S-2.10 | test_label_computation::test_a_holding_missing_one_session_is_left_out_and_the_window_reaches_back |
| S-2.11 | test_label_computation::test_nineteen_computable_sessions_in_thirty_give_no_label_even_with_more_further_back, ::test_a_gap_older_than_thirty_sessions_does_not_matter |
| S-2.12 | test_replay_parity::test_replay_trailing_returns_equal_the_jobs_series_where_the_day_values_coincide, ::test_the_jobs_label_maps_to_the_replays_resolved_tick_count, ::test_the_stored_row_of_a_real_job_run_maps_to_the_replays_resolved_tick_count |
| S-5.2 | test_label_computation::test_nineteen_computable_sessions_in_thirty_give_no_label_even_with_more_further_back, ::test_twenty_computable_sessions_in_thirty_give_a_label |
| S-5.5 | test_sip_fetch::test_thirty_one_tickers_are_requested_in_two_batches_of_at_most_the_batch_size, ::test_second_batch_failing_after_its_attempts_marks_only_its_tickers, ::test_a_later_page_failing_fails_the_whole_batch_whatever_page_one_returned |
| S-5.6 | test_label_computation::test_a_bad_close_inside_the_window_gives_no_label_and_no_classifier_reason |
| S-5.10 | test_label_computation::test_no_prices_for_any_holding_gives_no_label |
| S-5.11 | test_label_computation::test_every_failure_leaves_no_row_and_only_valid_labels_are_ever_stored |
| S-5.12 | test_sip_fetch::test_the_captured_recent_sip_refusal_fails_the_batch_with_status_403_only, ::test_any_other_non_200_status_fails_at_once_without_a_retry |
| DC-2 | S-2.8, S-2.10, S-5.10, S-5.11 tests above |
| DC-3 | test_target_session (all) |
| DC-4 | S-2.3 test |
| DC-5 | S-2.4 tests |
| DC-12 | S-2.1, S-2.6, S-2.10, S-2.11 tests; request params feed/timeframe in test_sip_fetch::test_request_is_a_get_of_sip_daily_split_adjusted_bars |
