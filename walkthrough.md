# EV Finder Upgrade: Top-Down Market Devigging

Based on your approval, the script has been completely stripped down and rebuilt around the professional **Top-Down Betting** methodology using sharp sportsbooks!

## Key Strategy Upgrades

### 1. Removing Predictive Guesswork & Pandas
The script no longer relies on `TeamRankings.com` static projections, so we've entirely deleted `Pandas` and `BeautifulSoup` from the logic. The algorithm will start up faster and parse data almost instantaneously!

### 2. Sourcing Sharp Market Probabilities
Because your Odds API tier limits access to Pinnacle itself, the script dynamically adapts! It aggressively searches the global API lines (`EU` region) to locate **Betfair Exchange** or **Marathonbet**, which are broadly considered the sharpest available European syndicates.
If found, the script uses their exact lines to calculate your True Probability benchmark!

### 3. Calculating the "Vig-Free" True Edge
The script now relies on a mathematically airtight formula:
1. It grabs the Sharp book odds for both sides of the game.
2. It detects the invisible "Vig" (usually ~4-5%) the sharp book baked into the line.
3. It removes the house edge utilizing the Multiplicative Devigging Method to compute the precise real-world percentage chance of winning.
4. Finally, it shops against FanDuel, DraftKings, and BetMGM. If the domestic US book is making a pricing mistake against that true devigged probability, we smash the bet!

## Usage Remains Exactly The Same

You can continue testing and executing the script precisely as before:
```bash
python ev-finder.py --verbose --league nba --bankroll 100
```
