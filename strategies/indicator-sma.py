from collections import deque
from datetime import datetime
from pathlib import Path

import pandas as pd
from nautilus_trader.adapters.binance import (
    BINANCE_VENUE,
)
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
from nautilus_trader.core.datetime import dt_to_unix_nanos

# Import system defined indicators
# https://nautilustrader.io/docs/latest/api_reference/indicators
from nautilus_trader.indicators import SimpleMovingAverage
from nautilus_trader.model import (
    Bar,
    BarSpecification,
    BarType,
    InstrumentId,
    Symbol,
)
from nautilus_trader.model.enums import AccountType, OmsType, OrderSide
from nautilus_trader.model.instruments import Instrument
from nautilus_trader.model.orders import MarketOrder
from nautilus_trader.trading import Strategy

# NOTE:
# 1. In order to test bars aggravation, I will use 1 min golden cross to buy, 3 mins death cross to sell.
# 2. We need to cache indicator values ourself. We can use cache.add or a property of strategy. In this example, we use strategy property and deque(same in cache).


class SMACrossConfig(StrategyConfig, frozen=True):
    spot_instrument_id: InstrumentId
    spot_bar_type: BarType
    sma_period_10: int
    sma_period_100: int


class SMACrossStrategy(Strategy):
    def __init__(self, config: SMACrossConfig) -> None:
        super().__init__(config)
        self.spot_instrument: Instrument = None
        # 1. initialize indicators
        self.sma_10 = SimpleMovingAverage(period=self.config.sma_period_10)
        self.sma_10_values = deque(maxlen=self.config.sma_period_10)
        self.sma_100 = SimpleMovingAverage(period=self.config.sma_period_100)
        self.sma_100_values = deque(maxlen=self.config.sma_period_100)
        self.agg_sma_10 = SimpleMovingAverage(period=self.config.sma_period_10)
        self.agg_sma_10_values = deque(maxlen=self.config.sma_period_10)
        self.agg_sma_100 = SimpleMovingAverage(period=self.config.sma_period_100)
        self.agg_sma_100_values = deque(maxlen=self.config.sma_period_100)

        # NOTE：
        # 2. aggravation bar type
        # 2.1 In the BayType string, 1-MINUTE-EXTERNAL as source bars, that can be either INTERNAL or EXTERNAL
        # 2.2 In the BayType string, BTCUSDT.BINANCE-5-MINUTE-LAST-INTERNAL as target bars, that always be INTERNAL
        self.min_3_bar_type = BarType.from_str(
            "BTCUSDT.BINANCE-5-MINUTE-LAST-INTERNAL@1-MINUTE-EXTERNAL"
        )

    def on_start(self) -> None:
        # BUG: Request bars not update indicators.
        # NOTE: https://nautilustrader.io/docs/latest/concepts/data/#common-pitfalls
        # 3. register indicators. Extends from Actor
        # https://nautilustrader.io/docs/latest/api_reference/trading#register_indicator_for_barsself-bartype-bar_type-indicator-indicator--void
        self.register_indicator_for_bars(self.config.spot_bar_type, self.sma_10)
        self.register_indicator_for_bars(self.config.spot_bar_type, self.sma_100)
        self.register_indicator_for_bars(self.min_3_bar_type, self.agg_sma_10)
        self.register_indicator_for_bars(self.min_3_bar_type, self.agg_sma_100)
        # NOTE: Avoiding request large data. Since there maybe a gap between historical data and live data, the results of indicators will be wrong.
        # 4. request historical data.
        # https://nautilustrader.io/docs/latest/api_reference/trading#request_barsself-bartype-bar_type-datetime-start-datetime-endnone-int-limit0-clientid-client_idnone-callback-callableuuid4-none--none--none-update_catalog-bool--false-dict-paramsnone--uuid4
        self.request_bars(
            bar_type=self.config.spot_bar_type,
            start=self.clock.utc_now() - pd.Timedelta(days=5),
        )
        self.request_bars(
            bar_type=self.min_3_bar_type,
            start=self.clock.utc_now() - pd.Timedelta(days=5),
        )
        # 5. subscribe
        self.spot_instrument = self.cache.instrument(self.config.spot_instrument_id)
        self.subscribe_bars(self.config.spot_bar_type)
        self.subscribe_bars(self.min_3_bar_type)

    def on_historical_data(self, data):
        # Processes batches of historical bars from request_bars()
        # Note: indicators registered with register_indicator_for_bars
        # are updated automatically with historical data
        print(self.clock.utc_now())
        print(data)

    def on_bar(self, bar: Bar) -> None:
        # Since one of the sma's peroids is 100, and also agg 5 mins bars, wait until indicators are ready
        # indicators_initialized is helpful to check if indicators are ready to use.
        if not self.indicators_initialized():
            return
        # check if 1 min smas cross
        if bar.bar_type == self.config.spot_bar_type:
            self.sma_10_values.append(self.sma_10.value)
            self.sma_100_values.append(self.sma_100.value)
            if len(self.sma_10_values) < 2:
                return
            # we can use is_flat to check there is positions
            if self.portfolio.is_flat(self.config.spot_instrument_id):
                if (
                    self.sma_10_values[-2] < self.sma_100_values[-2]
                    and self.sma_10_values[-1] >= self.sma_100_values[-1]
                ):
                    self.buy()

        if bar.bar_type == BarType.from_str("BTCUSDT.BINANCE-5-MINUTE-LAST-INTERNAL"):
            self.agg_sma_10_values.append(self.agg_sma_10.value)
            self.agg_sma_100_values.append(self.agg_sma_100.value)
            if len(self.sma_10_values) < 2:
                return
            if not self.portfolio.is_flat(self.config.spot_instrument_id):
                if (
                    self.agg_sma_10_values[-2] < self.agg_sma_100_values[-2]
                    and self.agg_sma_10_values[-1] >= self.agg_sma_100_values[-1]
                ):
                    self.sell()

    def buy(self):
        order: MarketOrder = self.order_factory.market(
            instrument_id=self.config.spot_instrument_id,
            order_side=OrderSide.BUY,
            quantity=self.spot_instrument.make_qty(
                0.01
            ),  # make_qty accepts decimal or float
        )
        print(order)
        self.submit_order(order)

    def sell(self):
        order: MarketOrder = self.order_factory.market(
            instrument_id=self.config.spot_instrument_id,
            order_side=OrderSide.SELL,
            quantity=self.spot_instrument.make_qty(
                0.01
            ),  # make_qty accepts decimal or float
        )
        print(order)
        self.submit_order(order)

    def on_stop(self) -> None:
        pass


