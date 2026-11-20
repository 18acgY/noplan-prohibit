"""A-share market data through akshare.

akshare scrapes the Chinese public finance sites (Eastmoney, Sina), which carry
the coverage Yahoo Finance lacks for A-shares — notably company news and
quarterly statements. It needs no API key and no outbound proxy.

Symbol translation lives in ``symbols``; the vendor functions live in ``market``,
``news`` and ``fundamentals``.
"""