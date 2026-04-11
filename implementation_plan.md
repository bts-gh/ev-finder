# Top-Down Pinnacle Strategy Implementation

This plan outlines the complete removal of the predictive TeamRankings model, transitioning the script to a mathematically rigorous Top-Down strategy built entirely on sharp market devigging.

## Proposed Strategy Shift

1.  **Remove TeamRankings Scraping:** The script will no longer scrape TeamRankings for historical trends or implied forward projections. We will completely gut the `BeautifulSoup` and HTML parsing segments, dramatically accelerating execution speed.
2.  **Remove Pandas Dependency:** Since we no longer need to intersect and scan multiple scraped stat tables, the `pandas` dependency can be removed entirely, massively speeding up the boot time of the script.
3.  **Pinnacle Market Targeting:** For every game fetched from the Odds API, the algorithm will specifically seek out the odds posted by **Pinnacle** (the universal benchmark for sharp odds). If Pinnacle does not have odds posted for a game, the game will be safely skipped.
4.  **Mathematical Devigging (Fair Probability):**
    *   The script will calculate Pinnacle's implied win probability for both the Home and Away side.
    *   Because sportsbooks bake a "Vig" (tax) into their lines, Pinnacle's implied probabilities will sum to more than 100% (usually ~104%).
    *   We will apply a normalized devigging formula to strip out Pinnacle's margin, resulting in a **True Fair Probability** that perfectly perfectly sums to 100%. This is the exact probability of the event occurring according to the sharpest betting syndicate in the world.
5.  **Line Shopping & +EV Validation:** 
    *   We will retain the line shopping functions for our targeted domestic books (DraftKings, FanDuel, BetMGM, or the specific `--book` argument).
    *   Using replacing the old weighted system with our new "True Fair Probability", we will compute the mathematically correct Expected Value (EV%) against the best lines available.
    *   The rest of the Kelly Criterion output logic will remain untouched, utilizing the new, far more accurate win probabilities.

## Proposed Code Changes

- **[DELETE]** `fetch_teamrankings_data`, `get_implied_breakeven`, `get_historical_winrate` methods.
- **[DELETE]** BeautifulSoup and pandas imports.
- **[MODIFY]** `process_games` to query the internal Odds API dictionary for `pinnacle` object instead of accessing the dataframes.
- **[NEW]** `calculate_fair_probability` function that accepts pinnacle odds and returns the devigged true probability using the margin normalization method.

## Open Questions

- **Odds API Tier:** Just verifying, does your current tier of the Odds API key grant you access to Pinnacle odds? (It should, as long as it's included in the default `us` region or if we need to switch the region parameter to `all`/`eu` just to grab the pinnacle lines while retaining the US books). Let me know if you are aware of any limitations on your key!

## Verification Plan
1. Check that the script executes without pandas or bs4 installed.
2. Watch execution logs to ensure Pinnacle's vig is being calculated (e.g. `Implied sum: 1.043 -> Fair Prob: 45.2% vs 54.8%`).
3. Ensure no deprecated TeamRankings logs or calls fire.
