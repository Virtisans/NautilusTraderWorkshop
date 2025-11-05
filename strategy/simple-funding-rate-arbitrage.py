from datetime import datetime
from pathlib import Path

import pandas as pd
from nautilus_trader.adapters.binance import (
    BINANCE_VENUE,
)
from nautilus_trader.common.events import TimeEvent
from nautilus_trader.common.enums import LogColor
from nautilus_trader.backtest.config import (
    BacktestDataConfig,
    BacktestEngineConfig,
    BacktestRunConfig,
    BacktestVenueConfig,
)
from nautilus_trader.backtest.node import BacktestNode
from nautilus_trader.config import (
    ImportableStrategyConfig,
    LoggingConfig,
    StrategyConfig,
)
from nautilus_trader.model.orders import MarketOrder
from nautilus_trader.model.enums import OrderSide
from nautilus_trader.core.datetime import dt_to_unix_nanos
from nautilus_trader.model import (
    Bar,
    BarType,
    Venue,
    BarSpecification,
    Quantity,
    PositionId,
)
from nautilus_trader.model.enums import AccountType, OmsType
from nautilus_trader.model.identifiers import InstrumentId, Symbol
from nautilus_trader.model.instruments import Instrument
from nautilus_trader.trading.strategy import Strategy


# Simple Funding Rate Arbitrage
class SFRAConfig(StrategyConfig, frozen=True):
    spot_instrument_id: InstrumentId
    spot_bar_type: BarType
    future_instrument_id: InstrumentId
    future_bar_type: BarType


class SFRAStrategy(Strategy):
    def __init__(self, config: SFRAConfig) -> None:
        super().__init__(config)
        self.spot_instrument: Instrument = None
        self.future_instrument: Instrument = None

    def on_start(self) -> None:
        self.spot_instrument = self.cache.instrument(self.config.spot_instrument_id)
        self.future_instrument = self.cache.instrument(self.config.future_instrument_id)
        self.subscribe_bars(self.config.spot_bar_type)
        self.subscribe_bars(self.config.future_bar_type)
        self.clock.set_time_alert(
            name="MakeOrder",
            alert_time=self.clock.utc_now() + pd.Timedelta(minutes=1),
            callback=self.make_order,
        )
        self.clock.set_timer(
            name="CheckPosition",
            interval=pd.Timedelta(hours=8),
            callback=self.check_position,
        )

    # def on_bar(self, bar: Bar) -> None:
    #     self.log.info(repr(bar), LogColor.CYAN)

    def make_order(self, _: TimeEvent):
        # https://nautilustrader.io/docs/latest/concepts/cache#accessing-market-data
        # 1. get account
        spot_account = self.cache.account_for_venue(BINANCE_VENUE)
        future_account = self.cache.account_for_venue(Venue("BINANCE_FUTURES"))
        # 2. get latest data
        spot_latest_bar = self.cache.bar(self.config.spot_bar_type)
        _ = self.cache.bar(self.config.future_bar_type)
        # 3. calculate quantities
        # In our test the margin for BTCUSDT-PERP is 1, and both cash and margin account have same amount.
        # If you print the instrument, you will see spot and futures has different precisions.
        # So, we will use future instrument to calculate quantities
        spot_qty = self.future_instrument.make_qty(
            spot_account.balance_free(
                self.spot_instrument.get_settlement_currency()
            ).as_decimal()
            / spot_latest_bar.close.as_decimal(),
            round_down=True,
        )  # type decimal
        future_qty = self.future_instrument.make_qty(
            future_account.balance_free(
                self.future_instrument.get_settlement_currency()
            ).as_decimal()
            / spot_latest_bar.close.as_decimal(),
            round_down=True,
        )  # type decimal
        # incase different quantities, we will use the smaller one
        order_qty: Quantity = min(spot_qty, future_qty)
        spot_order: MarketOrder = self.order_factory.market(
            instrument_id=self.config.spot_instrument_id,
            order_side=OrderSide.BUY,
            quantity=self.spot_instrument.make_qty(
                order_qty.as_decimal()
            ),  # make_qty accepts decimal or float
        )
        # Short SELL to receive funding rate
        position_id = PositionId(f"{self.config.future_instrument_id}-SHORT")
        future_order: MarketOrder = self.order_factory.market(
            instrument_id=self.config.future_instrument_id,
            order_side=OrderSide.SELL,
            quantity=order_qty,
        )
        self.submit_order(spot_order)
        self.submit_order(future_order, position_id)

    def check_position(self, _: TimeEvent):
        all_positions = self.cache.positions()
        for position in all_positions:
            self.log.info(repr(position), LogColor.YELLOW)

    def on_stop(self) -> None:
        pass


