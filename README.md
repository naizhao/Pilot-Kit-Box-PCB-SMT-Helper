# Pilot Kit 贴片助手发布仓库

发布仓库：[Pilot-Kit-Box-PCB-SMT-Helper](https://github.com/naizhao/Pilot-Kit-Box-PCB-SMT-Helper)。本仓库保存公开发布快照及 Pages 自动化；当前仓库保持私有，Actions 与 Pages 均未启用。
页面和板卡数据唯一维护来源为
[Pilot-Kit-Box-ESP32-P4](https://github.com/naizhao/Pilot-Kit-Box-ESP32-P4) 的 `web/assembly/`，不要在本仓库修改快照。

## 更新

在源项目修改并提交页面或数据，然后在本发布仓库目录运行这一条命令（源项目位于相邻目录）：

```sh
python3 scripts/export_site.py --source ../Pilot-Kit-Box-ESP32-P4 --ref HEAD --publish
```

不带 `--publish` 时仅同步本地快照；带该开关即明确授权提交快照并推送本发布仓库 main，当前 Actions 关闭，推送不会部署站点。
只从指定 Git 提交读取，不包含源工作区未提交修改，也不要求源仓库远端 main 已同步。
`_build.json` 记录实际源 SHA。没有快照变化时跳过提交与推送。
发布前检查 origin 精确指向 `naizhao/Pilot-Kit-Box-PCB-SMT-Helper`、当前分支为 main、暂存区为空；不会写入源项目或强制推送。

创建发布提交前，必须先完成身份隐私复核：使用项目公开署名及 GitHub noreply 邮箱，禁止沿用个人姓名或个人邮箱；本工具不自动修改 Git 身份或既有提交。

发布与 Pages 打包前会扫描发布提交的全部受跟踪内容及拟发布快照，包含 README、脚本和工作流；发现个人用户目录路径即阻断，只报告相对文件名、行号和类别，不回显目录值。

回退时把 `--ref HEAD` 改成经过确认的源项目历史提交 SHA，再运行相同命令；产生新的快照提交，不重写 Git 历史。

## 公开边界

同步与站点产物仅包含 `index.html`、`data/index.json` 及其列出的板卡 JSON、`_build.json`、`CNAME`、`.nojekyll`。
采购批次 `batches.json`、`internal/`、工程资料和源仓库其他文件不会导出。
旧快照中不再被索引引用的 JSON 即使仍留在发布仓库，也不会进入 Pages 产物。
新增板卡只需在源项目提交索引和对应 JSON，无需在此维护第二份清单。

保留的 Pages 工作流使用本仓库的 GitHub Actions；不会占用源项目的固件 updater 站点。
今后启用站点须另行授权；届时 Pages 选择 GitHub Actions，自定义域名为 `smt.pilotkit.app`。
代码沿用源项目 AGPL-3.0-only，完整许可证见 `LICENSE`。

本地验证（在本发布仓库目录）：`python3 -m unittest discover -s tests -v`。
