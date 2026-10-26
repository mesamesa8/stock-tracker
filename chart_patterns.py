"""
チャートパターン(ベース)の機械的な判定。

対象の型:
  - カップウィズハンドル(cup_with_handle): 3〜6ヶ月のカップ + 5〜10%の小さな押し(取っ手)
  - フラットベース(flat_base): 上昇の後、高値圏で値幅15%以内の横ばいが5週以上続く
  - VCP(vcp): 押し目の深さが回を追うごとに浅くなる(例: 20% → 10% → 5%)

各 detect_* 関数は、1銘柄分の日足配列(古い順)と判定日の位置 t を受け取り、
t日目の終値時点で型ができていれば dict を、できていなければ None を返す。
t日目より後のデータは使わない(未来の情報を使わない)。

返す dict の共通項目:
  status        : "forming"(形成中: ピボット未突破) / "breakout"(当日の終値でピボットを初めて上抜け)
  pivot         : ピボット(ブレイクの目安となる価格)
  base_start    : ベースの開始位置(配列の添字)
  markers       : チャートに印を付ける点 [(添字, 価格, ラベル), ...]
  details       : 画面に出す説明 [(項目名, 値の文字列), ...]
  vol_dry_ratio : 直近の出来高 ÷ それ以前の平均出来高(1未満なら出来高が細っている)
"""

from __future__ import annotations

import numpy as np

# 判定条件(下の定数)を変えたら上げる。過去の判定結果ファイルは、版が違うと作り直される
RULE_VERSION = 2

# --- カップウィズハンドル(ユーザー提供の解説図「カップウィズハンドル解説(週足)」の条件を日足に置き換え) ---
CUP_PRIOR_RISE_MIN = 30.0       # カップの前の上昇率(%)
CUP_DEPTH = (12.0, 33.0)        # 左の高値からカップの底までの下落率(%)
CUP_DAYS = (65, 130)            # ベースの幅 = 左の高値〜右の高値(3〜6ヶ月)
CUP_BOTTOM_VOL_DAYS = 10        # 底の前後この日数(1〜2週間)の出来高が、カップ全体の平均より少ない
RIGHT_LIP_VS_LEFT = (-8.0, 3.0)  # 右の高値が左の高値に対して何%の位置か(図: ほとんどの場合左より安い)
HANDLE_DAYS = (5, 25)           # 取っ手の長さ(図: 1〜2週間以上。上限は約5週)
HANDLE_DEPTH = (5.0, 10.0)      # 右の高値から取っ手の安値までの下落率(%)
HANDLE_MA_DAYS = 50             # 取っ手の間、終値がこの日数の移動平均(10週線)より上
BREAKOUT_VOL_RATIO = 1.4        # ブレイク日の出来高 ÷ 直前50日の平均出来高(図: 40〜50%以上の急増)

# 以前使っていたが、2026-10 にユーザーの判断で外した条件。True にすると元に戻る
CUP_REQUIRE_U_SHAPE = False     # 底が中央寄り(20〜80%の位置)で、下側1/3に35%以上の日数とどまる(V字を除外)
CUP_MIN_LOWER_THIRD = 0.35
CUP_REQUIRE_HANDLE_UPPER_HALF = False  # 取っ手の安値がカップの上半分にある

# --- フラットベース ---
FLAT_DAYS = (25, 65)            # ベースの長さ(5〜13週)
FLAT_MAX_DEPTH = 10.0           # ベース内の値幅(%)
FLAT_MIN_DEPTH = 3.0            # これより値幅が小さいのは、TOB等で値動きが止まった銘柄とみなす
FLAT_NEAR_52W_HIGH = 5.0        # ベースの高値が52週高値から何%以内か
FLAT_PRIOR_RISE_MIN = 30.0      # ベースの前の上昇率(%)
FLAT_MIN_DAYS_SINCE_TOP = 15    # ベースの高値付近に初めて届いてから経過した最短日数
FLAT_MAX_VOL_RATIO = 1.0        # 直近10日の出来高 ÷ ベース前50日の平均 がこれ未満

