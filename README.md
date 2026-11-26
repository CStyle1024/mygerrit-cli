# mygerrit-cli

独立的 Gerrit CLI，不依赖 issue-resolver 或兄弟项目源码。Python >=3.8，Linux，YAML 输出由 PyYAML 提供。

## 安装

需要 Bash、Git、`uv` 和 GitHub 仓库访问权限。发布安装脚本后，可执行：

```bash
curl -fsSL https://raw.githubusercontent.com/CStyle1024/mygerrit-cli/main/install.sh | bash
```

已有源码时可直接运行 `bash install.sh`。脚本默认通过 HTTPS 浅克隆 GitHub 仓库的默认分支，使用 `MYGERRIT_CLI_REPO` 可指定仓库地址，使用 `MYGERRIT_CLI_REF` 可选择分支或标签。安装结束后清理临时源码目录。如果终端找不到命令，运行 `uv tool update-shell` 后重新打开终端。

也可以在源码目录中用 pip 安装：

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install wheel
python -m pip install --no-build-isolation .
mygerrit-cli --help
mygerrit-cli --tool-info
```

也可从源码执行 `PYTHONPATH=src python3 -m mygerrit_cli --help`。发行包名与命令名均为 mygerrit-cli。

## 配置

### 交互式初始化

在终端运行：

```bash
mygerrit-cli config init
mygerrit-cli doctor
```

向导询问 REST 地址和认证方式。Bearer 隐藏输入 token，Basic 回显用户名并隐藏输入密码。验证成功后，凭据保存在独立配置目录下权限为 `0600` 的文件中，`${XDG_CONFIG_HOME:-$HOME/.config}/mygerrit-cli/config.json` 只保存凭据文件路径。

如果使用已有的环境变量，运行 `mygerrit-cli config init --auth-env`，向导会询问变量名。

留空服务器名称可建立单服务配置。使用名称可登记多台 Gerrit，之后通过 `mygerrit-cli --server NAME doctor` 或相应查询命令选择。现有独立单服务配置在添加具名服务器时保留为 `default`。探测仅使用 REST。内网 HTTP 发送凭据需要显式确认，Git 远端及 SSH 设置仍由仓库管理。

普通命令的配置优先级为 `--config`、`ISSUE_RESOLVER_CONFIG`、独立配置、当前目录的 `project.json`。初始化始终只写独立配置。身份验证不检查写入权限。运行 `mygerrit-cli doctor --format yaml` 可查看完整 YAML 响应，默认输出仍为 JSON。


使用 `--config /absolute/path/config.json`，或设置 `ISSUE_RESOLVER_CONFIG`；默认读取当前目录的 project.json。可使用完整工作流配置，也可只包含 gerrit 服务配置。认证使用环境变量或文件引用，不把凭据写进 JSON。

```bash
cp examples/gerrit.json gerrit.local.json
# 填写服务地址及认证引用后运行
mygerrit-cli --config gerrit.local.json changes list --query owner:self --limit 5
```

## Gerrit 查询和上传

将 `examples/gerrit.json` 的示例地址、project 和认证引用替换成本机配置；不能仅凭 Git SSH 已登录就认为 HTTP REST 也已认证。REST 凭据从环境/本机文件引用读取，Git 推送使用仓库已有的 SSH/credential helper 配置，不自动复制 REST 密码到 Git 参数。两个服务可以合并到同一份项目配置中。

```bash
mygerrit-cli --config /path/to/project.json changes list --query 'status:open owner:self'
mygerrit-cli --config /path/to/project.json change get --project platform/frameworks/base --branch release --change-id I0123456789012345678901234567890123456789

# 基线和提交都使用完整 SHA；每次只接受一个 parent=base 的非 merge 提交。
mygerrit-cli --config /path/to/project.json upload --repo /path/to/fix-worktree --remote origin --branch release --base FULL_BASE_SHA --commit FULL_COMMIT_SHA --topic BUG-123 --preview
mygerrit-cli --config /path/to/project.json upload --repo /path/to/fix-worktree --remote origin --branch release --base FULL_BASE_SHA --commit FULL_COMMIT_SHA --topic BUG-123 --execute --operation-id bug-123-base-upload-001
```

`--project` 可覆盖配置的项目映射；工具不猜测带公司路径前缀的 remote 对应哪个 Gerrit project。预览要求基线可从 push 目标分支到达，目标 tip 在本地对象库可检查；缺对象时先显式更新本地仓库，不静默 fetch 或 rebase。

预览和执行都检查真实对象历史，禁用 replace refs 和 legacy grafts 对检查的影响。推送仅到 `refs/for/<branch>`，显式关闭 follow-tags 和子模块递归推送。候选必须带唯一 Change-Id footer；不会直推分支、自动投审核分数或合入。

推送后使用 project/branch/Change-Id 查询并核对精确 revision 和 patch set；Git 命令成功本身不能证明上传结果。输出含 change URL、number、revision、patch_set。topic 仅归组，不提供跨仓库原子性。

回执默认目录为 `${XDG_STATE_HOME:-~/.local/state}/issue-resolver/actions/gerrit`；支持 `--state-dir`、gerrit.state_dir、顶层 state_dir。使用相同 operation ID、相同参数和回执目录重试时，先读取原始预览并通过 REST 对账，不重新执行 Git 上传预检或 push。即使 SSH 暂不可用也可恢复查询；REST 也不可用时保留 UNKNOWN。不要删除回执或随意改 operation ID 重推。


## 仅提供 HTTP 的 Gerrit

`examples/gerrit-http.json` 演示 `review.rnd.meizu.com` 的匿名只读配置，不含凭据。Basic 认证 HTTP 配置示例见 `examples/gerrit-http-auth.json`。将配置的 `gerrit` 部分合并到本机项目配置时，保留已有的提交模板及仓库映射。

```bash
mygerrit-cli \
  --config examples/gerrit-http.json changes list \
  --query 'project:app/Client/AutoSecureService limit:1'