CATALOG_PATH = Path.cwd() / "catalog_data"


def main():
    start_date = datetime(2025, 10, 1, 0, 0, 0)
    end_date = datetime(2025, 11, 1, 0, 0, 0)
    SPOT_SYMBOL = Symbol("BTCUSDT")
    SPOT_VENUE = BINANCE_VENUE
    FUTURE_SYMBOL = Symbol("BTCUSDT-PERP")
    FUTURE_VENUE = Venue("BINANCE_FUTURES")
    STEP = 1
    AGGREGATION = "MINUTE"
    PRICE_TYPE = "LAST"

    spot_instrument_id = InstrumentId(symbol=SPOT_SYMBOL, venue=SPOT_VENUE)
    spot_bar_type = BarType.from_str(
        f"{spot_instrument_id.value}-{STEP}-{AGGREGATION}-{PRICE_TYPE}-EXTERNAL"
    )
    futures_instrument_id = InstrumentId(symbol=FUTURE_SYMBOL, venue=FUTURE_VENUE)
    futuret_bar_type = BarType.from_str(
        f"{futures_instrument_id.value}-{STEP}-{AGGREGATION}-{PRICE_TYPE}-EXTERNAL"
    )
    bar_spec = BarSpecification.from_str(f"{STEP}-{AGGREGATION}-{PRICE_TYPE}")
    venue_configs = [
        BacktestVenueConfig(
            name=SPOT_VENUE.value,
            oms_type=OmsType.NETTING,
            account_type=AccountType.CASH,
            base_currency=None,
            starting_balances=["10_000 USDT"],
        ),
        BacktestVenueConfig(
            name=FUTURE_VENUE.value,
            oms_type=OmsType.NETTING,
            account_type=AccountType.MARGIN,
            base_currency=None,
            starting_balances=["10_000 USDT"],
        ),
    ]
    start = dt_to_unix_nanos(pd.Timestamp(start_date, tz="UTC"))
    end = dt_to_unix_nanos(pd.Timestamp(end_date, tz="UTC"))
    data_configs = [
        BacktestDataConfig(
            catalog_path=str(CATALOG_PATH),
            data_cls=Bar,
            instrument_id=spot_instrument_id,
            start_time=start,
            end_time=end,
            bar_spec=bar_spec,
        ),
        BacktestDataConfig(
            catalog_path=str(CATALOG_PATH),
            data_cls=Bar,
            instrument_id=futures_instrument_id,
            start_time=start,
            end_time=end,
            bar_spec=bar_spec,
        ),
    ]
    strategies = [
        ImportableStrategyConfig(
            strategy_path=SFRAStrategy.fully_qualified_name(),
            config_path=SFRAConfig.fully_qualified_name(),
            config={
                "spot_instrument_id": spot_instrument_id,
                "spot_bar_type": spot_bar_type,
                "future_instrument_id": futures_instrument_id,
                "future_bar_type": futuret_bar_type,
            },
        ),
    ]
    config = BacktestRunConfig(
        engine=BacktestEngineConfig(
            strategies=strategies,
            logging=LoggingConfig(log_level="INFO"),
        ),
        data=data_configs,
        venues=venue_configs,
        start=start,
        end=end,
        chunk_size=50000,
    )
    node = BacktestNode(configs=[config])

    node.run()
    # check out repots docs https://nautilustrader.io/docs/latest/concepts/reports


if __name__ == "__main__":
    main()
