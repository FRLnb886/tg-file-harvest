# vhost/ · 虚拟主机入口页（PHP）

在 Miyo 虚拟主机上跑一组 PHP 页面，提供一个**固定域名的最新文件下载入口**，并可以手动触发一次采集。

> 这台主机（vh-lite）套餐只有 PHP、没有命令执行/终端/定时任务，**不能跑采集本身**；采集仍由 GitHub Actions 完成。详见文末「能力边界」。

## 文件

| 文件 | 作用 |
| --- | --- |
| `index.php` | 状态页：最新文件名 / 大小 / 时间 / 来源，下载按钮，「立即检查」按钮 |
| `latest.php` | 302 跳转到当前最新的 Release 资产（给脚本用的固定入口） |
| `trigger.php` | `?k=<trigger_secret>` 触发一次 GitHub Actions collect |
| `lib.php` | 读 manifest（60 秒文件缓存，失败回退旧缓存）+ 调 GitHub dispatch |
| `config.php` | 运行时生成，**不提交**（`.gitignore` 已排除） |
| `config.example.php` | 配置模板 |
| `index.html` | 覆盖主机默认占位页，跳转到 `index.php` |
| `deploy.py` | 部署与运维脚本（调 Miyo 部署 API） |

## 部署 / 更新

令牌在面板「账号设置 → AI 部署助手」生成（`miyo_dep_` 开头，只显示一次），放环境变量：

```bash
export MIYO_TOKEN='miyo_dep_xxx'

python vhost/deploy.py init --keep-remote   # 首次：生成 config.php 并复用远端已有密钥
python vhost/deploy.py deploy --dry-run     # 先看会传哪些
python vhost/deploy.py deploy               # 真正上传
```

日常只改页面时，直接 `deploy` 即可（`config.php` 会一起上传，保持密钥不变）。

其他运维命令：

```bash
python vhost/deploy.py ls /                 # 列远端目录
python vhost/deploy.py read /config.php     # 读远端文件
python vhost/deploy.py rm /probe.php        # 删远端文件（谨慎）
```

## 配置 config.php

```php
return [
    'repo'           => 'FRLnb886/tg-file-harvest',
    'manifest_url'   => 'https://frlnb886.github.io/tg-file-harvest/data/manifest.json',
    'token'          => '',                      // 填 fine-grained token（Actions: Read and write）
    'trigger_secret' => '随机串',                  // trigger.php 与页面按钮共用
];
```

`token` 为空时页面照常显示最新文件，只是「立即检查」返回 502 并提示；填上 token 后即可用。

## 两个固定地址

- 人看：`https://<临时域名>/`
- 脚本：`https://<临时域名>/latest.php`（302 到最新文件，可直接 `curl -L`）

## 触发方式

### 30 分钟新鲜度：懒触发（默认开启）

入口页自己会按 30 分钟节流触发一次采集，**不需要任何外部定时器**：

- 每次访问 `latest.php` 或 `index.php` 时，若距上次触发超过 25 分钟（`vh_maybe_dispatch(1500)`），就顺手调一次 GitHub dispatch
- 用文件锁（`.dispatch.lock`）避免并发访问重复触发；`.last-dispatch` 记录上次触发时间
- 触发失败或 token 未填时**不影响页面本身**，只是这一轮不刷新

因此：**任何一次下载请求都保证拿到最多 30 分钟旧的数据**。如果你的下载脚本会定时拉 `latest.php`，就等于有了一个 30 分钟的定时器。

### 也可以交给外部定时器（可选）

想完全不管访问、由系统准点触发，就用 cron-job.org 之类打 `trigger.php`：

| 项 | 填什么 |
| --- | --- |
| URL | `https://<临时域名>/trigger.php?k=<trigger_secret>` |
| 请求方法 | `GET` |
| 时区 | `Asia/Shanghai` |
| 计划 | `*/30 * * * *` |

返回码含义：

| 返回 | 含义 |
| --- | --- |
| `{"ok":true,"code":204}` | 已提交给 GitHub，几十秒后 Actions 里能看到运行 |
| `403 forbidden` | `k` 与 `config.php` 的 `trigger_secret` 不一致 |
| `502 config.php 里的 token 还没填` | token 为空或已过期 |

好处：GitHub token 只留在主机，不暴露给定时服务。

> 另一条路是让定时服务直接打 GitHub API（`POST https://api.github.com/repos/<owner>/<repo>/actions/workflows/collect.yml/dispatches`，body `{"ref":"main"}`，头带 `Authorization: Bearer <token>`），少一跳，但 token 要存在第三方。

## 面板域名解析不出来时

`vhost.miyohost.cn` 的 DNS 偶尔会返回 NXDOMAIN（服务本身仍在原 IP 上跑）。这时给部署脚本指定 IP：

```bash
python vhost/deploy.py --api-ip 154.12.63.109 deploy
# 或：export MIYO_API_IP=154.12.63.109
```

脚本用 `curl --resolve` 固定 IP，同时保留 SNI 与证书校验。

## 能力边界（实测于 vh-lite 套餐）

| 能力 | 结果 |
| --- | --- |
| PHP | ✅ 8.5，`curl` 可用 |
| 出网到 GitHub | ✅ `frlnb886.github.io` / `api.github.com` / `github.com` 均通 |
| `POST /api/deploy/exec` | ❌ `403 PLAN_LIMIT`（"当前套餐不包含命令执行/网页终端能力"） |
| 非 PHP 运行时（node/python） | ❌ 套餐只给 `php` |
| 定时任务 | ❌ 面板无此功能 |
| 资源 | 50MB 内存 / 200MB 磁盘 |

想在这台主机上真正跑采集，需要升级到支持 Python 的套餐，并且内存要远大于 50MB（Telethon 需要常驻内存）。
