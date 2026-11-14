# GitHub main 与 SPHEREx 整合记录

2026-11-14。本地分支 `codex/merge-spherex-main`，远程 `https://github.com/cucuradu/skyshift.git`。

## Git 历史

1. 原 Desktop 开发分支基点：`bf0c3e9`。
2. 将已有 SPHEREx 整合结果保存为 `c251051`。
3. 三方合并 GitHub main `d920912`（PR #41），合并提交 `74c5410`。
4. 再次 fetch 后合并更新至 `1477f8e51a7a5551a6ef27a8610303aa562a92d2`（PR #49）。该 SHA 是本次验证的远程快照。

未向 GitHub push，未改动 Desktop 原仓库，也未进行云部署。原整合目录的独立备份在父目录 `.runtime/skyshift-before-upstream/`。

## 兼容与去重

- 采用主线 core、pipeline、search、cutouts、stack、Gaia、known 及契约更新，包括新的 calib/MPC 管线与验证数据；时间机保留单一科学扩展包，不运行旧 Flask 服务。
- 前端唯一像素播放器为主线 `web/src/blink`，取代整合早期的自建播放器；旧像素模块及其替代测试已移除。时间机图层只提供兼容 FramesResponse 元数据与数组地址。
- 主线播放器支持旧缓存的未知波长/质量字段，时间范围缺失时使用该帧的 MJD 中点；不会把缺失值当成零质量比例或伪造测量。
- 复用主线 sky/card/score/vote。详情卡不再绘制第二张光谱或光变；唯一光谱使用主线 renderer，DR1 参考点接在同一 SVG。
- 主线八目标 raw twins 保持原样。五目标模型、残差及候选分析使用 `/science-data/` 命名空间，避免覆盖团队测量和原始图。
- 任务摄取复用主线 search/cutouts；主线预计算仍可强制完整原始流程，原生/可信旧缓存通过独立 cached_results 适配器读取。
- 统一 pnpm 与冻结锁文件；CI 同时跑前端测试和构建，Python 各目录独立测试。API 缓存引擎版本提升到 `5-github-1477`，避免复用旧引擎响应。

## 验证

Python：core 44、pipeline 132、server 243、scripts/science 19、scripts/validate 4，共 442 通过、4 跳过。前端：146 个 Vitest 测试及 TypeScript/Vite 生产构建通过。

浏览器检查八个真实目标、唯一播放器和光谱、月/时与探测器/视场、相减/相加、模型/残差/DR1 差图、详情卡、验证面板、古星、GIF、坐标解析和手机宽度。外部目录故障、CDN 离线与后台任务 UI 使用 mock；未提交远程投票。结果见 `docs/previews/browser-check.json`。

验证在暂存版本的隔离导出中运行；同目录另一项 Explore/Learn 开发的未提交改动保留在工作区，不归入本次合并提交。Docker daemon 未运行，镜像和云端多实例行为不在本轮验证范围。
