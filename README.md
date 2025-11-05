# Nautilus Trader Workshop

## How to use?
1. Using binance-public-data to download data and saving all csv files into binance-csv. Remember to separate spot and future data.
2. Run `catalog/bar.py`(if the data type is kline) to save the data into catalog. If you want to know the logics behind catalog, please refer to `notebooks/Data-Catalog.ipynb`.
    - Binance spot testnet doesn't support `sapi` which is used in `BinanceSpotInstrumentProvider`. You need to apply your own keys on Binance.
    - If you only want to test BTCUSDT or ETHUSDT, using `TestInstrumentProvider`


## Notebooks

1. [Data Catalog](notebooks/Data-Catalog.ipynb)