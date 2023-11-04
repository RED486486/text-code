import time
import requests
import hashlib
import hmac
import json
import urllib.parse
import math
import os
import signal

class TraderConfig:
    def __init__(self, symbol, leverage, total_margin, drop_to_buy, rise_to_sell, max_buy_times, test_mode=True, api_key='', api_secret=''):
        self.symbol = symbol
        self.leverage = leverage
        self.total_margin = total_margin
        self.drop_to_buy = drop_to_buy
        self.rise_to_sell = rise_to_sell
        self.max_buy_times = max_buy_times
        self.test_mode = test_mode
        self.api_key = api_key
        self.api_secret = api_secret

class BinanceAPI:
    def __init__(self, config):
        self.config = config
        self.base_url = 'https://fapi.binance.com' if not config.test_mode else 'https://testnet.binancefuture.com'
        self.headers = {
            'X-MBX-APIKEY': config.api_key,
            'Content-Type': 'application/json;charset=utf-8'
        }

    def send_request(self, method, path, params=None):
        url = f'{self.base_url}{path}'
        params = params or {}
        params['timestamp'] = str(int(time.time() * 1000))
        params['recvWindow'] = 10000
        query_string = urllib.parse.urlencode(params)
        signature = hmac.new(self.config.api_secret.encode('utf-8'), query_string.encode('utf-8'), hashlib.sha256).hexdigest()
        full_url = f'{url}?{query_string}&signature={signature}'
        
        try:
            response = requests.request(method, full_url, headers=self.headers)
            response.raise_for_status()
            return response.json()
        except requests.HTTPError as http_err:
            print(f'HTTP error occurred: {http_err}')  # Python 3.6
        except Exception as err:
            print(f'Other error occurred: {err}')  # Python 3.6
        return None

    def get_symbol_info(self):
        path = '/fapi/v1/exchangeInfo'
        response = self.send_request('GET', path)
        if response and 'symbols' in response:
            for symbol_info in response['symbols']:
                if symbol_info['symbol'] == self.config.symbol.replace('/', ''):
                    price_filter = next(filter(lambda x: x['filterType'] == 'PRICE_FILTER', symbol_info['filters']), None)
                    if price_filter:
                        return {
                            'pricePrecision': symbol_info['pricePrecision'],
                            'quantityPrecision': symbol_info['quantityPrecision'],
                            'tickSize': float(price_filter['tickSize'])
                        }
        print(f'Failed to get symbol info: {response}')
        return None

    def get_market_price(self):
        # 获取当前市价
        url = f"{self.base_url}/fapi/v1/ticker/price?symbol={self.config.symbol.replace('/', '')}"
        try:
            response = requests.get(url)
            response.raise_for_status()  # 检查请求是否成功
            data = response.json()
            market_price = float(data['price'])
            print(f'Market price: {market_price}')  # 添加此打印语句来检查市价
            return market_price
        except requests.RequestException as e:
            print(f'Network or request error: {e}')
        except Exception as e:
            print(f'An error occurred: {e}')

    def create_order(self, side, price, amount):
        symbol_info = self.get_symbol_info()
        if not symbol_info:
            return None

        tick_size = symbol_info['tickSize']
        price = math.floor(price / tick_size) * tick_size
        price = round(price, symbol_info['pricePrecision'])
        amount = round(amount, symbol_info['quantityPrecision'])
        
        path = '/fapi/v1/order'
        params = {
            'symbol': self.config.symbol.replace('/', ''),
            'side': side.upper(),
            'type': 'LIMIT',
            'timeInForce': 'GTC',
            'quantity': str(amount),
            'price': str(price)
        }
        response = self.send_request('POST', path, params)
        if response and 'orderId' in response:
            return {'id': response['orderId'], 'info': response}
        
        print(f'Failed to create {side} order: {response}')
        return None

    def check_order_status(self, order_id):
        path = f'/fapi/v1/order'
        params = {
            'symbol': self.config.symbol.replace('/', ''),
            'orderId': str(order_id)
        }
        response = self.send_request('GET', path, params)
        if response:
            remaining_qty = float(response.get('origQty', 0)) - float(response.get('executedQty', 0))
            return response['status'], float(response.get('executedQty', 0)), remaining_qty
        print(f'Failed to check order status: {response}')
        return None, 0, 0  # 确保在这里返回三个值

    def cancel_order(self, order_id):
        path = f'/fapi/v1/order'
        params = {
            'symbol': self.config.symbol.replace('/', ''),
            'orderId': str(order_id),
            'timestamp': str(int(time.time() * 1000))
        }
        response = self.send_request('DELETE', path, params)
        if response and response.get('orderId'):
            print(f'Order {order_id} cancelled successfully')
        else:
            print(f'Failed to cancel order {order_id}: {response}')

