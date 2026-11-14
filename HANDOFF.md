# SkyShift × SPHEREx Time Machine 合并交接

更新：2026-11-14。工作副本在 `Documents/ChatGPT/NASA/skyshift`。本次是代码和产品能力整合，没有提交到远程或改动 Desktop 原仓库。

## 运行：一个入口

```bash
# Node 22+、pnpm、uv；Python 依赖统一锁定到 3.12
./scripts/run-local.sh
# 初次构建，或更改前端后：
SKYSHIFT_BUILD=1 ./scripts/run-local.sh
```

访问 http://127.0.0.1:8082 。Vite 开发仍可在 web/ 执行 pnpm dev，/api 代理到 8082。

本机已经验证的运行环境：

```bash
SKYSHIFT_PYTHON="$HOME/Desktop/NASA/.devtools/skyshift-server/bin/python" ./scripts/run-local.sh
```

启动脚本将 API 缓存、任务、参考数据放在本仓库 `.runtime/`，不依赖旧 Flask 服务或旁边的 spherex-tm 源目录。五个旧结果已迁移为 `.runtime/jobs/` 下的 JSON + NPZ，禁止 pickle 的对象反序列化。迁移入口是 `scripts/migrate_results.py`，只读取用户自己的可信旧缓存；新任务不生成 pickle。

## 功能归属

- 页面和唯一播放器：web/src/app/main.ts；原图/静态模型/残差/DR1 差图、月/时两时钟、差分、反色、Gaia 叠加、候选定位。
- 唯一光谱：同一张图显示目标和 DR1 参考，声化复用该光谱数据。
- 附加科学/文化面板：web/src/card/science.ts；光变、灵敏度、目录查询、古星、曝光明细、导出。
- 时间、掩膜、候选：core/。残差检测和链接只有一份 canonical 实现，science/algo 的同名模块仅为导入适配。
- 科学计算：server/src/skyshift_server/science/；来自 spherex-tm 的模型、参考、目标测光、实验性自行、灵敏度、NEOWISE、共动伴星。不包含旧 Flask 程序或第二套网页。
- 同一服务：FastAPI app.py。/api/frames、object、gaia、known 保持既有合约；增量接口说明在 docs/merge.md，未擅自改动 contracts/。
- 新坐标：ingest.py 经 IRSA 搜索和切片，503/读取错误可回退 NASA S3 HTTP Range 区域读取；jobs.py 单工作线程、最多三个排队/运行任务、去重和持久化。
- 古星 JS 保持可复用组件，位于 web/public/guxing；数据许可见同目录 NOTICE.md。

## 数据与重算

五目标的原图、模型和残差已生成静态 JSON + uint16 数组；science.json 提供其余缓存分析。坐标或所选波段/时钟缺少缓存时可点「建立观测分析」，任务进度在同一页面显示。重算完成后自动改用动态结果，避免重新显示旧静态数据。首次可能需数分钟甚至更久，受上游服务和曝光数量影响。

```bash
# 从可信旧缓存重导出静态数据（源目录可用 SKYSHIFT_RESULTS 指定）
PYTHONPATH=core/src:server/src /path/to/python scripts/precompute/local_results.py
# 一次性迁移到 skyshift 原生结果目录
SKYSHIFT_JOBS="$PWD/.runtime/jobs" PYTHONPATH=core/src:server/src /path/to/python scripts/migrate_results.py
# 然后 cd web && pnpm build
```

缓存约 4′ 的场区不会伪造更大的图像，空缺保留为 raw=0。旧结果缺少完整 FLAGS，质量比例和某些时间/波长字段为 null。新数据按统一 FLAGS 掩膜处理。全视场目录排除未自动认证任何候选；实验性自行与模型灵敏度均有标签，正式 measure 仍待参考星标定。详见 docs/merge.md。

## 验证

- 191 个 Python 测试通过，1 个网络测试默认跳过；2 个前端像素测试通过，TypeScript/Vite 构建通过。
- 浏览器逐个打开五目标，验证只有一个播放器和一个光谱、模型/残差、古星历史和五星事件、GIF 导出、手机布局与错误恢复。外部服务 UI 用确定性 mock 测试，记录不冒充真实目录结果。
- 另行真实在线验证：IRSA SIA、一个 49×49 FITS 切片（含 flags/MJD/detector）、Gaia 查询、WISE 0855 的完整 DR1 参考（100 个有效光谱点，差图已持久化）。
- Dockerfile 已合并前后端构建；本机 Docker daemon 未运行，因此未完成镜像构建验证。

本地完整测试：

```bash
PYTHONPATH=core/src:server/src /path/to/python -m pytest core/tests server/tests -q
cd web && pnpm test && pnpm build
# 安装 playwright 和 Chrome 后，预览运行时：
node tests/browser-check.mjs
```

不要把本机线程队列直接当成分布式任务队列。当前支持单 Python 进程，服务器迁移应保留 SKYSHIFT_JOBS 目录；多实例云部署需共享任务存储/队列。没有执行云部署或改动 Google Cloud 资源。
