import unittest

import numpy as np

import chart_patterns as cp


def _series(*segments):
    """(開始値, 終了値, 日数) の区間をつなげて終値の配列を作る。"""
    closes = [segments[0][0]]
    for start, end, days in segments:
        closes.extend(np.linspace(start, end, days + 1)[1:])
    return np.array(closes, dtype=float)


def _ohlv(closes):
    # 高値・安値は終値の±1%、出来高は一定とする
    return closes * 1.01, closes * 0.99, closes, np.full(len(closes), 1000.0)


def _cup_closes():
    return _series(
        (70, 100, 130),  # カップ前の上昇
        (100, 78, 40),   # カップの左側
        (78, 78, 20),    # 底
        (78, 99.5, 40),  # カップの右側
        (99.5, 94, 10),  # 取っ手の押し
        (94, 97, 5),
    )


def _flat_closes():
    up = _series((60, 100, 210))
    rng = np.random.default_rng(0)
    base = 96 + 2.5 * np.sin(np.arange(40) / 3) + rng.uniform(-0.5, 0.5, 40)
    return np.concatenate([up, base, [97.0]])


def _vcp_closes():
    return _series(
        (50, 100, 230),  # 上昇トレンド
        (100, 85, 8),    # 1回目の押し(約17%)
        (85, 98, 8),
        (98, 91, 5),     # 2回目(約9%)
        (91, 97, 5),
        (97, 94, 4),     # 3回目(約5%)
        (94, 96, 3),
    )


class CupWithHandleTest(unittest.TestCase):
    def test_detects_forming_cup(self):
        h, l, c, v = _ohlv(_cup_closes())
        res = cp.detect_cup_with_handle(h, l, c, v, len(c) - 1)
        self.assertIsNotNone(res)
        self.assertEqual(res["status"], "forming")
        self.assertAlmostEqual(res["pivot"], 99.5 * 1.01, places=6)
        self.assertEqual([m[2] for m in res["markers"]], ["左の高値", "底", "右の高値"])

    def test_detects_breakout_on_first_close_above_pivot(self):
        h, l, c, v = _ohlv(np.append(_cup_closes(), 102.0))
        res = cp.detect_cup_with_handle(h, l, c, v, len(c) - 1)
        self.assertEqual(res["status"], "breakout")

    def test_v_shaped_bottom_is_rejected(self):
        # 底の手前で急落し、すぐ急反発する(底にとどまらない)
        closes = _series(
            (70, 100, 130), (100, 90, 35), (90, 78, 15), (78, 90, 15), (90, 99.5, 35), (99.5, 94, 10), (94, 97, 5)
        )
        h, l, c, v = _ohlv(closes)
        self.assertIsNone(cp.detect_cup_with_handle(h, l, c, v, len(c) - 1))

    def test_too_deep_cup_is_rejected(self):
        closes = _series((70, 100, 130), (100, 55, 40), (55, 55, 20), (55, 99.5, 40), (99.5, 94, 10), (94, 97, 5))
        h, l, c, v = _ohlv(closes)
        self.assertIsNone(cp.detect_cup_with_handle(h, l, c, v, len(c) - 1))


class FlatBaseTest(unittest.TestCase):
    def test_detects_flat_base(self):
        h, l, c, v = _ohlv(_flat_closes())
        v[-30:] = 600.0  # 横ばいの間に出来高が細る
        res = cp.detect_flat_base(h, l, c, v, len(c) - 1)
        self.assertIsNotNone(res)
        self.assertEqual(res["status"], "forming")
        # ベースの開始は上昇途中ではなく、高値圏に届いた日以降
        self.assertGreaterEqual(h[res["base_start"]], res["pivot"] * 0.95)

    def test_steady_uptrend_is_not_flat(self):
        h, l, c, v = _ohlv(_series((60, 140, 300)))
        v[-30:] = 600.0
        self.assertIsNone(cp.detect_flat_base(h, l, c, v, len(c) - 1))

    def test_price_pinned_by_tender_offer_is_rejected(self):
        # 急騰後に値動きがほぼ止まる(TOB価格に張り付く)ケース
        closes = np.concatenate([_series((60, 70, 210)), np.full(40, 100.0), [100.2]])
        h, l, c, v = closes * 1.002, closes * 0.998, closes, np.full(len(closes), 1000.0)
        v[-30:] = 600.0
        self.assertIsNone(cp.detect_flat_base(h, l, c, v, len(c) - 1))

    def test_flat_without_volume_dry_up_is_rejected(self):
        h, l, c, v = _ohlv(_flat_closes())
        self.assertIsNone(cp.detect_flat_base(h, l, c, v, len(c) - 1))


class VcpTest(unittest.TestCase):
    def test_detects_three_contractions(self):
        h, l, c, v = _ohlv(_vcp_closes())
        res = cp.detect_vcp(h, l, c, v, len(c) - 1)
        self.assertIsNotNone(res)
        self.assertEqual(res["status"], "forming")
        self.assertTrue(res["details"][0][1].startswith("3回"))
        self.assertAlmostEqual(res["pivot"], 97 * 1.01, places=6)

    def test_widening_pullbacks_are_rejected(self):
        closes = _series((50, 100, 230), (100, 95, 6), (95, 99, 6), (99, 88, 8), (88, 97, 8))
        h, l, c, v = _ohlv(closes)
        self.assertIsNone(cp.detect_vcp(h, l, c, v, len(c) - 1))

    def test_downtrend_is_rejected(self):
        h, l, c, v = _ohlv(_series((150, 100, 230), (100, 85, 8), (85, 98, 8), (98, 91, 5), (91, 97, 5)))
        self.assertIsNone(cp.detect_vcp(h, l, c, v, len(c) - 1))


class ContractionLegsTest(unittest.TestCase):
    def test_highs_fall_and_lows_rise(self):
        h, l, _, _ = _ohlv(_vcp_closes())
        start = int(np.argmax(h[:-5]))
        legs = cp.contraction_legs(h, l, start, len(h) - 1, 2.5)
        highs = [x[1] for x in legs]
        lows = [x[3] for x in legs]
        self.assertEqual(highs, sorted(highs, reverse=True))
        self.assertEqual(lows, sorted(lows))


if __name__ == "__main__":
    unittest.main()
