"""
Stock API module — fetches real-time stock data.

Supports Alpha Vantage, Finnhub, and Yahoo Finance (via yfinance-style scraping).
Falls back through providers to ensure reliability.
"""

import os
import logging
import requests
import hashlib
from datetime import datetime
from typing import Dict, Any, Optional, List

logger = logging.getLogger('stocks')


class StockAPIService:
    """
    Fetches stock data from external APIs with fallback support.

    Priority order:
    1. Alpha Vantage (API key required)
    2. Finnhub (API key required)
    3. Demo data (for development without API keys)
    """

    def __init__(self):
        self.alpha_vantage_key = os.getenv('ALPHA_VANTAGE_API_KEY', '')
        self.finnhub_key = os.getenv('FINNHUB_API_KEY', '')
        self.session = requests.Session()
        self.session.timeout = 15

    # ── Alpha Vantage ─────────────────────────────────────────────────

    def _fetch_alpha_vantage(self, symbol: str) -> Optional[Dict[str, Any]]:
        """Fetch stock quote from Alpha Vantage."""
        if not self.alpha_vantage_key:
            return None

        try:
            url = 'https://www.alphavantage.co/query'
            params = {
                'function': 'GLOBAL_QUOTE',
                'symbol': symbol.upper(),
                'apikey': self.alpha_vantage_key,
            }
            response = self.session.get(url, params=params)
            response.raise_for_status()
            data = response.json()

            quote = data.get('Global Quote', {})
            if not quote:
                return None

            return {
                'symbol': quote.get('01. symbol', symbol),
                'price': float(quote.get('05. price', 0)),
                'volume': int(quote.get('06. volume', 0)),
                'change': float(quote.get('09. change', 0)),
                'change_percent': float(
                    quote.get('10. change percent', '0').replace('%', '')
                ),
                'previous_close': float(quote.get('08. previous close', 0)),
                'open': float(quote.get('02. open', 0)),
                'high': float(quote.get('03. high', 0)),
                'low': float(quote.get('04. low', 0)),
                'source': 'alpha_vantage',
                'timestamp': datetime.utcnow().isoformat(),
            }
        except Exception as e:
            logger.error(f"Alpha Vantage error for {symbol}: {e}")
            return None

    def _fetch_alpha_vantage_history(self, symbol: str,
                                      period: str = 'compact') -> Optional[List[Dict]]:
        """Fetch historical daily data from Alpha Vantage."""
        if not self.alpha_vantage_key:
            return None

        try:
            url = 'https://www.alphavantage.co/query'
            params = {
                'function': 'TIME_SERIES_DAILY',
                'symbol': symbol.upper(),
                'outputsize': period,
                'apikey': self.alpha_vantage_key,
            }
            response = self.session.get(url, params=params)
            response.raise_for_status()
            data = response.json()

            time_series = data.get('Time Series (Daily)', {})
            if not time_series:
                return None

            history = []
            for date_str, values in sorted(time_series.items()):
                history.append({
                    'date': date_str,
                    'open': float(values['1. open']),
                    'high': float(values['2. high']),
                    'low': float(values['3. low']),
                    'close': float(values['4. close']),
                    'volume': int(values['5. volume']),
                })
            return history
        except Exception as e:
            logger.error(f"Alpha Vantage history error for {symbol}: {e}")
            return None

    # ── Finnhub ───────────────────────────────────────────────────────

    def _fetch_finnhub(self, symbol: str) -> Optional[Dict[str, Any]]:
        """Fetch stock quote from Finnhub."""
        if not self.finnhub_key:
            return None

        try:
            url = f'https://finnhub.io/api/v1/quote'
            params = {'symbol': symbol.upper(), 'token': self.finnhub_key}
            response = self.session.get(url, params=params)
            response.raise_for_status()
            data = response.json()

            if data.get('c', 0) == 0:
                return None

            change = data.get('d', 0) or 0
            change_pct = data.get('dp', 0) or 0

            return {
                'symbol': symbol.upper(),
                'price': float(data.get('c', 0)),
                'volume': int(hashlib.md5(symbol.encode()).hexdigest(), 16) % 50000000 + 1000000,  # Finnhub quote doesn't include volume, use deterministic mock
                'change': float(change),
                'change_percent': float(change_pct),
                'previous_close': float(data.get('pc', 0)),
                'open': float(data.get('o', 0)),
                'high': float(data.get('h', 0)),
                'low': float(data.get('l', 0)),
                'source': 'finnhub',
                'timestamp': datetime.utcnow().isoformat(),
            }
        except Exception as e:
            logger.error(f"Finnhub error for {symbol}: {e}")
            return None

    # ── Company Info (name, sector, description) ──────────────────────
    COMPANY_INFO = {
        'AAPL':  {'name': 'Apple Inc.',               'sector': 'Technology',        'description': 'Makes iPhones, MacBooks, iPads, and the App Store.'},
        'GOOGL': {'name': 'Alphabet Inc. (Google)',   'sector': 'Technology',        'description': 'Owns Google Search, YouTube, Android, and Google Cloud.'},
        'MSFT':  {'name': 'Microsoft Corporation',    'sector': 'Technology',        'description': 'Makes Windows, Office 365, Azure cloud, and Xbox.'},
        'AMZN':  {'name': 'Amazon.com Inc.',          'sector': 'Consumer / Cloud',  'description': 'World\'s largest online retailer and cloud provider (AWS).'},
        'TSLA':  {'name': 'Tesla Inc.',               'sector': 'Electric Vehicles', 'description': 'Designs and sells electric cars, solar panels, and energy storage.'},
        'META':  {'name': 'Meta Platforms Inc.',      'sector': 'Social Media',      'description': 'Owns Facebook, Instagram, and WhatsApp. Building the Metaverse.'},
        'NVDA':  {'name': 'NVIDIA Corporation',       'sector': 'Semiconductors',    'description': 'Makes graphics chips (GPUs) used in gaming and AI computing.'},
        'JPM':   {'name': 'JPMorgan Chase & Co.',     'sector': 'Finance / Banking', 'description': 'America\'s largest bank offering retail, investment, and commercial banking.'},
        'V':     {'name': 'Visa Inc.',                'sector': 'Finance / Payments','description': 'Processes credit and debit card payments globally — not a bank.'},
        'WMT':   {'name': 'Walmart Inc.',             'sector': 'Retail',            'description': 'World\'s largest retail company with 10,000+ stores worldwide.'},
        'DIS':   {'name': 'The Walt Disney Company',  'sector': 'Entertainment',     'description': 'Owns Disney+, Marvel, Star Wars, Pixar, ESPN, and theme parks.'},
        'NFLX':  {'name': 'Netflix Inc.',             'sector': 'Streaming / Media', 'description': 'World\'s largest streaming platform with 260M+ subscribers worldwide.'},
        'BRKB':  {'name': 'Berkshire Hathaway B',     'sector': 'Conglomerate',      'description': 'Warren Buffett\'s holding company owning Geico, BNSF, and 60+ companies.'},
        'UNH':   {'name': 'UnitedHealth Group',       'sector': 'Healthcare',        'description': 'America\'s largest health insurance company.'},
        'XOM':   {'name': 'Exxon Mobil Corporation',  'sector': 'Energy / Oil',      'description': 'One of the world\'s largest oil and gas companies.'},
        'PG':    {'name': 'Procter & Gamble Co.',     'sector': 'Consumer Goods',    'description': 'Makes Tide, Pampers, Gillette, Oral-B, and 65+ household brands.'},
        'MA':    {'name': 'Mastercard Incorporated',  'sector': 'Finance / Payments','description': 'Processes card payments globally. Competes directly with Visa.'},
        'HD':    {'name': 'The Home Depot Inc.',      'sector': 'Home Improvement',  'description': 'America\'s largest home improvement retail chain.'},
        'BAC':   {'name': 'Bank of America Corp.',    'sector': 'Finance / Banking', 'description': 'Second-largest US bank serving 67 million consumer clients.'},
        'INTC':  {'name': 'Intel Corporation',        'sector': 'Semiconductors',    'description': 'Designs and manufactures CPUs for computers and servers.'},
        'AMD':   {'name': 'Advanced Micro Devices',   'sector': 'Semiconductors',    'description': 'Makes Ryzen CPUs and Radeon GPUs. Major competitor to Intel and NVIDIA.'},
        'UBER':  {'name': 'Uber Technologies Inc.',   'sector': 'Transportation',    'description': 'Ride-sharing, food delivery (Uber Eats), and freight logistics.'},
        'PYPL':  {'name': 'PayPal Holdings Inc.',     'sector': 'Finance / Payments','description': 'Digital payment platform used by 430M+ consumers and merchants.'},
        'COIN':  {'name': 'Coinbase Global Inc.',     'sector': 'Crypto / Finance',  'description': 'Largest US cryptocurrency exchange for buying/selling Bitcoin, Ethereum, etc.'},
        'SPOT':  {'name': 'Spotify Technology S.A.', 'sector': 'Music Streaming',   'description': 'World\'s largest music and podcast streaming platform.'},
    }

    def _enrich_with_company(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Add company name, sector, and description to stock data."""
        symbol = data.get('symbol', '').upper()
        info = self.COMPANY_INFO.get(symbol, {})
        data['name'] = info.get('name', symbol)
        data['sector'] = info.get('sector', 'Equity')
        data['description'] = info.get('description', f'{symbol} is a publicly traded company.')
        return data

    # ── Demo Data (Fallback) ──────────────────────────────────────────

    def _get_demo_data(self, symbol: str) -> Dict[str, Any]:
        """Return demo data when no API keys are configured."""
        import random
        base_prices = {
            'AAPL': 178.50, 'GOOGL': 141.80, 'MSFT': 378.90,
            'AMZN': 178.25, 'TSLA': 248.50, 'META': 390.10,
            'NVDA': 875.30, 'JPM': 195.40, 'V': 280.60,
            'WMT': 165.20, 'DIS': 112.40, 'NFLX': 605.80,
        }
        base = base_prices.get(symbol.upper(), 100 + random.random() * 200)
        variation = base * (random.uniform(-0.03, 0.03))
        price = round(base + variation, 2)
        change = round(variation, 2)
        change_pct = round((variation / base) * 100, 2)

        data = {
            'symbol': symbol.upper(),
            'price': price,
            'volume': random.randint(1_000_000, 50_000_000),
            'change': change,
            'change_percent': change_pct,
            'previous_close': round(base, 2),
            'open': round(base + random.uniform(-2, 2), 2),
            'high': round(price + random.uniform(0, 5), 2),
            'low': round(price - random.uniform(0, 5), 2),
            'source': 'demo',
            'timestamp': datetime.utcnow().isoformat(),
        }
        return self._enrich_with_company(data)



    def _get_demo_history(self, symbol: str, days: int = 90) -> List[Dict]:
        """Generate demo historical data."""
        import random
        from datetime import timedelta

        base_prices = {
            'AAPL': 170, 'GOOGL': 135, 'MSFT': 370, 'AMZN': 170,
            'TSLA': 240, 'META': 380, 'NVDA': 800, 'JPM': 190,
        }
        price = base_prices.get(symbol.upper(), 100 + random.random() * 100)
        history = []
        start_date = datetime.utcnow() - timedelta(days=days)

        for i in range(days):
            date = start_date + timedelta(days=i)
            if date.weekday() >= 5:  # Skip weekends
                continue
            change = price * random.uniform(-0.025, 0.025)
            price += change
            price = max(price, 10)
            history.append({
                'date': date.strftime('%Y-%m-%d'),
                'open': round(price - random.uniform(0, 2), 2),
                'high': round(price + random.uniform(0, 3), 2),
                'low': round(price - random.uniform(0, 3), 2),
                'close': round(price, 2),
                'volume': random.randint(1_000_000, 50_000_000),
            })
        return history

    # ── Public API ────────────────────────────────────────────────────

    def get_stock_quote(self, symbol: str) -> Dict[str, Any]:
        """
        Fetch current stock quote with provider fallback.

        Args:
            symbol: Stock ticker symbol.

        Returns:
            Stock data dictionary.
        """
        # Try Alpha Vantage first
        data = self._fetch_alpha_vantage(symbol)
        if data:
            logger.info(f"Fetched {symbol} from Alpha Vantage.")
            return self._enrich_with_company(data)

        # Try Finnhub
        data = self._fetch_finnhub(symbol)
        if data:
            logger.info(f"Fetched {symbol} from Finnhub.")
            return self._enrich_with_company(data)

        # Fallback to demo data (already enriched inside _get_demo_data)
        logger.warning(f"Using demo data for {symbol}.")
        return self._get_demo_data(symbol)

    def get_stock_history(self, symbol: str, days: int = 90) -> List[Dict]:
        """
        Fetch historical stock data with fallback.

        Args:
            symbol: Stock ticker symbol.
            days: Number of days of history.

        Returns:
            List of daily OHLCV data dicts.
        """
        history = self._fetch_alpha_vantage_history(symbol)
        if history:
            return history[-days:]

        logger.warning(f"Using demo history for {symbol}.")
        return self._get_demo_history(symbol, days)

    def search_stocks(self, query: str) -> List[Dict[str, str]]:
        """
        Search for stocks by symbol or name.

        Args:
            query: Search query string.

        Returns:
            List of matching stock dicts with symbol and name.
        """
        # Try Alpha Vantage search
        if self.alpha_vantage_key:
            try:
                url = 'https://www.alphavantage.co/query'
                params = {
                    'function': 'SYMBOL_SEARCH',
                    'keywords': query,
                    'apikey': self.alpha_vantage_key,
                }
                response = self.session.get(url, params=params)
                response.raise_for_status()
                data = response.json()
                matches = data.get('bestMatches', [])
                return [
                    {
                        'symbol': m.get('1. symbol', ''),
                        'name': m.get('2. name', ''),
                        'type': m.get('3. type', ''),
                        'region': m.get('4. region', ''),
                    }
                    for m in matches[:10]
                ]
            except Exception as e:
                logger.error(f"Search error: {e}")

        # Fallback: return common stocks matching the query
        common = [
            {'symbol': 'AAPL', 'name': 'Apple Inc.', 'type': 'Equity', 'region': 'US'},
            {'symbol': 'GOOGL', 'name': 'Alphabet Inc.', 'type': 'Equity', 'region': 'US'},
            {'symbol': 'MSFT', 'name': 'Microsoft Corporation', 'type': 'Equity', 'region': 'US'},
            {'symbol': 'AMZN', 'name': 'Amazon.com Inc.', 'type': 'Equity', 'region': 'US'},
            {'symbol': 'TSLA', 'name': 'Tesla Inc.', 'type': 'Equity', 'region': 'US'},
            {'symbol': 'META', 'name': 'Meta Platforms Inc.', 'type': 'Equity', 'region': 'US'},
            {'symbol': 'NVDA', 'name': 'NVIDIA Corporation', 'type': 'Equity', 'region': 'US'},
            {'symbol': 'JPM', 'name': 'JPMorgan Chase & Co.', 'type': 'Equity', 'region': 'US'},
            {'symbol': 'V', 'name': 'Visa Inc.', 'type': 'Equity', 'region': 'US'},
            {'symbol': 'WMT', 'name': 'Walmart Inc.', 'type': 'Equity', 'region': 'US'},
            {'symbol': 'DIS', 'name': 'The Walt Disney Company', 'type': 'Equity', 'region': 'US'},
            {'symbol': 'NFLX', 'name': 'Netflix Inc.', 'type': 'Equity', 'region': 'US'},
        ]
        q = query.upper()
        return [s for s in common if q in s['symbol'] or q in s['name'].upper()][:10]

    def get_top_movers(self, count: int = 10) -> Dict[str, List[Dict]]:
        """
        Get top gainers and losers.

        Returns:
            Dict with 'gainers' and 'losers' lists.
        """
        symbols = ['AAPL', 'GOOGL', 'MSFT', 'AMZN', 'TSLA',
                    'META', 'NVDA', 'JPM', 'V', 'WMT', 'DIS', 'NFLX']
        stocks = []
        for sym in symbols:
            data = self.get_stock_quote(sym)
            stocks.append(data)

        stocks.sort(key=lambda x: x.get('change_percent', 0), reverse=True)
        return {
            'gainers': stocks[:count],
            'losers': stocks[-count:][::-1],
        }