# --- VCP(ミネルヴィニの目安) ---
VCP_BASE_DAYS = (20, 130)       # ベースの長さ(4〜26週)
VCP_MIN_DEPTH = 2.5             # これ未満の押しは収縮として数えない(%)
VCP_MIN_LEG_DAYS = 3            # 1回の押しにかかる最短日数
VCP_FIRST_DEPTH = (8.0, 40.0)   # 最初の押し目の深さ(%)
VCP_LAST_MAX_DEPTH = 10.0       # 最後の押し目の深さの上限(%)
VCP_MIN_CONTRACTIONS = 2


def _vol_ratio(v: np.ndarray, recent: slice, before: slice) -> float:
    base = v[before].mean() if len(v[before]) else np.nan
    if not base or np.isnan(base):
        return float("nan")
    return float(v[recent].mean() / base)


def _status(c: np.ndarray, t: int, pivot: float) -> str | None:
    """当日終値でピボットを初めて上抜けたら breakout。前日までに上抜け済みなら対象外(None)。"""
    if c[t - 1] > pivot:
        return None
    return "breakout" if c[t] > pivot else "forming"


def _prior_rise(l: np.ndarray, start: int, peak: float, lookback: int = 120) -> float | None:
    pre = l[max(0, start - lookback):start]
    if len(pre) < 40:
        return None
    return (peak / pre.min() - 1) * 100


def detect_cup_with_handle(h, l, c, v, t: int) -> dict | None:
    # 右の高値(取っ手の始まり)= t-25〜t-5日の最高値
    lo_i, hi_i = t - HANDLE_DAYS[1], t - HANDLE_DAYS[0] + 1
    if lo_i < 60:
        return None
    r = lo_i + int(np.argmax(h[lo_i:hi_i]))
    R = h[r]
    if h[r + 1:t].max(initial=0) > R:
        return None  # 取っ手の途中で右の高値を超えていたら、取っ手ではない
    handle_low = l[r + 1:t + 1].min()
    handle_depth = (1 - handle_low / R) * 100
    if not (HANDLE_DEPTH[0] <= handle_depth <= HANDLE_DEPTH[1]):
        return None

    # 左の高値 = 右の高値から CUP_DAYS だけ遡った範囲の最高値
    a, b = max(0, r - CUP_DAYS[1]), r - CUP_DAYS[0] + 1
    if b - a < 10:
        return None
    left = a + int(np.argmax(h[a:b]))
    L = h[left]
    if h[left + 1:r].max(initial=0) > max(L, R):
        return None
    lip = (R / L - 1) * 100
    if not (RIGHT_LIP_VS_LEFT[0] <= lip <= RIGHT_LIP_VS_LEFT[1]):
        return None
    bottom = left + int(np.argmin(l[left:r + 1]))
    B = l[bottom]
    depth = (1 - B / L) * 100
    if not (CUP_DEPTH[0] <= depth <= CUP_DEPTH[1]):
        return None

    cup_len = r - left
    if CUP_REQUIRE_U_SHAPE:
        if not (0.2 <= (bottom - left) / cup_len <= 0.8):
            return None
        if (l[left:r + 1] <= B + (L - B) / 3).mean() < CUP_MIN_LOWER_THIRD:
            return None
    if CUP_REQUIRE_HANDLE_UPPER_HALF and handle_low < B + (L - B) / 2:
        return None
    rise = _prior_rise(l, left, L)
    if rise is None or rise < CUP_PRIOR_RISE_MIN:
        return None

    # 底の付近(1〜2週間)は出来高が減っている
    half = CUP_BOTTOM_VOL_DAYS // 2
    bottom_vol = _vol_ratio(v, slice(max(left, bottom - half), min(r, bottom + half)), slice(left, r + 1))
    if not bottom_vol < 1:
        return None
    # 取っ手の間は10週線より上で推移し、出来高が減っている
    if t < HANDLE_MA_DAYS + 1:
        return None
    for i in range(r + 1, t + 1):
        if c[i] < c[i - HANDLE_MA_DAYS + 1:i + 1].mean():
            return None
    handle_vol = _vol_ratio(v, slice(r + 1, t), slice(max(0, r - 50), r))
    if not handle_vol < 1:
        return None

    status = _status(c, t, R)
    if status is None:
        return None
    breakout_vol = _vol_ratio(v, slice(t, t + 1), slice(max(0, t - 50), t))
    if status == "breakout" and not breakout_vol >= BREAKOUT_VOL_RATIO:
        return None  # 出来高を伴わない上抜けはブレイクとみなさない
    details = [
        ("カップ", f"{cup_len / 5:.0f}週・深さ{depth:.0f}%"),
        ("底の出来高", f"{bottom_vol:.2f}倍"),
        ("取っ手", f"{t - r}日・押し{handle_depth:.0f}%"),
        ("カップ前の上昇", f"+{rise:.0f}%"),
    ]
    if status == "breakout":
        details.append(("ブレイク日の出来高", f"{breakout_vol:.2f}倍"))
    return {
        "status": status,
        "pivot": float(R),
        "base_start": left,
        "markers": [(left, float(L), "左の高値"), (bottom, float(B), "底"), (r, float(R), "右の高値")],
        "details": details,
        "vol_dry_ratio": handle_vol,
    }


