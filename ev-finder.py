import requests
import argparse
import logging
import os
from datetime import datetime, timezone
from dotenv import load_dotenv
from tzlocal import get_localzone

# Configuration Constants
MIN_EV_THRESHOLD = 0.015 # 1.5% edge minimum against sharp books

def setup_logging(verbose):
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(level=level, format='%(message)s')

def epoch_to_est(epoch_time):
    try:
        est_time = datetime.fromtimestamp(epoch_time, tz=timezone.utc).astimezone(get_localzone())
        return est_time
    except Exception as e:
        logging.error(f"Time conversion error: {e}")
        return None

class EVFinder:
    def __init__(self, league, odds_variance, api_key, bankroll, sportsbook):
        self.league = league.lower()
        self.odds_variance = odds_variance
        self.api_key = api_key
        self.bankroll = bankroll
        self.sportsbook = sportsbook
        self.session = requests.Session()

    def fetch_odds(self):
        url = "https://api.the-odds-api.com/v3/odds"
        sport_param = "basketball_nba" if self.league == "nba" else "baseball_mlb"
        
        param_us = {"api_key": self.api_key, "sport": sport_param, "region": "us", "mkt": "h2h"}
        param_eu = {"api_key": self.api_key, "sport": sport_param, "region": "eu", "mkt": "h2h"}
        
        logging.debug(f"Fetching Odds API data for {sport_param} (US and EU)...")
        try:
            response_us = self.session.get(url, params=param_us, timeout=15)
            response_us.raise_for_status()
            data_us = response_us.json().get("data", [])
            
            response_eu = self.session.get(url, params=param_eu, timeout=15)
            data_eu = response_eu.json().get("data", [])
            requests_left = response_eu.headers.get("x-requests-remaining", "Unknown")
            logging.debug(f"Odds API Requests Remaining: {requests_left}")

            # Merge EU sites (like Pinnacle) into US game objects
            eu_dict = {g['id']: g for g in data_eu if 'id' in g}
            for game in data_us:
                if game['id'] in eu_dict:
                    game.setdefault('sites', []).extend(eu_dict[game['id']].get('sites', []))

            return data_us
        except Exception as e:
            logging.error(f"Error fetching Odds API: {e}")
            return []

    def find_best_odds(self, game, home_index):
        # sharp book tracking
        sharp_home = 0.0
        sharp_away = 0.0
        sharp_source = "None"
        
        # targeted line shopping tracking
        best_home_odds = 0.0
        best_away_odds = 0.0
        best_home_site = None
        best_away_site = None
        
        if self.sportsbook:
            allowed_books = [self.sportsbook.lower()]
        else:
            allowed_books = ['draftkings', 'betmgm', 'fanduel']

        for site in game.get("sites", []):
            try:
                site_key = site["site_key"].lower()
                h2h = site["odds"]["h2h"]
                home_odds_cand = h2h[home_index]
                away_odds_cand = h2h[not home_index]
                
                # capture sharp lines with fallback
                if site_key in ['pinnacle', 'betfair_ex_eu', 'betfair_ex_uk', 'marathonbet']:
                    if sharp_home == 0.0 or site_key == 'pinnacle':
                        sharp_home = home_odds_cand
                        sharp_away = away_odds_cand
                        sharp_source = site_key.upper()
                    
                # line shop if book is allowed
                if site_key in allowed_books:
                    if home_odds_cand > best_home_odds:
                        best_home_odds = home_odds_cand
                        best_home_site = site["site_key"]
                        
                    if away_odds_cand > best_away_odds:
                        best_away_odds = away_odds_cand
                        best_away_site = site["site_key"]
            except KeyError:
                continue

        return sharp_home, sharp_away, sharp_source, best_home_odds, best_away_odds, best_home_site, best_away_site

    def calc_fair_prob(self, sharp_home, sharp_away):
        # Calculate vig-free probability via multiplicative method
        if sharp_home <= 1.0 or sharp_away <= 1.0:
            return 0.0, 0.0
        implied_home_prob = 1 / sharp_home
        implied_away_prob = 1 / sharp_away
        margin = implied_home_prob + implied_away_prob
        
        fair_home = implied_home_prob / margin
        fair_away = implied_away_prob / margin
        return fair_home, fair_away

    def process_games(self):
        games = self.fetch_odds()
        if not games:
            logging.info("No games found or error fetching odds.")
            return

        pick_list = []
        pick_count = 0
        game_num = 0

        for game in games:
            game_num += 1
            game_time = epoch_to_est(game['commence_time'])
            
            # Identify Home/Away
            home_team_name = game['home_team']
            home_index = 1 if home_team_name in game["teams"][1] else 0
            
            home_team = game["teams"][home_index]
            away_team = game["teams"][not home_index]

            sharp_home, sharp_away, sharp_source, best_home_odds, best_away_odds, h_site, a_site = self.find_best_odds(game, home_index)
            
            # Require sharp source of truth
            if sharp_home == 0 or sharp_away == 0:
                if getattr(self, 'verbose', False):
                    logging.debug(f'Skipped #{game_num} - {away_team} @ {home_team} (No Sharp odds available)')
                continue
            # Require domestic odds to line shop against
            if best_home_odds == 0 or best_away_odds == 0:
                 continue

            fair_home_prob, fair_away_prob = self.calc_fair_prob(sharp_home, sharp_away)

            # EV calculation (1 unit bet)
            # EV = (Win Prob * Profit) - (Loss Prob * Bet)
            home_profit = best_home_odds - 1
            home_ev = (fair_home_prob * home_profit) - ((1 - fair_home_prob) * 1)
            
            away_profit = best_away_odds - 1
            away_ev = (fair_away_prob * away_profit) - ((1 - fair_away_prob) * 1)

            logging.debug(f'============================================')
            logging.debug(f"#{game_num} — {game_time.strftime('%m-%d-%Y %I:%M:%S %p')}")
            logging.debug(f"{away_team} @ {home_team}")
            logging.debug(f"|| Sharp ({sharp_source}): \t{sharp_away} / {sharp_home}")
            logging.debug(f"|| True Prob: \t{round(fair_away_prob*100, 2)}%\t/ {round(fair_home_prob*100, 2)}%")
            logging.debug(f"|| Best Line: \t{best_away_odds} ({a_site})\t/ {best_home_odds} ({h_site})")
            logging.debug(f"|| EV ($1):   \t${round(away_ev, 3)}\t\t/ ${round(home_ev, 3)}")

            pick_formatted = f'{away_team} ({best_away_odds}) @ {home_team} ({best_home_odds})'

            # Evaluate picks against Edge Threshold
            if away_ev > MIN_EV_THRESHOLD:
                kelly_frac = ( (best_away_odds - 1) * fair_away_prob - (1 - fair_away_prob) ) / (best_away_odds - 1)
                kelly_pct = max(0, round(kelly_frac * 100, 2))
                s_kelly = round(kelly_pct * 0.25, 2) # 0.25 Fractional Kelly
                
                if game_time and game_time.day == datetime.today().day:
                    pick_list.append({
                        'team': away_team.upper(),
                        'ev': away_ev,
                        'book': a_site.upper(),
                        'odds': best_away_odds,
                        's_kelly': s_kelly,
                        'pick_formatted': pick_formatted,
                        'game_num': game_num,
                        'game_time': game_time
                    })
                    pick_count += 1
                
            if home_ev > MIN_EV_THRESHOLD:
                kelly_frac = ( (best_home_odds - 1) * fair_home_prob - (1 - fair_home_prob) ) / (best_home_odds - 1)
                kelly_pct = max(0, round(kelly_frac * 100, 2))
                s_kelly = round(kelly_pct * 0.25, 2)
                
                if game_time and game_time.day == datetime.today().day:
                    pick_list.append({
                        'team': home_team.upper(),
                        'ev': home_ev,
                        'book': h_site.upper(),
                        'odds': best_home_odds,
                        's_kelly': s_kelly,
                        'pick_formatted': pick_formatted,
                        'game_num': game_num,
                        'game_time': game_time
                    })
                    pick_count += 1

            logging.debug(f'============================================\n')

        # Output Recommendations
        logging.info(f'[{datetime.today().strftime("%m-%d-%Y %I:%M:%S %p")}] Today\'s Recommended Top-Down Picks ({pick_count}):')
        logging.info(f'============================================\n')
        
        pick_list.sort(key=lambda x: x['ev'], reverse=True)
        current_bankroll = self.bankroll
        
        if not pick_list:
            logging.info("No recommended +EV picks found for today's match-ups.")
            logging.info('')
            
        for p in pick_list:
            bet_amt = current_bankroll * (p['s_kelly'] / 100.0)
            current_bankroll -= bet_amt
            
            pick_str = f"[{p['team']}] [EV: +{round(p['ev']*100, 2)}%] [Book: {p['book']}] [Odds: {p['odds']}] [Rec Stake: {p['s_kelly']}% BNK / ${round(bet_amt, 2)}]"
            full_str = f"#{p['game_num']}\t{p['game_time'].strftime('%I:%M:%S %p')} - {pick_str}\n\t{p['pick_formatted']}"
            
            logging.info(full_str)
            logging.info('')

def main():
    parser = argparse.ArgumentParser(description="EV Finder - Pinnacle Top-Down Devigging Strategy")
    parser.add_argument('--league', type=str, default='nba', choices=['nba', 'mlb'], help='League to analyze (nba or mlb)')
    parser.add_argument('--variance', type=float, default=0.05, help='Odds variance modifier (no longer heavily used in devigging but preserved for extension)')
    parser.add_argument('--bankroll', type=float, default=5.00, help='Total bankroll available for Kelly sizing (default 5.00)')
    parser.add_argument('--book', type=str, default=None, help='Target a specific sportsbook (e.g. fanduel, betmgm, draftkings)')
    parser.add_argument('--verbose', action='store_true', help='Enable verbose debug logging')
    args = parser.parse_args()

    setup_logging(args.verbose)
    load_dotenv()
    
    api_key = os.getenv('API_KEY')
    if not api_key:
        logging.error("ERROR: API_KEY not found in environment variables or .env file.")
        return

    logging.info(f"Initializing Top-Down EV Finder for {args.league.upper()}...")
    finder = EVFinder(league=args.league, odds_variance=args.variance, api_key=api_key, bankroll=args.bankroll, sportsbook=args.book)
    finder.process_games()

if __name__ == "__main__":
    main()