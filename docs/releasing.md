# CI 与版本发布

工作流为 [`.github/workflows/ci-release.yml`](../.github/workflows/ci-release.yml)。运行记录见 [GitHub Actions](https://github.com/whoarei/traffic-monitor/actions/workflows/ci-release.yml)，正式版本见 [Releases](https://github.com/whoarei/traffic-monitor/releases)。

## 触发规则

| 事件 | 行为 |
| --- | --- |
| 向任意分支提交代码 | 语法检查、单元测试、构建和校验，上传构建附件。 |
| Pull Request | 执行相同检查，使用只读仓库权限。 |
| 推送 `v1.3.1` 这类版本标签 | 校验标签与代码版本；全部检查通过后发布 GitHub Release。 |
| Actions 页面手动运行 | 分支上只检查和构建；选择版本标签时可重试该版本的发布。 |

普通提交不会直接创建 Release。正式发布使用 `vMAJOR.MINOR.PATCH` 标签，当前不接受预发布后缀或前导零。仅修改版本号但尚未推送标签时，仍只进行普通 CI。

## 准备新版本

1. 更新 [`app/version.py`](../app/version.py) 的 `VERSION`，例如 `1.3.1`。应用状态、deb 元数据和私人交付脚本共用此值。
2. 提交功能、测试与文档变化；按需要更新 README 安装示例和技术方案的适用版本。历史示例版本不由 CI 批量改写。
3. 将代码推送到 `main` 并确认检查通过。
4. 创建并推送相同版本的标签：

```sh
python3 scripts/check_version.py
python3 -m unittest discover -s tests -v
git push origin main
git tag -a v1.3.1 -m "VPN Traffic 1.3.1"
git push origin v1.3.1
```

标签工作流会再次独立测试和构建。若 `VERSION` 为 `1.3.1`，推送 `v1.3.2` 或 `v1.3.1-rc.1` 将失败，不会发布。

## 检查与发布附件

构建在 GitHub 托管的 Ubuntu 24.04 runner 中执行：

1. 校验版本及标签，检查 Python、JavaScript 和 deb 维护脚本语法。
2. 运行 `tests/test_*.py` 单元测试和 `npm run test:web` 浏览器回归。后者安装锁定版本的 Playwright / Chromium，使用本地内存夹具，不连接真实服务器；旧版现场验收脚本不在 CI 中执行。
3. 构建 deb，检查包内版本和 `Architecture: all`，解包后执行 CLI `--help` 验证 Python 模块齐全。
4. 使用 `git archive HEAD` 导出当前提交的源码，不读取服务器、数据库或私人交付目录。
5. 复制项目技术方案，生成并核对 SHA256；上传构建附件，保留 14 天。
6. 标签运行下载刚刚测试过的同一组附件，重新核对校验值，使用 `VPN_TRAFFIC_SIGNING_KEY` Secret 签署升级清单并确认固定公钥匹配，再创建草稿、上传附件并发布。

以 `1.3.1` 为例，Release 提供：

- `vpn-traffic_1.3.1_all.deb`
- `vpn-traffic_1.3.1_all.deb.sha256`
- `vpn-traffic-1.3.1-source.tar.gz`
- `vpn-traffic-1.3.1-technical-design.html`
- `vpn-traffic-1.3.1-technical-design.md`
- `SHA256SUMS`
- `update-manifest.json`
- `update-manifest.sig`

在下载目录执行 `sha256sum -c SHA256SUMS` 可检查附件。GitHub 还会提供自身生成的 Source code 归档。Actions 构建附件到期不会删除 Release 附件；仓库保持私有时，下载也需要仓库访问权限。

本流程不会执行安装脚本或自动部署到 VPN 服务器。Debian 安装包依赖既有 Xray、HTTPS 订阅及部署路径，安装要求见 [技术方案](technical-design.md)。

## 失败与重试

检查或构建失败时，发布 job 不执行。附件上传失败时可能保留未公开的草稿，在 Actions 中重新运行失败任务可补齐附件并发布。已正式发布的同名 Release 会保留原附件，不会在重跑时覆盖；后续修复应使用新的版本号和标签。

正常分支构建只使用 `contents: read`；发布 job 仅在版本标签上获得 `contents: write`，使用仓库自动提供的 `GITHUB_TOKEN`。无需配置个人访问令牌或服务器 SSH 凭据。所有第三方 action 固定到完整提交 SHA，并注释对应版本，升级 action 时应同步校验其兼容性。

## 在线升级签名

发布 job 需要 `VPN_TRAFFIC_SIGNING_KEY` Secret，内容为与 `packaging/update-signing.pub` 匹配的 Ed25519 私钥 PEM。密钥不进入源码或构建 job；发布阶段使用 600 权限的临时文件，完成后清理。脚本 `scripts/create_update_manifest.py` 读取本版本 CHANGELOG，验证 deb 元数据并生成一年有效的签名清单；修改数据库兼容范围时必须同步审查该脚本的兼容与回退声明。

公钥首次安装后固定在服务器；更换信任根需独立的受信任维护。仅发布版本不会自动部署到服务器。在线安装和首次迁移见 [在线升级与失败恢复](updating.md)。