def detect_flat_base(h, l, c, v, t: int) -> dict | None:
    if t < 250:
        return None
    high_52w = h[t - 250:t].max()
    # 前日までの直近 n 日がベース。条件を満たす最長の n を採用する
    for n in range(FLAT_DAYS[1], FLAT_DAYS[0] - 1, -1):
        s = t - n
        top = h[s:t].max()
        depth = (1 - l[s:t].min() / top) * 100
        if depth <= FLAT_MAX_DEPTH:
            break
    else:
        return None
    if top < high_52w * (1 - FLAT_NEAR_52W_HIGH / 100):
        return None
    # 上昇途中の日をベースに含めないよう、高値圏に初めて届いた日をベースの開始にする
    s += int(np.argmax(h[s:t] >= top * (1 - FLAT_MAX_DEPTH / 200)))
    n = t - s
    if n < FLAT_DAYS[0]:
        return None
    depth = (1 - l[s:t].min() / top) * 100
    if depth < FLAT_MIN_DEPTH:
        return None  # 値動きがほぼ止まっている(TOBで価格が固定された銘柄など)
    # 横ばいなら高値は早めに付けている。ベース終盤に高値を更新し続けているのは上昇トレンドの途中
    first_touch = s + int(np.argmax(h[s:t] >= top * 0.98))
    if t - first_touch < FLAT_MIN_DAYS_SINCE_TOP:
        return None
    rise = _prior_rise(l, s, top)
    if rise is None or rise < FLAT_PRIOR_RISE_MIN:
        return None

    status = _status(c, t, top)
    if status is None:
        return None
    top_i = s + int(np.argmax(h[s:t]))
    low_i = s + int(np.argmin(l[s:t]))
    vol = _vol_ratio(v, slice(t - 10, t), slice(max(0, s - 50), s))
    if not vol < FLAT_MAX_VOL_RATIO:
        return None  # 横ばいの間に出来高が細っていない(売り物が多い)
    return {
        "status": status,
        "pivot": float(top),
        "base_start": s,
        "markers": [(top_i, float(top), "ベース高値"), (low_i, float(l[low_i]), "ベース安値")],
        "details": [
            ("ベース", f"{n / 5:.0f}週・値幅{depth:.0f}%"),
            ("ベース前の上昇", f"+{rise:.0f}%"),
        ],
        "vol_dry_ratio": vol,
    }


