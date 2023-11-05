import threading
import time
from datetime import datetime, timezone, timedelta
import pandas as pd
import pandas_ta as ta
from binance import Client
from binance.helpers import round_step_size
from pprint import pprint
import pytz


def get_atr(df, length):
    return ta.atr(high=df['High'], low=df['Low'], close=df['Close'], length=length)


def kernel_regression(_src, _h, relative_weight, start_reg):
    _currentWeight = 0.0
    _cumulativeWeight = 0.0
    for i in range(start_reg + 2):
        y = _src[i]
        w = pow(1 + (pow(i, 2) / (pow(_h, 2) * 2 * relative_weight)), -relative_weight)
        _currentWeight += y * w
        _cumulativeWeight += w

    return _currentWeight, _cumulativeWeight


def get_quantity(_price, _margin):
    return _margin / _price


class Process:
    def __init__(self):
        # Trading settings
        self.syms = []
        self.look_back = 8.0  # Look-back period for calculating indicators
        self.relative_weight = 8.0  # Relative weight used in indicator calculations
        self.start_reg = 25  # Starting value for a specific indicator
        self.lag = 2  # Lag parameter used in calculations

        self.atr_period = 32  # Period used in Average True Range (ATR) calculation
        self.atr_multi = 2.7  # Multiplier for Average True Range (ATR) indicator

        self.is_heikin = False
        self.read_config()

        # API credentials
        api_key = ""  # Your API key for accessing the trading platform
        api_secret = ""  # Your API secret for accessing the trading platform

        if api_key:
            self.bi_cli = self.bi_cli = Client(api_key=api_key, api_secret=api_secret)
            print("Bot Started")
        else:
            print('Doesnt found API key')
            quit()

        self.tfs = {
            '1m': self.bi_cli.KLINE_INTERVAL_1MINUTE,
            '3m': self.bi_cli.KLINE_INTERVAL_3MINUTE,
            '15m': self.bi_cli.KLINE_INTERVAL_15MINUTE,
            '30m': self.bi_cli.KLINE_INTERVAL_30MINUTE,
            '5m': self.bi_cli.KLINE_INTERVAL_5MINUTE,
            '1h': self.bi_cli.KLINE_INTERVAL_1HOUR,
            '2h': self.bi_cli.KLINE_INTERVAL_2HOUR,
            '4h': self.bi_cli.KLINE_INTERVAL_4HOUR,
            '6h': self.bi_cli.KLINE_INTERVAL_6HOUR,
            '8h': self.bi_cli.KLINE_INTERVAL_8HOUR,
            '12h': self.bi_cli.KLINE_INTERVAL_12HOUR,
            '1d': self.bi_cli.KLINE_INTERVAL_1DAY,
            '3d': self.bi_cli.KLINE_INTERVAL_3DAY
        }

        for sym in self.syms:
            time.sleep(1)
            threading.Thread(target=self.strategy, args=(sym,)).start()

    def read_config(self):
        with open("settings.txt", "r") as f:
            lines = f.readlines()
            line_ctr = 0

            self.look_back = float(lines[line_ctr].split('=')[1].replace("\n", ""))
            line_ctr += 1

            self.relative_weight = float(lines[line_ctr].split('=')[1].replace("\n", ""))
            line_ctr += 1

            self.start_reg = int(lines[line_ctr].split('=')[1].replace("\n", ""))
            line_ctr += 1

            self.lag = int(lines[line_ctr].split('=')[1].replace("\n", ""))
            line_ctr += 1

            self.atr_period = int(lines[line_ctr].split('=')[1].replace("\n", ""))
            line_ctr += 1

            self.atr_multi = float(lines[line_ctr].split('=')[1].replace("\n", ""))
            line_ctr += 1

            self.is_heikin = True if str(lines[line_ctr].split('=')[1].replace("\n", "")) == 'on' else False
            line_ctr += 3

            for line in lines[line_ctr:]:
                if 'USDT' in line:
                    split = line.split(',')
                    self.syms.append([split[0], split[1], split[2].replace("\n", "")])

    def get_kline(self, pair, interval):
        for _ in range(3):
            try:
                kline_data = self.bi_cli.get_historical_klines(symbol=pair, interval=self.tfs[interval], limit=500, start_str=str(datetime.now() - timedelta(days=3 if 'm' in interval else 20 if 'h' in interval else 40)))
                break
            except Exception as e:
                print(str(e))
                time.sleep(1)
        else:
            kline_data = None

        if kline_data:
            kline_df = pd.DataFrame(kline_data, columns=['Time', 'Open', 'High', 'Low', 'Close', 'Volume',
                                                         'Close Time', 'Quote asset volume',
                                                         'Num of trades', 'Taker buy asset vol', 'Taker quote asset vol', 'Ignore'])

            kline_df['Time'] = kline_df['Time'].apply(lambda x: datetime.fromtimestamp(x / 1000, tz=timezone.utc))
            kline_df['Close Time'] = kline_df['Close Time'].apply(lambda x: datetime.fromtimestamp(x / 1000, tz=timezone.utc))
            kline_df = kline_df.astype({'Close': float, 'Open': float, 'High': float, 'Low': float})
            return kline_df
        else:
            return None

    def get_precision(self, pair):
        all_info = self.bi_cli.get_symbol_info(symbol=pair)
        try:
            for i in all_info['filters']:
                if i['filterType'] == 'LOT_SIZE':
                    return float(i['minQty']), i['stepSize']
        except Exception as e:
            print(str(e))

    def get_tick_size(self, symbol):
        for _ in range(3):
            try:
                return self.bi_cli.get_symbol_info(symbol=symbol)['filters'][0]['tickSize']
            except Exception as e:
                print(e)
        else:
            return False

    def get_acc_balance(self):
        try:
            account_info = self.bi_cli.get_account()
            balances = account_info['balances']
            for asset in balances:
                if asset['asset'] == 'USDT':
                    free_balance = float(asset['free'])
                    return free_balance
            else:
                return 0
        except Exception as e:
            print(f"Error getting spot wallet balance: {e}")

    def strategy(self, sym_data):
        print(sym_data)
        sym = sym_data[0]
        sym_tf = sym_data[1]
        trade_size = int(sym_data[2])
        curr_trade = {
            "is_traded": False,
            'side': -1,

        }
        # print(self.bi_cli.create_order(symbol=self.sym, side='SELL', type='MARKET', quantity=0.0005))
        min_qty, step_size = self.get_precision(sym)
        is_time_set = False
        while True:
            k_df = self.get_kline(sym, sym_tf)
            if not is_time_set:
                curr_trade['Time'] = k_df['Time'].iloc[-1]
                is_time_set = True
            if curr_trade['Time'] != k_df['Time'].iloc[-1]:
                yhat1 = []

                if self.is_heikin:
                    df = ta.ha(k_df['Open'], k_df['High'], k_df['Low'], k_df['Close'])
                    reversed_column = list(df['HA_close'][::-1])
                else:
                    reversed_column = list(k_df['Close'][::-1])

                reversed_patches = [list(reversed_column[i:i + self.start_reg + 2]) for i in range(1, len(reversed_column))]

                for patch in reversed_patches[:4]:
                    currentWeight1, cumulativeWeight1 = kernel_regression(patch, self.look_back, self.relative_weight, self.start_reg)
                    yhat1.append(currentWeight1 / cumulativeWeight1)
                # currentWeight1, cumulativeWeight1 = kernel_regression(reversed_patches[1], self.look_back, self.relative_weight, self.start_reg)
                # yhat1.append(currentWeight1 / cumulativeWeight1)
                # print(yhat1)
                if len(yhat1) >= 3:
                    bullish = yhat1[0] > yhat1[1]
                    bearish = yhat1[0] < yhat1[1]
                    was_bullish = yhat1[1] > yhat1[2]
                    was_bearish = yhat1[1] < yhat1[2]
                    # print(bullish, bearish)
                    # print(was_bullish, was_bearish)
                    if bullish and was_bearish:
                        if not curr_trade['is_traded']:
                            curr_price = float(k_df['Close'].iloc[-1])
                            curr_bal = self.get_acc_balance()

                            if curr_bal > 0:
                                qty = get_quantity(_price=curr_price, _margin=trade_size)
                                qty = round_step_size(qty, step_size)
                                if qty < min_qty:
                                    print(f"{sym}\nQty is less than min qty {min_qty}")
                                else:
                                    for _ in range(3):
                                        try:
                                            resp = self.bi_cli.create_order(symbol=sym, side='BUY', type='MARKET', quantity=qty)
                                            print(f"\nTrade Taken {sym}\nQuantity: {qty}\nTime: {datetime.now(tz=pytz.timezone('Europe/Rome'))}\nBalance: {curr_bal}\n{resp}")
                                            break
                                        except Exception as e:
                                            print(e)

                                    curr_trade['is_traded'] = True
                                    curr_trade['side'] = 0

                            else:
                                print(f"{sym}\nNo balance available")

                    if bearish and was_bullish:
                        if curr_trade['is_traded']:
                            if curr_trade['side'] == 0:
                                for _ in range(3):
                                    try:
                                        for asset in self.bi_cli.get_account()['balances']:
                                            if asset['asset'] == sym.replace("USDT", ""):
                                                resp = self.bi_cli.create_order(symbol=sym, side='SELL', type='MARKET', quantity=round_step_size(float(asset['free']), step_size))
                                                print(f"\nTrade Closed {sym}\nTime: {datetime.now(tz=pytz.timezone('Europe/Rome'))}\n{resp}")
                                                break
                                        break
                                    except Exception as e:
                                        print(e)

                                curr_trade['is_traded'] = False

            time.sleep(3)


p = Process()