CATALOG_PATH = Path.cwd() / "catalog_data"


def main():
    start_date = datetime(2025, 10, 10, 0, 0, 0)
    end_date = datetime(2025, 10, 11, 0, 0, 0)
    SPOT_SYMBOL = Symbol("BTCUSDT")
    SPOT_VENUE = BINANCE_VENUE
    STEP = 1
    AGGREGATION = "MINUTE"
    PRICE_TYPE = "LAST"

    spot_instrument_id = InstrumentId(symbol=SPOT_SYMBOL, venue=SPOT_VENUE)
    spot_bar_type = BarType.from_str(
        f"{spot_instrument_id.value}-{STEP}-{AGGREGATION}-{PRICE_TYPE}-EXTERNAL"
    )

    bar_spec = BarSpecification.from_str(f"{STEP}-{AGGREGATION}-{PRICE_TYPE}")
    venue_configs = [
        BacktestVenueConfig(
            name=SPOT_VENUE.value,
            oms_type=OmsType.NETTING,
            account_type=AccountType.CASH,
            base_currency=None,
            starting_balances=["10_000 USDT"],
        )
    ]
    start = dt_to_unix_nanos(pd.Timestamp(start_date, tz="UTC"))
    end = dt_to_unix_nanos(pd.Timestamp(end_date, tz="UTC"))
    data_configs = [
        BacktestDataConfig(
            catalog_path=str(CATALOG_PATH),
            data_cls=Bar,
            instrument_id=spot_instrument_id,
            start_time=dt_to_unix_nanos(
                pd.Timestamp(datetime(2025, 10, 1, 0, 0, 0), tz="UTC")
            ),
            end_time=dt_to_unix_nanos(
                pd.Timestamp(datetime(2025, 10, 31, 0, 0, 0), tz="UTC")
            ),
            bar_spec=bar_spec,
        )
    ]
    strategies = [
        ImportableStrategyConfig(
            strategy_path=SMACrossStrategy.fully_qualified_name(),
            config_path=SMACrossConfig.fully_qualified_name(),
            config={
                "spot_instrument_id": spot_instrument_id,
                "spot_bar_type": spot_bar_type,
                "sma_period_10": 10,
                "sma_period_100": 100,
            },
        ),
    ]
    config = BacktestRunConfig(
        engine=BacktestEngineConfig(
            strategies=strategies,
            logging=LoggingConfig(log_level="WARNING"),
        ),
        data=data_configs,
        venues=venue_configs,
        start=start,
        end=end,
        chunk_size=50000,  # ！！！！！！you really should set this, unless you have enough memory.
    )
    node = BacktestNode(configs=[config])
    node.run()
    # check out repots docs https://nautilustrader.io/docs/latest/concepts/reports


if __name__ == "__main__":
    main()
