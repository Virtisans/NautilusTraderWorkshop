import types
from datetime import datetime
from pathlib import Path
import requests
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
from nautilus_trader.common.events import TimeEvent
from nautilus_trader.config import (
    ImportableStrategyConfig,
    LoggingConfig,
    StrategyConfig,
)
from nautilus_trader.core.datetime import dt_to_unix_nanos
from nautilus_trader.model import (
    Bar,
    BarSpecification,
    BarType,
)
from nautilus_trader.model.enums import AccountType, OmsType
from nautilus_trader.model.identifiers import InstrumentId, Symbol
from nautilus_trader.model.instruments import Instrument
from nautilus_trader.trading.strategy import Strategy

# Signal names for price extremes
signals = types.SimpleNamespace()
signals.PRICE = "CURRENT_PRICE"


class AnotherConfig(StrategyConfig, frozen=True):
    webhook_url: str


class AnotherStrategy(Strategy):
    def __init__(self, config: AnotherConfig) -> None:
        super().__init__(config)

    def on_start(self) -> None:
        self.subscribe_signal(signals.PRICE)

    def on_signal(self, signal):
        print("Another Strategy on_signal:", signal.value, self.config.webhook_url)
        res = requests.post(
            self.config.webhook_url,
            json={"msg_type": "text", "content": {"text": signal.value}},
        )
        print(res.content)

    def on_stop(self) -> None:
        pass


class NotificationConfig(StrategyConfig, frozen=True):
    spot_instrument_id: InstrumentId
    spot_bar_type: BarType


class NotificationStrategy(Strategy):
    def __init__(self, config: NotificationConfig) -> None:
        super().__init__(config)
        self.spot_instrument: Instrument = None

    def on_start(self) -> None:
        self.spot_instrument = self.cache.instrument(self.config.spot_instrument_id)
        self.subscribe_signal(signals.PRICE)
        self.clock.set_timer(
            name="Notify_Feishu",
            interval=pd.Timedelta(hours=2),
            callback=self.send_signal,
        )

    def send_signal(self, timeEvent: TimeEvent):
        last_bar = self.cache.bar(self.config.spot_bar_type, index=0)
        self.publish_signal(
            name=signals.PRICE,
            value=f"Symbol:{last_bar.bar_type.instrument_id.value} Price:{last_bar.close} TIME:{last_bar.ts_event} From: NotificationStrategy",  # Using same string as name for simplicity
            ts_event=timeEvent.ts_event,
        )

    def on_stop(self) -> None:
        pass


CATALOG_PATH = Path.cwd() / "catalog_data"


def main():
    start_date = datetime(2025, 10, 1, 0, 0, 0)
    end_date = datetime(2025, 10, 1, 10, 0, 0)
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
    ]
    strategies = [
        ImportableStrategyConfig(
            strategy_path=NotificationStrategy.fully_qualified_name(),
            config_path=NotificationConfig.fully_qualified_name(),
            config={
                "spot_instrument_id": spot_instrument_id,
                "spot_bar_type": spot_bar_type,
            },
        ),
        ImportableStrategyConfig(
            strategy_path=AnotherStrategy.fully_qualified_name(),
            config_path=AnotherConfig.fully_qualified_name(),
            config={
                "webhook_url": "Your WEBHOOK URL",
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


if __name__ == "__main__":
    main()
