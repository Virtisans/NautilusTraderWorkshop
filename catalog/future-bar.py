from nautilus_trader.persistence.catalog import ParquetDataCatalog
from pathlib import Path
from nautilus_trader.model.instruments import Instrument
from nautilus_trader.adapters.binance.common.enums import BinanceAccountType
from nautilus_trader.adapters.binance import (
    get_cached_binance_http_client,
)
from nautilus_trader.adapters.binance.futures.providers import (
    BinanceFuturesInstrumentProvider,
)
from nautilus_trader.persistence.wranglers import BarDataWrangler
from nautilus_trader.config import InstrumentProviderConfig
from nautilus_trader.common.component import LiveClock
from nautilus_trader.model import Symbol, Venue, InstrumentId, BarType
import pandas as pd

# 1. Update following variables
SYMBOL = Symbol("BTCUSDT-PERP")
VENUE = Venue("BINANCE_FUTURES")
STEP = 1
AGGREGATION = "MINUTE"  # see here https://nautilustrader.io/docs/latest/concepts/data#aggregation-methods
PRICE_TYPE = "LAST"  # usually you do not need to change this

API_KEY = ""
API_SECRET = ""

parent_dir = Path(__file__).resolve().parent.parent
CATALOG_PATH = parent_dir / "catalog_data"
CSV_PATH = parent_dir / "binance-csv" / "future"

# Create a new catalog instance
catalog = ParquetDataCatalog(CATALOG_PATH)


def getInstrument(instrument_id: InstrumentId):
    clock = LiveClock()
    client = get_cached_binance_http_client(
        clock=clock,
        account_type=BinanceAccountType.USDT_FUTURES,
        api_key=API_KEY,
        api_secret=API_SECRET,
        is_testnet=False,
    )
    binance_provider = BinanceFuturesInstrumentProvider(
        client=client,
        clock=clock,
        config=InstrumentProviderConfig(
            load_all=False,
        ),
        venue=Venue("BINANCE_FUTURES"),
    )
    binance_provider.load(instrument_id)
    return binance_provider.find(instrument_id)


def write_catalog(filepath: Path, bar_type: BarType, instrument: Instrument):
    wrangler = BarDataWrangler(bar_type, instrument)
    files = []
    if filepath.is_file():
        files.append(filepath)
    else:
        files = list(filepath.glob("*.csv"))
    for file in files:
        if instrument.raw_symbol.root() in file.name:
            df = pd.read_csv(
                filepath_or_buffer=file,
                dtype={
                    "open_time": "int64",
                    "open": "float64",
                    "high": "float64",
                    "low": "float64",
                    "close": "float64",
                    "volume": "float64",
                    "close_time": "int64",
                    "quote_volume": "float64",
                    "count": "int64",
                    "taker_buy_volume": "float64",
                    "taker_buy_quote_volume": "float64",
                    "ignore": "int64",
                },
            )
            df["timestamp"] = pd.to_datetime(df["close_time"], unit="ms", utc=True)
            df = df.set_index("timestamp")
            df = df.drop(
                columns=[
                    "open_time",
                    "close_time",
                    "quote_volume",
                    "count",
                    "taker_buy_volume",
                    "taker_buy_quote_volume",
                    "ignore",
                ]
            )
            bar_data = wrangler.process(df)
            catalog.write_data(bar_data)
    catalog.write_data([instrument])


if __name__ == "__main__":
    instrument_id = InstrumentId(SYMBOL, VENUE)
    bar_type = BarType.from_str(
        f"{instrument_id.value}-{STEP}-{AGGREGATION}-{PRICE_TYPE}-EXTERNAL"
    )
    instrument = getInstrument(instrument_id)
    write_catalog(CSV_PATH, bar_type, instrument)
