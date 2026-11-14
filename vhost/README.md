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

主机没有定时任务，所以「每 30 分钟检查一次」需要外部定时器（如 cron-job.org）打这个地址：

```
https://<临时域名>/trigger.php?k=<trigger_secret>
```

好处：GitHub token 只留在主机，不暴露给定时服务。

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
