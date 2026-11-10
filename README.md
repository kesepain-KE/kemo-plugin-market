# kemo-plugin-market

<p align="center">
  <strong>简体中文</strong> · <a href="README.en.md">English</a>
</p>

<p align="center">
  <strong>给 Kemo 网关的高级插件市场，提供索引生成与静态发布。</strong>
</p>

<p align="center">
  把插件包目录变成一份网关能直接消费的索引，<br>
  再用一个只读服务把它发布出去。
</p>

<p align="center">
  <a href="version.json"><img src="https://img.shields.io/badge/version-1.0.0-00a98f" alt="version 1.0.0"></a>
  <a href="https://www.python.org/"><img src="https://img.shields.io/badge/python-3.10%2B-3776ab" alt="python"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-Apache%202.0-green.svg" alt="license"></a>
</p>

---

## 这个仓库解决什么问题

Kemo 网关的高级插件支持"插件源"：你在管理界面填一个索引地址，网关就去拉一份 JSON，把里面列出的插件展示成插件市场。索引本身是静态文件，但要让网关真正拉得动，有几条不太直观的硬约束：

- 网关的 HTTP 客户端固定 `follow_redirects=False`，**任何 3xx 都会被判为源不可用**。GitHub Release 资产必然 302，所以不能直接当下载地址。
- 索引条目是多一个字段就整源失效的严格结构（`extra="forbid"`），`sha256` 必须是小写、`version` 必须能被 PEP 440 解析。
- 反向代理层如果把 404 渲染成自己的 HTML 再返回 200，网关只会报"索引不可用或格式无效"，排查方向会完全跑偏。

这个仓库把这些约束做成代码与测试：索引怎么生成、服务怎么响应、反代要注意什么，都有对应实现和可执行的验证。

## 它做什么

| 能力 | 说明 |
|---|---|
| 索引生成 | 扫描 `packages/*.zip`，从包内 `plugin.yaml` 读 id / 名称 / 版本 / 说明，自动算 SHA-256，输出 `index.json` |
| 结构校验 | 按网关的严格结构逐字段校验，未知字段、大写摘要、非法版本、重复条目在本地就拦下 |
| 静态服务 | 只读服务，永不跳转、不列目录、只放行索引与插件包，索引改动实时生效 |
| 端到端校验 | `verify` 直接对一个已发布地址做"索引可拉 + 结构合法 + 包可下 + 摘要一致"四步验证 |
| 一键部署 | systemd 单元 + 安装脚本 + 反代片段，装完只监听回环，经反代对外 |

## 快速开始

### 安装

```bash
git clone https://github.com/kesepain-KE/kemo-plugin-market.git
cd kemo-plugin-market
pip install -r requirements.txt
```

### 准备市场内容根

```bash
mkdir -p /srv/kemo-plugin-market/packages
cp examples/market.yaml /srv/kemo-plugin-market/market.yaml
```

编辑 `market.yaml`，把 `base_url` 改成外部实际能访问的站点根地址。

### 放入插件包

插件包必须符合 Kemo 高级插件 SDK v1：ZIP 根目录直接含 `plugin.yaml`，不要再套一层目录。用网关自带的 CLI 打包最稳：

```bash
python -m gateway.plugins check <插件目录>
python -m gateway.plugins pack  <插件目录> dsh-model-system-1.1.3.zip
cp dsh-model-system-1.1.3.zip /srv/kemo-plugin-market/packages/
```

### 生成索引

```bash
python -m kemo_plugin_market build --root /srv/kemo-plugin-market
```

输出会列出每个条目的 id、版本与摘要前缀，最后原子写入 `index.json`。

想先看看会生成什么，用 `--dry-run`；只想验证不想落盘，用 `--check`。

### 启动服务

```bash
python -m kemo_plugin_market serve --root /srv/kemo-plugin-market --port 8099
```

服务默认只监听 `127.0.0.1`。生产环境用 systemd：

```bash
sudo ./deploy/install.sh
```

### 接到反代

把 `deploy/nginx-location.conf` 里的 `location` 放进对应 server 块。三条不能省的：

```nginx
proxy_intercept_errors off;   # 否则 404 被渲染成 HTML 200
proxy_buffering off;          # 索引要实时生效
# 不要 try_files 兜底，不要做目录索引
```

### 在网关里添加插件源

管理界面填索引地址：

```
https://你的域名/index.json
```

如果地址是私网 IP 或 `localhost`，记得在同一条源配置里勾选"允许私网地址"。

### 校验

不用打开网关也能验证一遍：

```bash
python -m kemo_plugin_market verify --url https://你的域名
```

```
[1] 索引可达 635 字节  https://你的域名/index.json
[2] 结构合法，条目数 1
[3] dsh-model-system v1.1.3 34979 字节 摘要一致

通过：1 个条目全部可用
```

## 命令一览

