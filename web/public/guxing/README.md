# js/ — 浏览器 / App 端（零依赖）

| 文件 | 作用 | 大小 |
|---|---|---|
| `guxing-data.js` | 星官连线、星名、28 宿距星、恒星坐标；设置 `window.GUXING_DATA`（Node 下 `module.exports`）。由 `tools/build_data.py` 生成，**不要手改** | 256 KB |
| `guxing.js` | 核心算法，`python/guxing/core.py` 的逐行移植。改一边必须改另一边，然后跑 `tests/` | 20 KB |
| `guxing-nasa.js` | NASA 预计算结果（JPL DE421 事件、SPHEREx 实测自行、SPHEREx 遗迹图像路径）；`window.GUXING_NASA`。由 `tools/bundle_nasa.py` 生成 | 134 KB |
| `guxing-widget.js` | `<guxing-card>` Web Component，属性说明见文件头注释 | 10 KB |

API（与 Python 同名，驼峰处除外）：
- `gx.locate(ra, dec, {date, epoch, teff, mag, kind, kind_en})`
- `gx.track({ra, dec, pmra, pmdec, start, end, steps})` 或 `gx.track({points: [[date, ra, dec], ...]})`（pm 单位 mas/yr）
- `gx.mansion(ra, dec, epoch)`、`gx.nearest(ra, dec, k)`、`gx.segments(ra, dec, radius)`、`gx.mansionWidths(epoch)`、`gx.backtest(GUXING_NASA.guest_stars)`

嵌入 App 的 WebView：把这四个文件和 `data/nasa/spherex_remnants/` 一起打包即可，不需要网络。