```

服务若要求认证，应使用 Gerrit 实际支持的凭据方式，例如 Basic 的用户名与 HTTP 密码引用，并显式启用以下两项：

```json
"transport": {"allow_http": true, "allow_http_credentials": true},
"auth": {
  "type": "basic",
  "username_env": "GERRIT_USERNAME",
  "password_env": "GERRIT_HTTP_PASSWORD"
}
```

HTTP 不加密认证信息、代码审查内容和响应；此配置只适用于你明确接受该传输方式的服务。优先使用可用的 HTTPS 入口或加密隧道。这里的配置不提供额外的网络信任保证。

`allow_http` 与 `allow_http_credentials` 默认均为 false，必须填写 JSON 布尔值，后者依赖前者。只开启 allow_http 时，远端 HTTP 的 Basic/Bearer 配置会在读取凭据前报错。许可仅适用于 base_url 的精确 scheme、主机和有效端口；更换主机或端口不会继承旧 origin 的许可。HTTPS 降级、跨 origin 认证转发和写请求重定向均不放行。原有本地回环 HTTP 测试行为保持兼容。

只有 mygerrit-cli 使用 gerrit.transport；Jira 不支持此 HTTP 放行配置。该配置不改变 Git remote、SSH 或 Git credential helper，也不启用多 Gerrit 服务。

已知边界：当前 URL origin 归一化会把显式端口 `:0` 当作默认端口；不要配置 `:0`，应省略端口或填写实际服务端口。

## 多 Gerrit 服务和查询上限

`examples/gerrit-multi.json` 展示具名 `gerrits` 和每个仓库的 `gerrit` 映射。每个服务分别填写 REST 地址、认证引用、传输策略及提交模板；每个仓库必须选择明确的服务、project、remote 和目标分支。示例的用户名和密码环境变量需要在执行进程中设置；切勿把密码填入 JSON。原有只有 `gerrit` 的单服务配置仍然支持。配置了 `gerrits` 时必须显式选择，不能依靠 Git remote 自动推断服务器。

```bash
mygerrit-cli \
  --config /path/to/project.json --server aosp \
  changes list --query 'owner:self' --limit 5
```

`--limit` 限制所有分页合计返回的条数，省略时继续读取全部页。查询语句中的 `limit:5` 在部分 Gerrit 版本中会影响每页大小，不能代替 CLI 的 `--limit 5`。对 HTTP 服务启用认证后凭据及内容不加密；该选择仅适合明确接受此传输方式的网络。

## 机器调用契约

`--format json`（默认）stdout 只含一个结果对象；`--format text` 以缩进 JSON 便于人工查看。帮助和版本信息为常规文本。错误不会回显原始异常、凭据或服务端登录页。

```json
{"schema_version": 1, "status": "SUCCESS", "data": {}, "error": null}
```

写入结果另含 operation_id、receipt_path。status 为 SUCCESS / FAILED / UNKNOWN；不要仅根据退出码或 stderr 判断某次未知写入是否发生。

| 退出码 | 含义 |
| --- | --- |
| 0 | 成功，包括只读预览 |
| 2 | 参数/配置错误 |
| 3 | 认证或权限错误 |
| 4 | 冲突或前置条件不满足 |
| 5 | 已知远端失败/归档部分失败 |
| 6 | 写入结果未知，需对账 |
| 7 | 本地错误 |


`--tool-info` 不读取配置或连接服务，输出 distribution、command、version、result_schema，供调用程序识别本项目及兼容契约。此接口不是程序签名或安全认证。

## 验证与构建

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -v
python -m pip wheel --no-deps --no-build-isolation --wheel-dir dist .
```

测试使用本地 HTTP 服务和临时 Git 仓库；真实服务的写操作不属于拆分验收。默认操作回执目录继续使用 ~/.local/state/issue-resolver/actions/gerrit（遵守 XDG_STATE_HOME），升级时不要删除。UNKNOWN 必须使用原 operation ID 与输入对账。
