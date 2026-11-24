"""Resolve an akshare interface across the renames it ships in minor releases.

akshare 在 1.20.41 / 1.20.46 两批改名里直接删掉了旧函数名（不是保留别名）：

    1.20.41  fund_etf_fund_info_em   -> fund_exchange_nav_hist_em   (ETF 净值)
    1.20.46  stock_zh_a_hist         -> stock_zh_a_hist_em          (A股·东财)
    1.20.46  stock_zh_a_daily        -> stock_zh_a_hist_sina        (A股·新浪)

引擎里写死旧名时，装上新版就会在运行期抛 AttributeError；ETF 净值是主路径，
没有兜底，整次分析直接以退出码 1 结束。所以取函数时按「新名 → 旧名」依次尝试，
两边都没有才报错，并把试过的名字和已装版本一起写进错误里。

新增改名时，把新名放第一个参数、旧名放后面即可。
"""

from __future__ import annotations

from typing import Any, Callable

from tradingagents.dataflows.errors import VendorUnavailableError


def resolve(name: str, *fallbacks: str) -> Callable[..., Any]:
    """Return the first akshare function that exists among ``name`` and ``fallbacks``."""
    import akshare as ak

    for candidate in (name, *fallbacks):
        function = getattr(ak, candidate, None)
        if callable(function):
            return function

    tried = ", ".join((name, *fallbacks))
    version = getattr(ak, "__version__", "unknown")
    raise VendorUnavailableError(
        f"akshare {version} provides none of: {tried}"
    )