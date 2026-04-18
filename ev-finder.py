import requests
import argparse
import logging
import os
from datetime import datetime, timezone, timedelta
from dotenv import load_dotenv
from tzlocal import get_localzone

# Configuration Constants
MIN_EV_THRESHOLD = 0.000 # 0.5% edge minimum against sharp books

def setup_logging(verbose):
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(level=level, format='%(message)s')

def string_to_est(time_str):
    try:
        dt = datetime.strptime(time_str, "%Y-%m-%dT%H:%M:%SZ")
        dt = dt.replace(tzinfo=timezone.utc)
        est_time = dt.astimezone(get_localzone())
        return est_time
    except Exception as e:
        logging.error(f"Time conversion error: {e}")
        return None

class EVFinder:
    def __init__(self, leagues, markets, odds_variance, api_key, bankroll, sportsbooks=None):
        if isinstance(leagues, str):
            leagues = [leagues]
        self.leagues = [l.lower() for l in leagues]
        if isinstance(markets, str):
            markets = [markets]
        self.markets = [m.lower() for m in markets]
        self.odds_variance = odds_variance
        self.api_key = api_key
        self.bankroll = bankroll
        if sportsbooks is None:
            self.sportsbooks = []
        elif isinstance(sportsbooks, str):
            self.sportsbooks = [sportsbooks.lower()]
        else:
            self.sportsbooks = [s.lower() for s in sportsbooks]
        self.session = requests.Session()

    def fetch_odds(self):
        all_data = []
        bookmakers_str = "pinnacle,betfair_ex_eu,betfair_ex_uk,marathonbet,draftkings,betmgm,fanduel"
        for book in self.sportsbooks:
            if book not in bookmakers_str:
                bookmakers_str += f",{book}"
            
        for league in self.leagues:
            if league == "nba":
                sport_param = "basketball_nba"
            elif league == "mlb":
                sport_param = "baseball_mlb"
            elif league == "nhl":
                sport_param = "icehockey_nhl"
            elif league == "nfl":
                sport_param = "americanfootball_nfl"
            else:
                continue
            
            url = f"https://api.the-odds-api.com/v4/sports/{sport_param}/odds"
            params = {
                "api_key": self.api_key,
                "bookmakers": bookmakers_str,
                "markets": ",".join(self.markets)
            }
            
            logging.debug(f"Fetching Odds API v4 data for {sport_param} - Markets: {','.join(self.markets)}...")
            try:
                response = self.session.get(url, params=params, timeout=15)
                response.raise_for_status()
                data = response.json()
                
                requests_left = response.headers.get("x-requests-remaining", "Unknown")
                logging.debug(f"Odds API Requests Remaining: {requests_left}")

                # Explode the single game response by requested markets so process_games remains relatively unchanged
                for raw_game in data:
                    for mkt in self.markets:
                        game_copy = dict(raw_game)
                        game_copy['league'] = league.upper()
                        game_copy['market'] = mkt
                        all_data.append(game_copy)
                        
            except requests.exceptions.HTTPError as e:
                if e.response is not None and e.response.status_code in [400, 401, 429]:
                    try:
                        error_data = e.response.json()
                        if error_data.get("status") == "EXCEEDED_REQ_LIMIT" or error_data.get("error_code") == "OUT_OF_USAGE_CREDITS":
                            logging.error("CRITICAL: Odds API Quota Exceeded. Halting further API requests.")
                            return all_data
                    except ValueError:
                        pass
                logging.error(f"Error fetching Odds API for {sport_param}: {e}")
            except Exception as e:
                logging.error(f"Error fetching Odds API for {sport_param}: {e}")
        
        return all_data

    def find_best_odds(self, game, market):
        """
        Iterates over the available sportsbooks for a game to find the sharpest reference line,
        and then line-shops across allowed domestic books for the best available odds.
        For spreads and totals, it strictly enforces that the point value matches the sharp line.
        """
        sharp_home = 0.0
        sharp_away = 0.0
        sharp_source = "None"
        sharp_points_home = None
        sharp_points_away = None
        
        best_home_odds = 0.0
        best_away_odds = 0.0
        best_home_site = None
        best_away_site = None
        
        home_team = game.get('home_team')
        away_team = game.get('away_team')
        
        if self.sportsbooks:
            allowed_books = self.sportsbooks
        else:
            allowed_books = ['draftkings', 'betmgm', 'fanduel']

        # 1. Find Sharp Odds First
        for bookmaker in game.get("bookmakers", []):
            try:
                site_key = bookmaker["key"].lower()
                if site_key not in ['pinnacle', 'betfair_ex_eu', 'betfair_ex_uk', 'marathonbet']:
                    continue
                
                for m in bookmaker.get("markets", []):
                    if m["key"] == market:
                        outcomes = m["outcomes"]
                        
                        if market == "totals":
                            home_outcome = next((o for o in outcomes if o["name"] == "Over"), None)
                            away_outcome = next((o for o in outcomes if o["name"] == "Under"), None)
                        else:
                            # Edge case: European books (like Marathonbet) often return 3-way moneylines (Regulation Only) 
                            # under the 'h2h' key, which include a 'Draw' outcome. 
                            # Comparing a 3-way ML against a 2-way ML is mathematically invalid and creates false arbs/EV.
                            if len(outcomes) > 2:
                                continue
                                
                            home_outcome = next((o for o in outcomes if o["name"] == home_team), None)
                            away_outcome = next((o for o in outcomes if o["name"] == away_team), None)
                            
                        if not home_outcome or not away_outcome:
                            continue
                            
                        home_odds_cand = home_outcome["price"]
                        away_odds_cand = away_outcome["price"]
                        home_pts_cand = home_outcome.get("point")
                        away_pts_cand = away_outcome.get("point")
                        
                        if sharp_home == 0.0 or site_key == 'pinnacle':
                            sharp_home = home_odds_cand
                            sharp_away = away_odds_cand
                            sharp_points_home = home_pts_cand
                            sharp_points_away = away_pts_cand
                            sharp_source = site_key.upper()
                        break
            except (KeyError, ValueError):
                continue
                
        # 2. Line Shop Domestic Books
        if sharp_home > 0 and sharp_away > 0:
            for bookmaker in game.get("bookmakers", []):
                try:
                    site_key = bookmaker["key"].lower()
                    if site_key not in allowed_books:
                        continue
                        
                    for m in bookmaker.get("markets", []):
                        if m["key"] == market:
                            outcomes = m["outcomes"]
                            if market == "totals":
                                home_outcome = next((o for o in outcomes if o["name"] == "Over"), None)
                                away_outcome = next((o for o in outcomes if o["name"] == "Under"), None)
                            else:
                                if len(outcomes) > 2:
                                    continue
                                home_outcome = next((o for o in outcomes if o["name"] == home_team), None)
                                away_outcome = next((o for o in outcomes if o["name"] == away_team), None)
                                
                            if not home_outcome or not away_outcome:
                                continue
                                
                            if market in ["spreads", "totals"]:
                                if home_outcome.get("point") != sharp_points_home:
                                    continue
                                    
                            home_odds_cand = home_outcome["price"]
                            away_odds_cand = away_outcome["price"]

                            if home_odds_cand > best_home_odds:
                                best_home_odds = home_odds_cand
                                best_home_site = bookmaker["key"]
                                
                            if away_odds_cand > best_away_odds:
                                best_away_odds = away_odds_cand
                                best_away_site = bookmaker["key"]
                            break
                except (KeyError, ValueError):
                    continue

        return sharp_home, sharp_away, sharp_source, best_home_odds, best_away_odds, best_home_site, best_away_site, sharp_points_home, sharp_points_away

    def calc_fair_prob(self, sharp_home, sharp_away):
        """
        Calculates vig-free (true) probabilities using the multiplicative method.
        Requires decimal odds.
        """
        if sharp_home <= 1.0 or sharp_away <= 1.0:
            return 0.0, 0.0
            
        implied_home_prob = 1 / sharp_home
        implied_away_prob = 1 / sharp_away
        margin = implied_home_prob + implied_away_prob
        
        # Edge case: Avoid division by zero if margin is somehow 0
        if margin == 0:
            return 0.0, 0.0
            
        fair_home = implied_home_prob / margin
        fair_away = implied_away_prob / margin
        return fair_home, fair_away

    def process_games(self):
        games = self.fetch_odds()
        if not games:
            logging.info("No games found or error fetching odds.")
            return

        pick_list_today = []
        pick_list_tomorrow = []
        pick_count_today = 0
        pick_count_tomorrow = 0
        game_num = 0
        current_time = datetime.now(get_localzone())
        tomorrow_date = (current_time + timedelta(days=1)).date()

        for game in games:
            game_num += 1
            
            # Parse and validate the game's start time
            commence_time = game.get('commence_time')
            if not commence_time:
                logging.debug(f'Skipped #{game_num} - Missing commence_time')
                continue
                
            game_time = string_to_est(commence_time)
            if not game_time:
                continue # Skip if time conversion failed
            
            # Safely identify Home and Away teams
            home_team = game.get('home_team')
            away_team = game.get('away_team')
            if not home_team or not away_team:
                logging.debug(f'Skipped #{game_num} - Missing team data')
                continue

            # Exclude live or past games to avoid betting on unavailable lines
            if game_time < current_time:
                if getattr(self, 'verbose', False):
                    logging.debug(f'Skipped #{game_num} - {home_team} vs {away_team} (Game is already live or past)')
                continue
                
            market = game.get("market", "h2h")

            # Extract the sharp reference line and compare against domestic books
            sharp_home, sharp_away, sharp_source, best_home_odds, best_away_odds, h_site, a_site, sharp_points_home, sharp_points_away = self.find_best_odds(game, market)
            
            # Format team names for pick output
            matchup_str = f"{away_team} @ {home_team}"
            if market == "totals":
                home_team_name_pick = f"Over {sharp_points_home}" if sharp_points_home else "Over"
                away_team_name_pick = f"Under {sharp_points_away}" if sharp_points_away else "Under"
            elif market == "spreads":
                home_team_name_pick = f"{home_team} {sharp_points_home}" if sharp_points_home else home_team
                away_team_name_pick = f"{away_team} {sharp_points_away}" if sharp_points_away else away_team
            else:
                home_team_name_pick = home_team
                away_team_name_pick = away_team

            # Require sharp source of truth
            if sharp_home == 0 or sharp_away == 0:
                if getattr(self, 'verbose', False):
                    logging.debug(f'Skipped #{game_num} - {matchup_str} ({market}) (No Sharp odds available)')
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

            league_str = game.get('league', '')
            logging.debug(f'============================================')
            logging.debug(f"#{game_num} [{league_str}] [{market.upper()}] — {game_time.strftime('%m-%d-%Y %I:%M:%S %p')}")
            logging.debug(f"{away_team_name_pick} @ {home_team_name_pick}")
            logging.debug(f"|| Sharp ({sharp_source}): \t{sharp_away} / {sharp_home}")
            logging.debug(f"|| True Prob: \t{round(fair_away_prob*100, 2)}%\t/ {round(fair_home_prob*100, 2)}%")
            logging.debug(f"|| Best Line: \t{best_away_odds} ({a_site})\t/ {best_home_odds} ({h_site})")
            logging.debug(f"|| EV ($1):   \t${round(away_ev, 3)}\t\t/ ${round(home_ev, 3)}")

            pick_formatted = f'[{league_str}] [{market.upper()}] {away_team_name_pick} ({best_away_odds}) / {home_team_name_pick} ({best_home_odds}) [{matchup_str}]'

            # Evaluate picks against Edge Threshold
            if away_ev > MIN_EV_THRESHOLD:
                kelly_frac = ( (best_away_odds - 1) * fair_away_prob - (1 - fair_away_prob) ) / (best_away_odds - 1)
                kelly_pct = max(0, round(kelly_frac * 100, 2))
                s_kelly = round(kelly_pct * 1.00, 2) # 0.25-1 Fractional Kelly
                
                if game_time:
                    pick_data = {
                        'league': league_str,
                        'market': market,
                        'team': away_team_name_pick.upper(),
                        'ev': away_ev,
                        'book': a_site.upper(),
                        'odds': best_away_odds,
                        'sharp_odds': sharp_away,
                        'sharp_source': sharp_source,
                        's_kelly': s_kelly,
                        'pick_formatted': pick_formatted,
                        'game_num': game_num,
                        'game_time': game_time
                    }
                    if game_time.date() == current_time.date():
                        pick_list_today.append(pick_data)
                        pick_count_today += 1
                    elif game_time.date() == tomorrow_date:
                        pick_list_tomorrow.append(pick_data)
                        pick_count_tomorrow += 1
                
            if home_ev > MIN_EV_THRESHOLD:
                kelly_frac = ( (best_home_odds - 1) * fair_home_prob - (1 - fair_home_prob) ) / (best_home_odds - 1)
                kelly_pct = max(0, round(kelly_frac * 100, 2))
                s_kelly = round(kelly_pct * 0.25, 2)
                
                if game_time:
                    pick_data = {
                        'league': league_str,
                        'market': market,
                        'team': home_team_name_pick.upper(),
                        'ev': home_ev,
                        'book': h_site.upper(),
                        'odds': best_home_odds,
                        'sharp_odds': sharp_home,
                        'sharp_source': sharp_source,
                        's_kelly': s_kelly,
                        'pick_formatted': pick_formatted,
                        'game_num': game_num,
                        'game_time': game_time
                    }
                    if game_time.date() == current_time.date():
                        pick_list_today.append(pick_data)
                        pick_count_today += 1
                    elif game_time.date() == tomorrow_date:
                        pick_list_tomorrow.append(pick_data)
                        pick_count_tomorrow += 1

            logging.debug(f'============================================\n')

        # Output Recommendations
        current_bankroll = self.bankroll
        
        # Today's Picks
        logging.info(f'[{current_time.strftime("%m-%d-%Y")}] Today\'s Recommended Top-Down Picks ({pick_count_today}):')
        logging.info(f'============================================\n')
        
        pick_list_today.sort(key=lambda x: x['ev'], reverse=True)
        
        if not pick_list_today:
            logging.info("No recommended +EV picks found for today's match-ups.")
            logging.info('')
            
        for p in pick_list_today:
            bet_amt = current_bankroll * (p['s_kelly'] / 100.0)
            current_bankroll -= bet_amt
            
            pick_str = f"[{p.get('league', '')}] [{p['team']}] [EV: +{round(p['ev']*100, 2)}%] [Book: {p['book']}] [Odds: {p['odds']}] [Sharp: {p['sharp_odds']} ({p['sharp_source']})] [Rec Stake: {p['s_kelly']}% BNK / ${round(bet_amt, 2)}]"
            full_str = f"#{p['game_num']}\t{p['game_time'].strftime('%I:%M:%S %p')} - {pick_str}\n\t{p['pick_formatted']}"
            
            logging.info(full_str)
            logging.info('')

        # Tomorrow's Picks
        logging.info(f'[{tomorrow_date.strftime("%m-%d-%Y")}] Tomorrow\'s Recommended Top-Down Picks ({pick_count_tomorrow}):')
        logging.info(f'============================================\n')
        
        pick_list_tomorrow.sort(key=lambda x: x['ev'], reverse=True)
        
        if not pick_list_tomorrow:
            logging.info("No recommended +EV picks found for tomorrow's match-ups.")
            logging.info('')
            
        for p in pick_list_tomorrow:
            bet_amt = current_bankroll * (p['s_kelly'] / 100.0)
            current_bankroll -= bet_amt
            
            pick_str = f"[{p.get('league', '')}] [{p['team']}] [EV: +{round(p['ev']*100, 2)}%] [Book: {p['book']}] [Odds: {p['odds']}] [Sharp: {p['sharp_odds']} ({p['sharp_source']})] [Rec Stake: {p['s_kelly']}% BNK / ${round(bet_amt, 2)}]"
            full_str = f"#{p['game_num']}\t{p['game_time'].strftime('%I:%M:%S %p')} - {pick_str}\n\t{p['pick_formatted']}"
            
            logging.info(full_str)
            logging.info('')