def contraction_legs(h, l, start: int, end: int, min_depth: float) -> list[tuple[int, float, int, float]]:
    """start(ベースの高値)から end-1 までを、大きな押し目から順に分解する。

    高値 → その後の最安値 → その後の最高値 → その後の最安値 … とたどるので、
    高値は切り下がり・安値は切り上がる。押し目が min_depth% 未満になったら終了。
    戻り値: [(高値の添字, 高値, 安値の添字, 安値), ...](古い順)
    """
    legs = []
    hi_i = start
    while hi_i + 1 < end:
        lo_i = hi_i + 1 + int(np.argmin(l[hi_i + 1:end]))
        if (1 - l[lo_i] / h[hi_i]) * 100 < min_depth:
            break
        legs.append((hi_i, float(h[hi_i]), lo_i, float(l[lo_i])))
        if lo_i + 1 >= end:
            break
        hi_i = lo_i + 1 + int(np.argmax(h[lo_i + 1:end]))
    return legs


def detect_vcp(h, l, c, v, t: int) -> dict | None:
    if t < 250:
        return None
    # 上昇トレンド中であること(終値 > 50日線 > 150日線 > 200日線、200日線が上向き)
    ma50, ma150, ma200 = c[t - 49:t + 1].mean(), c[t - 149:t + 1].mean(), c[t - 199:t + 1].mean()
    ma200_prev = c[t - 219:t - 19].mean()
    if not (c[t] > ma50 > ma150 > ma200 and ma200 > ma200_prev):
        return None

    # ベースの開始 = 直近 VCP_BASE_DAYS の範囲で最も高い高値(前日まで)
    a, b = t - VCP_BASE_DAYS[1], t - VCP_BASE_DAYS[0] + 1
    start = a + int(np.argmax(h[a:b]))
    top = h[start]
    if h[start + 1:t].max(initial=0) > top:
        return None
    legs = contraction_legs(h, l, start, t, VCP_MIN_DEPTH)
    if len(legs) < VCP_MIN_CONTRACTIONS:
        return None
    depths = [(1 - lo / hi) * 100 for _, hi, _, lo in legs]
    if not (VCP_FIRST_DEPTH[0] <= depths[0] <= VCP_FIRST_DEPTH[1]):
        return None
    if any(d2 >= d1 for d1, d2 in zip(depths, depths[1:])):
        return None  # 押し目が回を追うごとに浅くなっていない
    if depths[-1] > VCP_LAST_MAX_DEPTH:
        return None
    if any(lo_i - hi_i < VCP_MIN_LEG_DAYS for hi_i, _, lo_i, _ in legs):
        return None  # 1〜2日の急落は収縮とみなさない

    # ピボット = 最後の押し目が始まった高値
    last_hi_i, pivot, last_lo_i, last_lo = legs[-1]
    if h[last_lo_i + 1:t].max(initial=0) > pivot:
        return None  # 最後の押し目の高値を既に抜けている
    if l[last_lo_i + 1:t + 1].min(initial=np.inf) < last_lo:
        return None  # 最後の押し目の安値を割り込んだ(収縮が崩れた)
    status = _status(c, t, pivot)
    if status is None:
        return None
    markers = []
    for k, (hi_i, hi, lo_i, lo) in enumerate(legs, start=1):
        markers += [(hi_i, float(hi), f"高値{k}"), (lo_i, float(lo), f"安値{k}")]
    vol = _vol_ratio(v, slice(t - 10, t), slice(t - 60, t - 10))
    return {
        "status": status,
        "pivot": float(pivot),
        "base_start": start,
        "markers": markers,
        "details": [
            ("収縮", f"{len(legs)}回(" + " → ".join(f"{d:.0f}%" for d in depths) + ")"),
            ("ベース", f"{(t - start) / 5:.0f}週"),
        ],
        "vol_dry_ratio": vol,
    }


PATTERNS = {
    "cup_with_handle": ("カップウィズハンドル", detect_cup_with_handle),
    "flat_base": ("フラットベース", detect_flat_base),
    "vcp": ("VCP", detect_vcp),
}
