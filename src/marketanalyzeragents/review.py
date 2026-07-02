from __future__ import annotations

from typing import Any

from .portfolio_store import PortfolioStore


def build_daily_review(store: PortfolioStore, market: str, day: str) -> dict[str, Any]:
    entries = []
    for operation in store.operations_for_date(market, day):
        suggestion = store.latest_suggestion_before(market, operation["symbol"], operation["operated_at"])
        first_after = store.first_quote_after(market, operation["symbol"], operation["operated_at"])
        later = store.recent_quotes(market, operation["symbol"], 1)
        first_after_price = float(first_after["price"]) if first_after else None
        later_price = float(later[0]["price"]) if later else None
        first_after_return_pct = (
            round((first_after_price / float(operation["price"]) - 1) * 100, 4)
            if first_after_price is not None and operation["price"]
            else None
        )
        return_pct = (
            round((later_price / float(operation["price"]) - 1) * 100, 4)
            if later_price is not None and operation["price"]
            else None
        )
        entries.append(
            {
                "operation": dict(operation),
                "available_suggestion": dict(suggestion) if suggestion else None,
                "first_after_price": first_after_price,
                "first_after_return_pct": first_after_return_pct,
                "latest_price": later_price,
                "subsequent_return_pct": return_pct,
                "process_note": (
                    "操作前存在可追溯建议；应检查是否遵守其证据与失效条件。"
                    if suggestion else "操作前没有可追溯建议，无法评估建议执行一致性。"
                ),
            }
        )
    return {"market": market, "date": day, "operations": entries}
