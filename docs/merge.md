# SkyShift 与 SPHEREx Time Machine 合并说明

2026-11-14。在当前工作副本完成 SPHEREx 整合及 GitHub main 两轮合并，核对到 `1477f8e`。本地分支 `codex/merge-spherex-main`，未 push、未部署，Desktop 源码保持原状。详见 [GitHub 合并记录](upstream-merge.md)。

## 去重后的功能清单

| 能力 | 合并后唯一入口/实现 |
|---|---|
| 名称/坐标与八目标 | SkyShift 搜索和目标卡，沿用主线 resolve/坐标解析与 URL 状态；禁用 CDS 名称回退 |
| 月/时、反色、相减/相加、波段比较 | 唯一主线 web/src/blink 播放器；同探测器内播放，切换 D3/D6 比较颜色，不将跨波段闪烁解释为移动 |
| 静态天空模型、残差、DR1 参考差图 | 同一播放器的图像菜单，仍传原始数值数组，客户端负责拉伸和相减 |
| 光谱与参考、声化 | 同一张光谱，白点为 DR1 参考，声化复用当前数据 |
| 快/慢移动与冷源 | core/candidates + canonical legacy/movers；pipeline 不再另跑一套候选评分 |
| 光变 | 合成 W1/W2 随 visit 比较；光晕相对变化标实验性，旧 3.8–5 µm 光晕结果保留原始窗口标签 |
| 自行 | 独立详情展示实验性目标拟合，不覆盖尚未标定的正式 measure；新拟合单探测器，视差关闭 |
| 灵敏度/Planet X 模型 | 一个灵敏度面板与展开的半径—距离表，不宣称全局排除或发现 |
| NEOWISE、共动、CatWISE、小天体、Gaia | 一个按需查询面板，API 同源、统一错误格式、缓存成功结果；CatWISE 复用相同的慢运动候选，反推到目录历元匹配 |
| Gaia 预测位置、候选定位 | 叠加在现有播放器；Gaia 从 2016.0 传播，视差未加入此显示 |
| 天区背景、正式天体详情、验证与投票 | 沿用主线 sky/card/score/vote；card 关闭重复图表，光谱和光变各保留一个入口；Aladin 失败不影响播放器 |
| GIF、JSON、CSV、ADES、MPC | 一个下载区，GIF 固定当前探测器；ADES/MPC 未认证草稿，观测站代码须人工填写 |
| 古星 | 一个可展开的文化面板，当前目标、历史客星图与 NASA JPL 五星事件；独立组件，不参与候选评分 |
| 新坐标任务与进度 | 一个有界队列，去重、JSON/NPZ 原生存储，不运行旧 Flask 服务器 |

## 适配规则

1. 所有 visit 都来自 skyshift_core.visits：60 天间隔规则、超过 75 天的长 visit 等分到最多 30 天。分组基于场区全部曝光后再选探测器；光谱和目录的时刻按同一 visit 重算。
2. 所有新数据使用 core FLAGS 策略，SOURCE/FULLSAMPLE 不掩膜，记录类位保留。旧缓存信息不足，不能“升级”为完整标志；界面按数据来源显示质量状态。
3. 正式响应遵循 ICRS 度、MJD UTC、arcsec、µm、µJy；内部旧算法 mJy 在适配层转换。Gaia id 转字符串、pm 保持 mas/yr，古星接口前将测得 arcsec/yr ×1000。
4. 同探测器比较亮度与位移。背景模型允许多波长共同建模；新目标自行和候选链接按单探测器执行。不同方法（SEP 曝光源表、残差 matched-filter、PSF 目标测光）各有用途，不建立重复产品入口。
5. 数据查询 IRSA 优先。实测一次 QR3 切片 503，另一个 DR1 实测切片成功；因此加 S3 Range 区域回退，并以单元测试验证该错误路径，不下载整个 L2 FITS。探测器以 header 和 SIA bandpass 校验，时间取 MJD-AVG，不从文件名或 DATE 推测。
6. 新任务有界、重复请求复用、失败可重试；默认最多 800 曝光且限制 800 万立方体像素，大视场按完整观测组有界采样，日志与 source 报告实际/可用曝光数，避免移动设备所连接的服务端因大视场耗尽内存。结果以不含对象数组的 NPZ + JSON 持久化。原 pickle 仅作为用户可信缓存的一次性迁移来源。已有原生结果时默认不访问旧 pickle。
7. 桌面和手机使用同一页面。古星/星表等按需加载；切换目标会取消当前页面的数据请求、停止声化，过期请求不能覆盖新目标。已发起的后台分析继续运行，结果留在队列/缓存中。

## 增量接口（在主线合约之外单独记录）

| 接口 | 用途 |
|---|---|
| GET /api/frames?...&view=raw/resid/model/refdiff | 返回既有 frames 形状，只扩展图像选择参数，默认 raw 保持兼容 |
| POST /api/jobs?ra=&dec=&size= | 创建/复用新坐标分析，返回 id/state/progress/error |
| GET /api/jobs/{id} | 任务状态，错误也以可见状态返回 |
| GET /api/science?ra=&dec= | source、bands（µJy/星等）、lightcurve、reference、sensitivity、astrometry、frames |
| POST /api/reference?ra=&dec= | 请求 DR1 光谱与差图，保存至原生缓存；已有则复用 |
| GET /api/analysis?ra=&dec=&det= | 原补充候选接口，所有消费方共用此缓存和评分实现 |
| GET /api/enrichment?...&kind=relay/comovers/catwise/asteroids | 按需外部服务，部分小天体失败显式报告，不缓存不完整结果 |
| GET /api/export?...&det=&clock=&format=json/csv/gif/ades/mpc80 | 下载当前选择的分析或图像；草稿不自动提交 |

主线静态 twins 与 index、正式 object.json 原样保存在 `/data/<slug>/`，由公共 API client 优先读取；探测器不存在时回退 API。时间机扩展在 `/science-data/<slug>/`：analysis.json、science.json、model.json、resid.json 及其帧数组。`scripts/precompute/local_results.py` 默认只写 science-data；旧缓存的 raw/object 副本留在该扩展目录供追溯。所有预计算由脚本生成，未手工编造实测数据。

## 边界与验证范围

正式 measure 的参考星 V3 误差尚未补齐，保持 null。冷源的颜色检验没有独立误报标定，p_false 为 null；运动的 p_false 是指定随机背景检验的经验估计，默认 100 次，不能当成天体为假的后验概率。自动候选仍需目录和影像核查，旧缓存不能恢复缺失的 flags。

单元/集成与浏览器证据见 HANDOFF.md 和 previews/browser-check.json。真实联网验证与模拟上游 UI 测试明确分开。NEOWISE/CatWISE/JPL/SkyBoT/共动服务没有在本次逐一验证全部目标；异常、部分结果和重试路径已有适配，实际可用性取决于上游。

本次没有实测 Docker 镜像（daemon 未运行），没有验证云端多实例任务分发，也没有执行云发布。默认单进程本地运行可复现；迁移机器时携带 .runtime/jobs，或重新获取观测。八个主线静态目标及五目标模型/残差无需旧目录即可展示。
