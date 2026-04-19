# MTools 发版指南

本文档说明如何通过 GitHub Actions 发布正式版和测试版，以及如何避免测试版触发客户端的更新提醒。

---

## 🎯 版本号规范

`src/constants/app_config.py` 的 `APP_VERSION` 决定应用内显示的版本号，`git tag` 决定 GitHub Release 的 tag 和名称。两者建议保持一致。

| 类型 | `APP_VERSION` | `git tag` | 触发更新提醒？ |
|------|---------------|-----------|----------------|
| 正式版 | `0.1.6` | `v0.1.6` | ✅ 会 |
| 公测版 / Beta | `0.1.6-beta.1` | `v0.1.6-beta.1` | ❌ 不会 |
| 候选版 / RC | `0.1.6-rc.1` | `v0.1.6-rc.1` | ❌ 不会 |
| 内测 / 小范围测试 | `0.1.6-test.1` | `v0.1.6-test.1` | ❌ 不会 |
| Alpha 版 | `0.1.6-alpha.1` | `v0.1.6-alpha.1` | ❌ 不会 |
| 开发版 | `0.1.6-dev.1` | `v0.1.6-dev.1` | ❌ 不会 |

> **原理**：`.github/workflows/build.yml` 里判断 `tag` 是否包含 `-beta` / `-alpha` / `-rc` / `-test` / `-dev`，命中则把 Release 标记为 **prerelease**。GitHub `api/repos/:owner/:repo/releases/latest` 接口 **永远不会返回 prerelease**，因此客户端的更新检查（`src/services/update_service.py`）天然拉不到这些测试版，不会弹升级提示。

---

## 🧪 发布测试版（不触发更新提醒）

### 1. 修改版本号

编辑 `src/constants/app_config.py`：

```python
APP_VERSION: Final[str] = "0.1.6-beta.1"
```

### 2. 提交 + 推 tag

```bash
git add src/constants/app_config.py
git commit -m "chore: bump to 0.1.6-beta.1"

git tag v0.1.6-beta.1
git push
git push origin v0.1.6-beta.1
```

### 3. 等流水线跑完

GitHub Actions 会：
- 自动构建 Windows / macOS / Linux 各平台产物（DirectML / CUDA / CUDA FULL）
- 创建一个 **prerelease**，不会出现在 "Latest release" 徽章里
- 不会被客户端的自动更新检查拉到

### 4. 分发给测试者

把 Release 页面的对应下载链接直接发给测试者即可（Release 依然是公开可见的，只是不会被判定为 "最新"）。

---

## 🚀 发布正式版

### 1. 修改版本号

编辑 `src/constants/app_config.py`，**去掉后缀**：

```python
APP_VERSION: Final[str] = "0.1.6"
```

### 2. 提交 + 推 tag

```bash
git add src/constants/app_config.py
git commit -m "chore: release v0.1.6"

git tag v0.1.6
git push
git push origin v0.1.6
```

### 3. 等流水线跑完

- 普通 tag（不含 `-beta` / `-alpha` / `-rc` / `-test` / `-dev`）会自动标记为正式 Release
- `api/releases/latest` 立即返回它 → 所有装了老版本的用户在下次开启更新检查时会收到升级提示

---

## 📦 完全不公开的测试版（可选）

如果连 Release 页面都不想让普通用户看到，只想发给少数测试者：

1. 打开 [Actions → Build and Release](https://github.com/HG-ha/MTools/actions/workflows/build.yml)
2. 点 **Run workflow**
3. `是否创建 Release` 保持 **false**（默认就是 false）
4. 跑完后进入该次运行详情页，底部 **Artifacts** 区域可下载打包产物

> ⚠️ Artifact 只保留 7 天（`retention-days: 7`），需要长期分发请走 prerelease 路线。

---

## 🧯 常见问答

### Q1：我已经用正式 tag 发了，但其实想作为测试版怎么办？

两条路：

1. **网页改**：进 GitHub Releases 页面 → 编辑该 Release → 勾 **Set as a pre-release** → Save。立即生效，老用户从这一刻起的更新检查就拉不到它了。
2. **删除重发**：删除该 Release 和对应 tag，改用带 `-beta.1` 后缀的新 tag 重发。

### Q2：测试版用户之后能正常收到正式版的更新提示吗？

**能**。`src/services/update_service.py` 的 `compare_versions` 按 PEP 440 / SemVer 规则比较版本号，约定的优先级：

```
x.y.z-alpha.N  <  x.y.z-beta.N  <  x.y.z-rc.N  <  x.y.z
x.y.z-dev.N    <  x.y.z
x.y.z-test.N   <  x.y.z   （内部约定，归一化为 .devN 处理）
```

举例：装了 `0.1.6-beta.1` 的用户，在正式版 `0.1.6` 发布后下次检查更新时，会被正确判定为"有更新可用"并收到升级提示。

### Q3：Release 失败了怎么办？

- 流水线失败：检查对应 job 的日志。常见原因：网络抖动（重跑即可）、flet build 环境问题（参考 `docs/build_guide.md`）
- tag 推错了：`git tag -d v0.1.6-beta.1 && git push origin :refs/tags/v0.1.6-beta.1` 删掉本地和远端 tag，修完再推
- Release 已创建但 artifact 不全：进 Release 页面手动补传，或删掉 Release 让流水线重跑

### Q4：每次必须手动推 tag 吗，不能只改 `APP_VERSION` 就发？

**是的，必须推 tag**。workflow 的 Release 任务只在 `refs/tags/v*` 推送时或手动勾选 `release=true` 时才执行。只 push commit 不推 tag 只会跑构建，不发 Release。

---

## 📋 发版 Checklist

发正式版之前建议挨个过一遍：

- [ ] 代码已合并到 `main` 分支
- [ ] `APP_VERSION` 已改成目标版本号（且与 tag 保持一致）
- [ ] 本地简单跑过 `uv run python src/main.py`，UI 能正常打开
- [ ] `git status` 干净，没有未提交改动
- [ ] `git log` 最近 commit 信息清晰（Release 会自动生成 notes）
- [ ] tag 已创建并 push 到 origin
- [ ] GitHub Actions 流水线绿灯通过
- [ ] Release 页面产物齐全（Windows / macOS / Linux 各变体）
- [ ] 下载一个包实测能跑起来
- [ ] 测试版：确认在 Release 页面显示 `Pre-release` 徽章；正式版：确认 `Latest` 徽章

---

## 🔗 相关文件

- `.github/workflows/build.yml` — GitHub Actions 构建/发布流水线
- `src/constants/app_config.py` — `APP_VERSION` 所在位置
- `src/services/update_service.py` — 客户端更新检查逻辑
- `docs/build_guide.md` — 本地编译指南
