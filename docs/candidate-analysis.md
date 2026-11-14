> 2026-11-14 更新：本文记录第一阶段缓存适配。当前完整合并、原生任务和新增功能以 [merge.md](merge.md) 与 [HANDOFF](../HANDOFF.md) 为准。

# 候选筛选迁移说明与补充接口提案

2026-11-14。此次修改跨 core、server、web 和 scripts/precompute：五个真实目标需要共同的数据链路；原仓库的 web 尚未实现、blink 引擎为占位。没有修改 contracts/，没有推送、部署或修改云资源。

## 保持的接口

`frames.json`、`object.json` 和 `/api/frames`、`/api/object` 保持 contracts/api.md 的字段名、单位和二进制方向。图像为 little-endian uint16，0 表示缺失，value = raw × scale + zero（MJy/sr）。北上东左。只在同一探测器内构造巡天中值叠加与位移候选。visit 从所有可用曝光时刻调用 core.visits 后再按探测器选择，避免稀疏波段产生不同分组。

输入是本机用户已有的、可信的 spherex-tm pickle（默认 ~/.spxtm_cache/results，可用 SKYSHIFT_RESULTS 覆盖），仅只读；不得把上传文件放入这个目录。服务不导入旧 Flask 程序，预计算产物无需旧服务器也能显示。

旧缓存覆盖约 4′，按约定映射到 5′ / 2.05″ 网格，外沿缺失，未伪造大视场。它已经做过黄道光、背景扣除。旧掩膜与 SkyShift 当前约定不同，且不保留 FLAGS 立方体。`flag_frac`、`sat_frac`、全图波长范围、逐曝光 MJD-BEG/END 无法重建，均为 null；没有用 NaN 比例、中心波长或推测的曝光时长代替。object 光谱 mJy × 1000 转为 µJy，visit 重新编号，核心饱和从保留的目标 flag 计数判断；没有 V3 参考星误差，`measure` 为 null。Gaia 只包含已缓存的目标条目，未运行全视场查询，`known` 为空。

这些产物用于接通演示，不能声称已完成原始数据的完整合约质量验证。任意坐标目前仅能命中已有缓存场区，其他位置返回 404。未实现的上游接口默认返回错误，只有显式 SKYSHIFT_ALLOW_SYNTHETIC=1 才允许带 synthetic 标记的 fixture；前端仍拒绝将其当作真实观测显示。

## 补充输出提案（尚未进入团队合约）

为避免把新字段塞入正式 frames/object，实验结果单独放在 `/data/<slug>/analysis.json`，本机按需端点为 `/api/analysis?ra=&dec=&det=1..6`。这是本实现的补充接口，待 P1 审阅后再决定是否加入 contracts/；没有把它作为已批准的团队合约。

顶层：`version`、`detector`、`n_slides`、`source`、`warnings`、`slow_movers`、`fast_movers`、`cold_candidates`。source 记录缓存文件名、总曝光、visit、覆盖范围、源像素尺寸。位置为 ICRS 度，慢运动速度 arcsec/yr，快运动速度 arcsec/day；输出同时保留原始像素诊断量，仅供开发核查。候选 `status` 不代表发现认证。

## 算法变化

- 原数值实现保存在 core/legacy/{detect,movers}.py，来源是用户提供的 spherex-tm/spxtm/algo，接口适配在 core/candidates.py 与 server/analysis.py。没有运行旧服务器来生成 PNG。
- 运动仅在同一探测器中检测/链接；快运动使用随机逐帧旋转背景，慢运动使用随机 visit 星表平移背景。固定随机种子，默认各 100 次。不继续使用偏高的旧视差拟合。
- p_false = (1 + 背景统计量不小于候选的次数) / (1 + 背景次数)。100 次的最小可报告值约 0.0099，不可宣称更小；空背景、非有限值返回 null。它是指定检验下的经验搜索误报估计，不是候选为假的后验概率，也不是全站多重检验校正。
- D6/D3 冷源筛选在未减静态模型的缓存图像上重新测峰并强制测光；D6 S/N≥5，必须有同一次、相差不超过 1 秒的 D3 实测覆盖且 S/N<2。缺失 D3 不能算 nondetection。
- 至少两个 native visit 的重复信号才通过重复性门槛。分组半径 2.5 个源像素，以种子为界，不串联扩张；不自动追踪更快移动者，存在漏检边界。
- 新算法收到完整 flags 时按当前 MASK_BITS 排除、RECORD_BITS 保留警告；当前旧缓存没有 flags，所以跨 visit 候选仍为 unvetted。单 visit 为 single_visit，不提升为持久候选。
- Gaia / SkyBoT / CatWISE 全视场排除尚未执行。已知恒星、鬼影等可能通过重复性门槛；颜色筛选 p_false 未独立标定，保持 null。

## 验证

离线 Python 测试覆盖旧核心/接口和新增：有限背景修正、缺失短波段、同 visit 拒绝、真实同时性、完整 flags 掩膜、北上东左、4′→5′ 缺失边缘、关闭默认模拟回退。前端像素测试覆盖缺失值传播及统一拉伸。浏览器检查五个真实缓存目标、切帧、差分、播放、逐曝光 API、波段切换、名称解析、未缓存坐标报错和手机横向溢出。

科学限制：这次验证的是数据链路和算法行为，没有证明所有候选为真实移动天体，也没有完成全景源表交叉匹配、V3 位移误差或视差验证。
