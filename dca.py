import logging
import os
from decimal import Decimal
from typing import Dict, List

from pydantic import Field

from hummingbot.client.config.config_data_types import BaseClientModel, ClientFieldData
from hummingbot.connector.connector_base import ConnectorBase
from hummingbot.core.data_type.common import OrderType, PriceType, TradeType
from hummingbot.core.data_type.order_candidate import OrderCandidate
from hummingbot.core.event.events import OrderFilledEvent
from hummingbot.strategy.script_strategy_base import ScriptStrategyBase


class Config(BaseClientModel):
    script_file_name: str = Field(default_factory=lambda: os.path.basename(__file__))
    exchange: str = Field("binance_paper_trade", client_data=ClientFieldData(
        prompt_on_new=True, prompt=lambda mi: "Exchange where the bot will trade"))
    trading_pair: str = Field("BTC-USDT", client_data=ClientFieldData(
        prompt_on_new=True, prompt=lambda mi: "Trading pair in which the bot will place orders"))
    shares: int = Field(1, client_data=ClientFieldData(
        prompt_on_new=True, prompt=lambda mi: "portions for total value in usd"))
    min_size: float = Field(0.001, client_data=ClientFieldData(
        prompt_on_new=True, prompt=lambda mi: "minimal size of the order"))
    max_size: float = Field(0.001, client_data=ClientFieldData(
        prompt_on_new=True, prompt=lambda mi: "maximum size of the order"))
    min_profit_percent: float = Field(0.001, client_data=ClientFieldData(
        prompt_on_new=True, prompt=lambda mi: "minimal profit of the order"))
    add_position_step_ratio: float = Field(0.02, client_data=ClientFieldData(
        prompt_on_new=True, prompt=lambda mi: "position step increase ratio"))
    order_delay_time: int = Field(10, client_data=ClientFieldData(
        prompt_on_new=True, prompt=lambda mi: "Delay time between orders (in seconds)"))


class (ScriptStrategyBase):

    create_timestamp = 0
    price_source = PriceType.MidPrice

    @classmethod
    def init_markets(cls, config: Config):
        cls.markets = {config.exchange: {config.trading_pair}}

    def __init__(self, connectors: Dict[str, ConnectorBase], config: Config):
        super().__init__(connectors)
        self.config = config
        self.token = get_token(self.config.trading_pair)
        self.user_id = self.config.exchange_user_id

    def on_tick(self):
        if self.create_timestamp <= self.current_timestamp:
            proposal: List[OrderCandidate] = self.create_proposal()
            proposal_adjusted: List[OrderCandidate] = self.adjust_proposal_to_budget(proposal)
            self.place_orders(proposal_adjusted)
            self.create_timestamp = self.config.order_refresh_time + self.current_timestamp

    def create_proposal(self) -> List[OrderCandidate]:
        usdt_balance = self.connectors[self.config.exchange].get_balance("usdt")
        token_index_price = self.connectors[self.config.exchange].get_price_by_type(self.config.trading_pair, self.price_source)
        token_balance = self.connectors[self.config.exchange].get_balance(self.token)
        total_value_in_usdt = usdt_balance + token_balance * token_index_price
        unit_size = float(total_value_in_usdt) / self.config.shares
        if min_size > 0 and unit_size < min_size:
            unit_size = min_size
        if max_size > 0 and unit_size > max_size:
            unit_size = max_size
        if unit_size < MIN_SPOT_AMOUNT:
            logger.info(f"#{user_id}:{ex} unit_size: {unit_size:.2f} this unit size is less than exchange minimal amount")
            return  

        # total = self.connectors[self.config.exchange].get_balance("total")
        # token_value = token_balance * token_index_price
        last_price = rdb.get(f"dca:{user_id}:{ex}:{token}:long:price")
        if not last_price:
            logger.error(f"#{user_id}:{ex} no last_price for {token}")
            return

        # increase position
        ratio = (last_price - token_index_price) / last_price
        if ratio < -(self.config.add_position_step_ratio):
            logger.info(f"#{user_id}:{ex} increase position {token}")
            buy_order = OrderCandidate(
                trading_pair=self.config.trading_pair, 
                is_maker=True, 
                order_type=OrderType.LIMIT,
                order_side=TradeType.BUY, 
                amount=Decimal(unit_size), 
                price=token_index_price
            )
            return buy_order

        # decrease position
        entry_price = rdb.get(f"dca:{user_id}:{ex}:{token}:long:entry_price")
        ratio = (entry_price - token_index_price) / entry_price
        if ratio > self.config.add_position_step_ratio
            logger.info
            (f"#{user_id}:{ex} increase position {token}")
            sell_order = OrderCandidate(
                trading_pair=self.config.trading_pair, 
                is_maker=True, 
                order_type=OrderType.LIMIT,
                order_side=TradeType.SELL, 
                amount=Decimal(unit_size), 
                price=token_index_price
            )
            return sell_order        

    def adjust_proposal_to_budget(self, proposal: List[OrderCandidate]) -> List[OrderCandidate]:
        proposal_adjusted = self.connectors[self.config.exchange].budget_checker.adjust_candidates(proposal, all_or_none=True)
        return proposal_adjusted

    def place_orders(self, proposal: List[OrderCandidate]) -> None:
        for order in proposal:
            self.place_order(connector_name=self.config.exchange, order=order)

    def place_order(self, connector_name: str, order: OrderCandidate):
        if order.order_side == TradeType.SELL:
            self.sell(connector_name=connector_name, trading_pair=order.trading_pair, amount=order.amount,
                      order_type=order.order_type, price=order.price)
        elif order.order_side == TradeType.BUY:
            self.buy(connector_name=connector_name, trading_pair=order.trading_pair, amount=order.amount,
                     order_type=order.order_type, price=order.price)

    def did_fill_order(self, event: OrderFilledEvent):
        msg = (f"{event.trade_type.name} {round(event.amount, 2)} {event.trading_pair} {self.config.exchange} at {round(event.price, 2)}")

        entry_price = rdb.get(f"dca:{user_id}:{ex}:{token}:long:entry_price")
        accumulate_amount = rdb.get(f"dca:{user_id}:{ex}:{token}:long:accumulate_amount")
        if event.order_type == buy:
            accumulate_amount += event.amount
            new_entry_price = (entry_price*accumulate_amount + event.amount*event.price)/ accumulate_amount
            rdb.set(f"dca:{user_id}:{ex}:{token}:long:entry_price", new_entry_price)
            rdb.set(f"dca:{user_id}:{ex}:{token}:long:accumulate_amount", accumulate_amount)

        if event.order_type == sell:
            accumulate_amount -= event.amount
            rdb.set(f"dca:{user_id}:{ex}:{token}:long:accumulate_amount", accumulate_amount)
            rdb.set(f"dca:{user_id}:{ex}:{token}:long:entry_price", entry_price)


        self.log_with_clock(logging.INFO, msg)
        self.notify_hb_app_with_timestamp(msg)
