"""tests for make_mgp_race.py's pass detection and places (python -m unittest test_make_mgp_race -v, from tools/mgp)"""
import unittest
import numpy as np
from make_mgp_race import crossings, place_order

GATE = dict(centre=[0, 0, 0], normal=[0, 0, 1], width_dir=[1, 0, 0], half_width=1.0, height=2.0)
FPS = 30


def line(z0, z1, x=0.0, y=1.0, n=None):              # a straight path along the gate normal at 10 m/s
    n = n or int(abs(z1 - z0) / 10 * FPS) + 1
    return np.c_[np.full(n, x), np.full(n, y), np.linspace(z0, z1, n)]


class CrossingsTest(unittest.TestCase):
    def test_forward(self): self.assertAlmostEqual(crossings(line(-10, 10), FPS, GATE, 2.5, 10)[0], 1.0, places=2)
    def test_backward(self): self.assertEqual(crossings(line(10, -10), FPS, GATE, 2.5, 10), [])
    def test_beside_the_margin(self): self.assertEqual(crossings(line(-10, 10, x=3.6), FPS, GATE, 2.5, 10), [])
    def test_inside_the_margin(self): self.assertEqual(len(crossings(line(-10, 10, x=3.4), FPS, GATE, 2.5, 10)), 1)
    def test_below(self): self.assertEqual(crossings(line(-10, 10, y=-1.5), FPS, GATE, 2.5, 10), [])

    def test_wobble_counts_once(self):                # through, back and through again within min_gap
        p = np.r_[line(-10, 1), line(1, -1), line(-1, 10)]
        self.assertEqual(len(crossings(p, FPS, GATE, 2.5, 10)), 1)

    def test_finish_reached_past_the_last_frame(self):  # the path stops 1 m short (the finish card), still moving at 10 m/s
        p = np.r_[line(-10, -1, n=28), np.repeat([[0, 1, -1]], 30, axis=0)]
        self.assertEqual(crossings(p, FPS, GATE, 2.5, 10), [])
        self.assertAlmostEqual(crossings(p, FPS, GATE, 2.5, 10, last=27)[0], 1.0, places=1)

    def test_finish_too_far_to_reach(self):           # 0.6 s short at 10 m/s: more than END_REACH
        p = np.r_[line(-10, -6, n=13), np.repeat([[0, 1, -6]], 30, axis=0)]
        self.assertEqual(crossings(p, FPS, GATE, 2.5, 10, last=12), [])


class PlaceOrderTest(unittest.TestCase):
    OFF = {"A": [20, 20, 20], "B": [21, 21, 21], "C": [19, 19], "D": [22, 22, 22]}

    def test_by_laps_then_finish(self):
        cv = {"A": [5, 25, 45, 66], "B": [5, 26, 47, 68], "C": [5, 24, 43], "D": [5, 27, 49, 70]}
        self.assertEqual(place_order(list(self.OFF), self.OFF, cv), ["A", "B", "D", "C"])

    def test_no_path_starts_with_the_others(self):    # D has no pose file: 5 + 66 = 71, not 0 + 66 (first of the 3-lap pilots)
        cv = {"A": [5, 25, 45, 66], "B": [5, 26, 47, 68], "C": [5, 24, 43]}
        self.assertEqual(place_order(["D", "A", "B", "C"], self.OFF, cv), ["A", "B", "D", "C"])
        fast = dict(self.OFF, D=[19, 19, 19])            # a faster pilot with no path still places by its official total
        self.assertEqual(place_order(["A", "B", "C", "D"], fast, cv), ["D", "A", "B", "C"])


    def test_missing_finish_uses_holeshot_plus_official_total(self):   # B's last pass is missing: 5 + 63 = 68 > A's 66
        cv = {"A": [5, 25, 45, 66], "B": [5, 26, 47], "C": [5, 24, 43], "D": [5, 27, 49, 70]}
        self.assertEqual(place_order(list(self.OFF), self.OFF, cv), ["A", "B", "D", "C"])


if __name__ == "__main__":
    unittest.main()
