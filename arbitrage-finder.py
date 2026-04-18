import requests
import argparse
import logging
import os
from datetime import datetime, timezone, timedelta
from dotenv import load_dotenv
from tzlocal import get_localzone

MIN_ARB_THRESHOLD = 0.000

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

class ArbitrageFinder:
    def __init__(self, leagues, markets, api_key, sportsbooks=None):
        if isinstance(leagues, str):
            leagues = [leagues]
        self.leagues = [l.lower() for l in leagues]
        if isinstance(markets, str):
            markets = [markets]
        self.markets = [m.lower() for m in markets]
        self.api_key = api_key
        if sportsbooks is None:
            self.sportsbooks = []
        elif isinstance(sportsbooks, str):
            self.sportsbooks = [sportsbooks.lower()]
        else:
            self.sportsbooks = [s.lower() for s in sportsbooks]
        self.session = requests.Session()

    def fetch_odds(self):
        all_data = []
        bookmakers_str = "draftkings,betmgm,fanduel,pinnacle,marathonbet"
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

    def find_arbitrage_odds(self, game, market):
        """
        Scans all permitted sportsbooks to find the absolute maximum odds available 
        for both the Home/Over side and Away/Under side of a specific market.
        
        Edge Case Handling:
        - Points Matching: For Spreads and Totals, it groups offerings strictly by their exact 
          point value. This prevents falsely identifying an arbitrage between mismatched lines 
          (e.g. Over 215.5 on Book A and Under 216.5 on Book B).
        - Missing Data: Skips books missing price or point data.
        """
        best_home_odds = 0.0
        best_away_odds = 0.0
        best_home_site = None
        best_away_site = None
        best_points_home = None
        best_points_away = None
        
        home_team = game.get('home_team')
        away_team = game.get('away_team')
        
        if self.sportsbooks:
            allowed_books = self.sportsbooks
        else:
            allowed_books = ['draftkings', 'betmgm', 'fanduel', 'pinnacle', 'marathonbet']

        # Group offerings by point value to ensure we only arb identical lines
        offerings_by_point = {}
        
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
                            # Edge case: European books (like Marathonbet) often return 3-way moneylines (Regulation Only) 
                            # under the 'h2h' key, which include a 'Draw' outcome. 
                            # Comparing a 3-way ML against a 2-way ML is mathematically invalid and creates false arbs/EV.
                            if len(outcomes) > 2:
                                continue
                                
                            home_outcome = next((o for o in outcomes if o["name"] == home_team), None)
                            away_outcome = next((o for o in outcomes if o["name"] == away_team), None)
                            
                        if not home_outcome or not away_outcome:
                            continue
                            
                        home_price = home_outcome.get("price")
                        away_price = away_outcome.get("price")
                        
                        # Edge case: Ensure valid pricing exists (odds > 1.0)
                        if not home_price or not away_price or home_price <= 1.0 or away_price <= 1.0:
                            continue
                        
                        if market in ["spreads", "totals"]:
                            point = home_outcome.get("point")
                            away_pt = away_outcome.get("point")
                            
                            # Edge case: A point spread/total without a point value cannot be safely arbed
                            if point is None or away_pt is None:
                                continue
                                
                            point_tuple = (point, away_pt)
                        else:
                            # H2H does not utilize points
                            point_tuple = (None, None)
                            
                        if point_tuple not in offerings_by_point:
                            offerings_by_point[point_tuple] = {
                                "home": {"price": 0.0, "site": None},
                                "away": {"price": 0.0, "site": None}
                            }
                        if home_price > offerings_by_point[point_tuple]["home"]["price"]:
                            offerings_by_point[point_tuple]["home"] = {"price": home_price, "site": site_key}
                        if away_price > offerings_by_point[point_tuple]["away"]["price"]:
                            offerings_by_point[point_tuple]["away"] = {"price": away_price, "site": site_key}
                        break
            except (KeyError, ValueError):
                continue
                
        best_margin = 100.0
        for pt_tuple, offer in offerings_by_point.items():
            h_price = offer["home"]["price"]
            a_price = offer["away"]["price"]
            
            if h_price > 0 and a_price > 0:
                margin = (1 / h_price) + (1 / a_price)
                if margin < best_margin:
                    best_margin = margin
                    best_home_odds = h_price
                    best_away_odds = a_price
                    best_home_site = offer["home"]["site"]
                    best_away_site = offer["away"]["site"]
                    best_points_home = pt_tuple[0]
                    best_points_away = pt_tuple[1]

        return best_home_odds, best_away_odds, best_home_site, best_away_site, best_points_home, best_points_away, best_margin

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
            commence_time = game.get('commence_time')
            if not commence_time:
                continue
                
            game_time = string_to_est(commence_time)
            if not game_time:
                continue
            
            home_team = game.get('home_team')
            away_team = game.get('away_team')
            if not home_team or not away_team:
                continue

            if game_time < current_time:
                continue
                
            market = game.get("market", "h2h")

            best_home_odds, best_away_odds, h_site, a_site, sharp_points_home, sharp_points_away, margin = self.find_arbitrage_odds(game, market)
            
            # Edge case: Ensure valid odds were found for both sides of the market
            if best_home_odds == 0 or best_away_odds == 0:
                continue
                
            # Edge case: Arbitrage margin must be strictly less than 1.0 (or whatever threshold) to guarantee profit
            if margin >= 1.0 - MIN_ARB_THRESHOLD:
                continue
                
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

            # Calculate exact guaranteed profit margin percentage
            profit_margin = 1.0 / margin - 1.0

            # Wagering Strategy (Arbitrage)
            # Standardize on a $100 total investment across both legs to easily demonstrate proportional split
            total_stake = 100.0
            
            # Edge case: Safely handle stakes to avoid ZeroDivisionError (though checked earlier via margin check)
            home_stake = total_stake * ((1/best_home_odds) / margin)
            away_stake = total_stake * ((1/best_away_odds) / margin)
            guaranteed_payout = home_stake * best_home_odds
            guaranteed_profit = guaranteed_payout - total_stake

            league_str = game.get('league', '')
            logging.debug(f'============================================')
            logging.debug(f"#{game_num} [{league_str}] [{market.upper()}] - {game_time.strftime('%m-%d-%Y %I:%M:%S %p')}")
            logging.debug(f"{away_team_name_pick} @ {home_team_name_pick}")
            logging.debug(f"|| ARB Margin: \t{round((1.0 - margin)*100, 2)}%")
            logging.debug(f"|| Best Line: \t{best_away_odds} ({a_site})\t/ {best_home_odds} ({h_site})")
            logging.debug(f"|| Profit($100):\t+${round(guaranteed_profit, 2)}")

            pick_data = {
                'league': league_str,
                'market': market,
                'team_home': home_team_name_pick.upper(),
                'team_away': away_team_name_pick.upper(),
                'margin': profit_margin,
                'home_book': h_site.upper(),
                'away_book': a_site.upper(),
                'home_odds': best_home_odds,
                'away_odds': best_away_odds,
                'home_stake': home_stake,
                'away_stake': away_stake,
                'guaranteed_profit': guaranteed_profit,
                'game_num': game_num,
                'game_time': game_time,
                'matchup_str': matchup_str
            }
            if game_time.date() == current_time.date():
                pick_list_today.append(pick_data)
                pick_count_today += 1
            elif game_time.date() == tomorrow_date:
                pick_list_tomorrow.append(pick_data)
                pick_count_tomorrow += 1

            logging.debug(f'============================================\n')

        logging.info(f"[{current_time.strftime('%m-%d-%Y')}] Today's Arbitrage Opportunities ({pick_count_today}):")
        logging.info(f'============================================\n')
        
        pick_list_today.sort(key=lambda x: x['margin'], reverse=True)
        if not pick_list_today:
            logging.info("No arbitrage opportunities found for today's match-ups.\n")
            
        for p in pick_list_today:
            pick_str = f"[{p.get('league', '')}] [{p['market'].upper()}] [ARB: +{round(p['margin']*100, 2)}%] [Total Stake: $100.00]"
            full_str = f"#{p['game_num']}\t{p['game_time'].strftime('%I:%M:%S %p')} - {pick_str}\n"
            full_str += f"\tLeg 1: {p['team_home']} @ {p['home_odds']} ({p['home_book']}) - Stake: ${round(p['home_stake'], 2)}\n"
            full_str += f"\tLeg 2: {p['team_away']} @ {p['away_odds']} ({p['away_book']}) - Stake: ${round(p['away_stake'], 2)}\n"
            full_str += f"\tGuaranteed Payout: ${round(p['home_stake'] * p['home_odds'], 2)} | Guaranteed Profit: +${round(p['guaranteed_profit'], 2)}\n"
            full_str += f"\t[{p['matchup_str']}]\n"
            logging.info(full_str)

        logging.info(f"[{tomorrow_date.strftime('%m-%d-%Y')}] Tomorrow's Arbitrage Opportunities ({pick_count_tomorrow}):")
        logging.info(f'============================================\n')
        
        pick_list_tomorrow.sort(key=lambda x: x['margin'], reverse=True)
        if not pick_list_tomorrow:
            logging.info("No arbitrage opportunities found for tomorrow's match-ups.\n")
            
        for p in pick_list_tomorrow:
            pick_str = f"[{p.get('league', '')}] [{p['market'].upper()}] [ARB: +{round(p['margin']*100, 2)}%] [Total Stake: $100.00]"
            full_str = f"#{p['game_num']}\t{p['game_time'].strftime('%I:%M:%S %p')} - {pick_str}\n"
            full_str += f"\tLeg 1: {p['team_home']} @ {p['home_odds']} ({p['home_book']}) - Stake: ${round(p['home_stake'], 2)}\n"
            full_str += f"\tLeg 2: {p['team_away']} @ {p['away_odds']} ({p['away_book']}) - Stake: ${round(p['away_stake'], 2)}\n"
            full_str += f"\tGuaranteed Payout: ${round(p['home_stake'] * p['home_odds'], 2)} | Guaranteed Profit: +${round(p['guaranteed_profit'], 2)}\n"
            full_str += f"\t[{p['matchup_str']}]\n"
            logging.info(full_str)

def main():
    parser = argparse.ArgumentParser(description="Arbitrage Finder - Cross-Book Arbitrage Strategy")
    parser.add_argument('--league', type=str, nargs='+', default=['nba'], choices=['nba', 'mlb', 'nhl', 'nfl'], help='League(s) to analyze (e.g. --league nba nhl)')
    parser.add_argument('--market', type=str, nargs='+', default=['h2h', 'spreads', 'totals'], choices=['h2h', 'spreads', 'totals'], help='Market(s) to analyze (e.g. --market h2h spreads totals)')
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
    logging.info(f"Initializing Arbitrage Finder for {leagues_str} [{markets_str}]...")
    finder = ArbitrageFinder(leagues=args.league, markets=args.market, api_key=api_key, sportsbooks=args.book)
    finder.process_games()

if __name__ == "__main__":
    main()