class OrderManager:
    def __init__(self, config, api):
        self.config = config
        self.api = api
        self.buy_orders = {}
        self.sell_orders = {}
        self.sell_order_filled = False  # 你已经有了这个属性
        self.sell_order_executed = False  # 添加这行来定义 sell_order_executed 属性
        self.buy_order_filled = False  # 新增属性

    def place_buy_orders(self, base_price):
        single_margin = self.config.total_margin / self.config.max_buy_times
        single_amount = single_margin * self.config.leverage / base_price
        # 计算买入价格并挂买入限价单
        buy_prices = [base_price * (1 - self.config.drop_to_buy * i) for i in range(1, self.config.max_buy_times + 1)]
        for price in buy_prices:
            order = self.api.create_order('buy', price, single_amount)
            if order is None:
                print('Failed to create limit buy order. Retrying...')
                return  # 如果无法创建限价买单，则返回并在下一次循环中重试
            self.buy_orders[order['id']] = order

    def check_and_place_sell_orders(self):
        for order_id, order in list(self.buy_orders.items()):
            status, filled_amount, _ = self.api.check_order_status(order_id)
            print(f"Buy_order ID: {order_id}, Status: {status}, Filled Amount: {filled_amount}")  
            if status in ['FILLED', 'PARTIALLY_FILLED']:
                self.buy_order_filled = True  # 如果订单被执行，设置标志为True
                if float(order['info']['origQty']) == filled_amount:  # 检查订单是否完全填充
                    sell_price = float(order['info']['price']) * (1 + self.config.rise_to_sell)
                    print(f"Attempting to create sell_order with price: {sell_price}, amount: {filled_amount}")  # 新增此行

                    sell_order = None
                    retry_count = 3  # 设置重试次数
                    while retry_count > 0 and sell_order is None:
                        sell_order = self.api.create_order('sell', sell_price, filled_amount)
                        if sell_order is None:
                            print(f'Failed to create sell order for buy order {order_id}, retrying...')
                            retry_count -= 1
                        else:
                            self.sell_orders[sell_order['id']] = sell_order
                            self.sell_order_executed = True

                    if sell_order is not None:  # 在确认卖出订单已创建后删除买入订单
                        del self.buy_orders[order_id]

    def cancel_all_orders(self):
        for order_id in list(self.buy_orders.keys()):
            self.api.cancel_order(order_id)
            del self.buy_orders[order_id]

