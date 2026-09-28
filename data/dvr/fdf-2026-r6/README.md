# FDF CUP 2026 R6 の DVR 飛行経路（小さいファイルだけ）

`docs/dvr-localization.ja.md` の「印も COLMAP も使わない通し」の結果。作業場所 `build/dvr/<flight>/` から、JSON・JSONL・
テキストだけを写した。動画、シーン、照合のダンプ、ログ、d05 の blackbox は入れていない。ビューアは https://vdgs.saqoo.sh/dvr/fdf-2026-r6/ 。

- 最良の姿勢は各飛行の `index.json` の先頭（いまは `poses60_pad.json`）。web 座標（x 東・y 上・z 南、m）、`quat` はカメラ → 世界で COLMAP のカメラ軸
- `pad.json` はビューアの `pad` で人が置いたスタート台（e02 は当てはめのほうが近かったので無い）
- `align_*.json` は `eval_align.py` の遠景の評価、`choice.txt` は周の採否
- `race-sf-knt/block_labels.json` は HDZero のノイズブロックを人がラベル付けしたもの（`label_blocks.py`）