| 命令 | 作用 |
|---|---|
| `build` | 扫描插件包并生成索引 |
| `build --check` | 只校验，不写盘 |
| `build --dry-run` | 打印索引内容，不写盘 |
| `serve` | 启动静态索引服务 |
| `verify --url <地址>` | 对已发布的源做端到端校验 |

## 索引格式

```json
{
  "schema_version": 1,
  "plugins": [
    {
      "id": "dsh-model-system",
      "name": "DSH 模型系统",
      "version": "1.1.3",
      "url": "https://你的域名/packages/dsh-model-system-1.1.3.zip",
      "sha256": "47c57bca…c51e",
      "summary": "独立运行 DSH 模型生态插件…",
      "author": "Kemo Gateway Local",
      "category": "provider_extension",
      "capabilities": ["llm"],
      "compatibility": ">=2.0.0,<3.0.0"
    }
  ]
}
```

字段约束与网关 `CatalogEntry` 一致：

| 字段 | 必填 | 约束 |
|---|---|---|
| `id` | 是 | `^[a-z0-9][a-z0-9_-]{0,63}$` |
| `name` | 是 | 1–100 字符 |
| `version` | 是 | 合法 PEP 440，≤60 字符 |
| `url` | 是 | HTTP(S) 直链，≤2000 字符，不能带 query / fragment / 凭据 |
| `sha256` | 是 | 64 位小写十六进制，等于 ZIP 文件字节摘要 |
| `summary` | 否 | ≤1000 字符 |
| `author` | 否 | ≤200 字符 |
| `category` | 否 | `model_account` / `cli_provider` / `provider_extension` |
| `capabilities` | 否 | ≤32 项，每项 ≤100 字符 |
| `compatibility` | 否 | ≤100 字符 |

整体另外受两个上限约束：索引 ≤2 MiB，条目 ≤500 条。

## 配置

`market.yaml` 只描述"市场怎么发布"，不重复声明插件字段：

```yaml
base_url: https://plugin-gateway.kesepain.com
packages_dir: packages

# 可选：按插件 id 覆盖清单里没有的市场元数据
overrides:
  some_plugin:
    category: model_account
    capabilities: [llm, tools]
    summary: 覆盖清单里的说明
```

`base_url` 里落下的每个包地址都拼成 `<base_url>/packages/<文件名>.zip`，所以**文件名就是下载地址的一部分**，改名后要重新 `build`。

## 数据来源优先级

```
overrides 显式值  >  插件包 plugin.yaml  >  内置默认
```

- `capabilities` 未声明时，有 provider 的插件默认标 `llm`，没有的留空
- `category` 未声明时用清单里的，再缺就落到 `provider_extension`
- `compatibility` 取清单里的 `compatibility.gateway`，缺省 `>=2.0.0,<3.0.0`

## 部署形态

| 位置 | 源地址示例 | 私网开关 |
|---|---|---|
| 云服务器 + 域名 | `https://plugins.example.com/index.json` | 不需要 |
| 内网服务器 | `http://192.168.1.10:8099/index.json` | 必须勾 |
| 本机自测 | `http://127.0.0.1:8099/index.json` | 必须勾 |

内网没有自签证书豁免（网关固定 `verify=True`），只能用 http。

## 关于"只给自己用"

网关的插件源配置**没有任何鉴权入口**：不能配自定义请求头，URL 里不能带 query 也不能带凭据。所以"只给自己"只能靠外部手段：

- 反代层按来源 IP 白名单（家宽 IP 会变，变了就 403）
- 用不可猜测的路径前缀
- 干脆只放内网，不暴露公网

索引里的 `sha256` 只保证"下载到的包和索引承诺的一致"，**不防索引本身被谁看到**。私有插件该保护的还得保护。

## 开发

```bash
pip install -e ".[dev]"
pytest -q
```

测试不依赖网关仓库，本地按 SDK v1 的包结构造最小插件包即可运行。

## 项目结构

```
kemo_plugin_market/
├── schema.py      索引条目与文档的校验规则
├── manifest.py    从 ZIP 读取 plugin.yaml
├── config.py      market.yaml 解析
├── builder.py     扫描 → 生成索引
├── server.py      静态只读服务
└── cli.py         命令行入口
deploy/
├── install.sh            一键安装
├── kemo-market.service   systemd 单元
└── nginx-location.conf   反代片段
tests/                    结构、生成、服务三层测试
```

## 相关项目

| 项目 | 说明 |
|---|---|
| [kemo-agent](https://github.com/kesepain-KE/kemo-agent) | 智物智能体框架本体 |
| [kemo-adapter-api](https://github.com/kesepain-KE/kemo-adapter-api) | Kemo 网关，插件市场的消费方 |
| [kemo-graph](https://github.com/kesepain-KE/kemo-graph) | 本地知识图谱基础设施 |
| [kemo-agent-app](https://github.com/kesepain-KE/kemo-agent-app) | 安卓端应用 |

## 许可

[Apache-2.0](LICENSE)
