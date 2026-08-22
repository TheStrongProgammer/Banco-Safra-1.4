import unittest

from src.market_service import MarketService


class SafetyLimitsTests(unittest.TestCase):
    def test_market_price_change_is_capped_at_1000(self) -> None:
        capped = MarketService._cap_price_change(100_000.0, 102_500.0)

        self.assertEqual(capped, 101_000.0)


if __name__ == "__main__":
    unittest.main()