def main():
    parser = argparse.ArgumentParser(description="EV Finder - Pinnacle Top-Down Devigging Strategy")
    parser.add_argument('--league', type=str, nargs='+', default=['nba'], choices=['nba', 'mlb', 'nhl', 'nfl'], help='League(s) to analyze (e.g. --league nba nhl)')
    parser.add_argument('--market', type=str, nargs='+', default=['h2h', 'spreads', 'totals'], choices=['h2h', 'spreads', 'totals'], help='Market(s) to analyze (e.g. --market h2h spreads totals)')
    parser.add_argument('--variance', type=float, default=0.005, help='Odds variance modifier (no longer heavily used in devigging but preserved for extension)')
    parser.add_argument('--bankroll', type=float, default=100.00, help='Total bankroll available for Kelly sizing (default 100.00)')
    parser.add_argument('--book', type=str, nargs='+', default=None, help='Target specific sportsbook(s) (e.g. --book fanduel betmgm draftkings)')
    parser.add_argument('--verbose', action='store_true', help='Enable verbose debug logging')
    args = parser.parse_args()

    setup_logging(args.verbose)
    load_dotenv()
    
    api_key = os.getenv('API_KEY')
    if not api_key:
        logging.error("ERROR: API_KEY not found in environment variables or .env file.")
        return

    leagues_str = ", ".join(args.league).upper()
    markets_str = ", ".join(args.market).upper()
    logging.info(f"Initializing Top-Down EV Finder for {leagues_str} [{markets_str}]...")
    finder = EVFinder(leagues=args.league, markets=args.market, odds_variance=args.variance, api_key=api_key, bankroll=args.bankroll, sportsbooks=args.book)
    finder.process_games()

if __name__ == "__main__":
    main()