class GridTrader:
    def __init__(self, config):
        self.config = config
        self.api = BinanceAPI(config)
        self.order_manager = OrderManager(config, self.api)
        self.base_price = None
        self.new_cycle_flag = True
        self.order_executed = False  # 初始化 order_executed 属性
        
    def start_trade_cycle(self):
        print('Starting trade cycle...')  # 添加此打印语句来检查方法调用
        self.order_manager.buy_order_filled = False  # 新交易周期开始时清除买入订单成交标志
        # 取消所有未成交的买入和卖出定价单
        self.order_manager.cancel_all_orders()  # 调用 OrderManager 的 cancel_all_orders 方法
        self.base_price = self.api.get_market_price()
        print(f'New base_price={self.base_price}')
        if self.base_price is None:
            print('Failed to get the market price. Retrying...')
            return  # 如果无法获取市价，则返回并在下一次循环中重试
        self.order_manager.place_buy_orders(self.base_price)  # 调用 OrderManager 的 place_buy_orders 方法

    def check_price_rise(self):
        print(f'Checking price rise...')  
        # 检查是否有部分填充的买入订单
        partial_filled = any(
            [self.api.check_order_status(order_id)[0] == 'PARTIALLY_FILLED' for order_id in self.order_manager.buy_orders]
        )
        if not self.order_manager.buy_order_filled and not partial_filled:  # 仅在没有买入订单成交且没有部分填充的买入订单时检查价格上涨
            current_price = self.api.get_market_price()
            print(f'Current market price: {current_price}')  
            if current_price is not None and self.base_price is not None:
                threshold_price = self.base_price * (1 + self.config.rise_to_sell)
                print(f'Threshold price: {threshold_price}')  # 输出阈值价格，以便调试
                print(f'Checking if current_price > threshold_price: {current_price > threshold_price}') 
                if current_price > threshold_price:
                    self.new_cycle_flag = True  # 重置新周期标志

 
    def check_and_replace_buy_orders(self):
        drop_to_buy_ratio = 1 - self.config.drop_to_buy
        all_sell_orders_filled = True  # 初始化为True

        # 首先检查所有卖单是否已填充
        for order_id, order in list(self.order_manager.sell_orders.items()):
            status, filled_amount, _ = self.api.check_order_status(order_id)
            print(f"Sell_order ID: {order_id}, Status: {status}, Filled Amount: {filled_amount}")
            if status != 'FILLED':
                all_sell_orders_filled = False  # 如果找到未填充的卖出订单，设置标志为False

        if all_sell_orders_filled and len(self.order_manager.sell_orders) > 0:  # 检查是否有卖出订单
            # 如果所有卖出订单都被填充，开始新的交易周期
            del self.order_manager.sell_orders[order_id]  # 删除这个已经被填充的卖单
            self.new_cycle_flag = True  # 重置新周期标志
            return  # 退出函数，不再挂买单

        # 如果代码到达这里，说明有未填充的卖单，需要重新挂买单
        for order_id, order in list(self.order_manager.sell_orders.items()):
            status, filled_amount, _ = self.api.check_order_status(order_id)
            if status == 'FILLED':
                sell_price = float(order['info']['price'])
                new_buy_price = sell_price * drop_to_buy_ratio
                buy_amount = float(order['info']['origQty'])
                print(f"Attempting to create buy_order with price: {new_buy_price}, amount: {buy_amount}") 
                buy_order = self.api.create_order('buy', new_buy_price, buy_amount)
                if buy_order:
                    self.order_manager.buy_orders[buy_order['id']] = buy_order
                    del self.order_manager.sell_orders[order_id]  # 删除这个已经被填充的卖单
                else:
                    print(f"Failed to replenish buy order for sell order {order_id}")


    def trade(self):
        # 主交易循环
        while True:
            print(f'Entering trade loop: new_cycle_flag={self.new_cycle_flag}, base_price={self.base_price}')
            # 如果新周期标志为True或基准价为None，则开始新交易周期
            if self.new_cycle_flag or self.base_price is None:
                print(f'Calling new start_trade_cycle due to new_cycle_flag={self.new_cycle_flag} or base_price={self.base_price}')
                self.start_trade_cycle()
                self.new_cycle_flag = False  # 重置新周期标志

            self.order_manager.check_and_place_sell_orders()  # 检查并放置卖出订单
            self.check_and_replace_buy_orders()
            self.check_price_rise()

            time.sleep(1)

if __name__ == "__main__":
    config = TraderConfig(
        symbol='BNBUSDT',
        leverage=10,
        total_margin=1000,
        drop_to_buy=0.0011,
        rise_to_sell=0.001,
        max_buy_times=4,
        test_mode=True,  # 设置为 True 以使用模拟盘，设置为 False 以使用实盘
        api_key='xxx',  # 你的 Binance API Key
        api_secret='xxx'  # 你的 Binance API Secret
    )
    trader = GridTrader(config)
    trader.trade()
