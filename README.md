# EV Finder

> [!IMPORTANT]
> **This script was primarily created and optimized for binary moneyline (head-to-head) odds.**

EV Finder is a top-down expected value (EV) betting tool that uses market-sharp odds (like Pinnacle and Betfair) to calculate true probabilities with the vig removed via a multiplicative method. It then line-shops across domestic sportsbooks to find +EV (positive expected value) bets and recommends a fractional Kelly unit size.

## Features

- Works across multiple leagues: NBA, MLB, NHL, and NFL.
- Allows running multiple leagues simultaneously.
- Pulls live odds from The-Odds-API.
- Identifies sharp books to determine true probabilities.
- Compares those probabilities against domestic US sportsbooks (e.g., DraftKings, FanDuel, BetMGM).
- Calculates Recommended Stake Size based on a 0.25 Fractional Kelly Criterion.

## Requirements

1. Python 3.7+
2. The Odds API key. You can get one at [the-odds-api.com](https://the-odds-api.com/).

### Installation

Install the required Python packages using pip:
```bash
pip install requests tzlocal python-dotenv
```

### Environment Variables

The script expects an `API_KEY` for The Odds API. You can pass it by using a `.env` file in the root directory:

```env
API_KEY=your_odds_api_key_here
```

## Usage

Run the script from the command line:

```bash
python ev-finder.py --league nba nhl
```

### Command Line Arguments

- `--league`: One or more leagues to analyze (e.g. `--league nba nhl mlb nfl`). Default is `nba`.
- `--bankroll`: The total current bankroll available for Kelly sizing. Default is `100.00`.
- `--book`: Target a specific sportsbook (e.g. `fanduel`, `betmgm`, `draftkings`). If not passed, it will check the default allowed domestic books.
- `--variance`: Odds variance modifier. Default is `0.005` (0.05% edge minimum).
- `--verbose`: Enable detailed verbose debug logging to view step-by-step logic, sharp lines versus US lines, and game skipping logic.

## Output Example

```text
Initializing Top-Down EV Finder for NBA, NHL...
[04-12-2026] Today's Recommended Top-Down Picks (2):
============================================

#1  07:30:00 PM - [BOS Celtics] [EV: +2.15%] [Book: FANDUEL] [Odds: 2.10] [Rec Stake: 1.25% BNK / $1.25]
    [NBA] BOS Celtics (2.10) @ NY Knicks (1.80)

#2  08:00:00 PM - [TOR Maple Leafs] [EV: +1.85%] [Book: DRAFTKINGS] [Odds: 1.95] [Rec Stake: 0.90% BNK / $0.90]
    [NHL] TOR Maple Leafs (1.95) @ BOS Bruins (1.85)

```

### Output Breakdown

Each recommendation provides the following details:

* **Game Time**: Local time the game is scheduled to commence.
* **Team**: The team that has the mathematical edge (+EV) identified.
* **EV (+X.XX%)**: The expected value, representing your mathematical long-term profit margin over the true probability (sharp odds with vig removed).
* **Book**: The domestic sportsbook offering the best odds.
* **Odds**: The decimal odds being offered at the recommended book.
* **Rec Stake**: Recommended bet size based on a 0.25 Fractional Kelly Criterion. Provided as a percentage of your total bankroll (`% BNK`) and the actual cash amount (`$`).
* **Match-up Details**: The second line provides the league (e.g., `[NBA]`), away team, home team, and the best available odds for each side to give full context.
