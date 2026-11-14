<?php
require __DIR__ . '/lib.php';
$cfg  = vh_config();
$data = vh_latest();
$item = $data['items'][0] ?? null;
$triggerResult = null;
$secret = (string) ($_POST['k'] ?? '');
if ($_SERVER['REQUEST_METHOD'] === 'POST' && $secret === $cfg['trigger_secret']) {
    $triggerResult = vh_trigger();
}
$lastTrigger = is_file(__DIR__ . '/.last-trigger') ? trim((string) file_get_contents(__DIR__ . '/.last-trigger')) : null;
function h($v) { return htmlspecialchars((string) $v, ENT_QUOTES, 'UTF-8'); }
function size_h($b) {
    $b = (float) $b;
    foreach (['B', 'KB', 'MB', 'GB'] as $unit) {
        if ($b < 1024 || $unit === 'GB') { return round($b, $b < 10 ? 1 : 0) . ' ' . $unit; }
        $b /= 1024;
    }
    return $b . ' B';
}
?>
<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex,nofollow">
<title>最新文件下载入口</title>
<style>
:root{color-scheme:dark;--bg:#0d0f13;--panel:#161a21;--line:#262c36;--text:#e8eaee;--muted:#8d95a3;--accent:#4c8dff;--ok:#35c48a}
*{box-sizing:border-box}
body{margin:0;padding:28px 16px 48px;background:radial-gradient(900px 400px at 20% -10%,#1b2436,var(--bg) 60%);color:var(--text);font:15px/1.6 -apple-system,"Segoe UI","Noto Sans SC",system-ui,sans-serif}
.wrap{max-width:720px;margin:0 auto;display:flex;flex-direction:column;gap:16px}
h1{margin:0;font-size:20px}
.card{padding:18px;border:1px solid var(--line);border-radius:14px;background:linear-gradient(180deg,var(--panel),#111419)}
.muted{color:var(--muted);font-size:13px}
.name{font-size:17px;font-weight:600;word-break:break-all;margin:6px 0}
.meta{display:flex;gap:14px;flex-wrap:wrap;color:var(--muted);font-size:12.5px;margin-bottom:14px}
.meta b{color:var(--text);font-weight:600}
.btn{display:inline-flex;align-items:center;justify-content:center;gap:6px;padding:11px 18px;border:1px solid transparent;border-radius:10px;background:linear-gradient(135deg,var(--accent),#3f6fe0);color:#fff;font:inherit;font-weight:600;text-decoration:none;cursor:pointer}
.btn.ghost{background:transparent;border-color:var(--line);color:var(--text);font-weight:500}
.row{display:flex;gap:10px;flex-wrap:wrap;align-items:center}
code{background:#00000055;border-radius:6px;padding:2px 6px;font-family:ui-monospace,Menlo,monospace;font-size:12.5px;word-break:break-all}
.tag{display:inline-block;padding:2px 9px;border-radius:999px;font-size:11.5px;border:1px solid #2b3442;color:var(--muted)}
.tag.ok{border-color:#2f6b52;color:#6fe0ad;background:#12251d}
.tag.bad{border-color:#6b3038;color:#ff8b95;background:#251315}
pre{margin:0;white-space:pre-wrap;word-break:break-all;font-size:12.5px;color:var(--muted)}
form{margin:0}
</style>
</head>
<body>
<div class="wrap">
  <h1>最新文件下载入口</h1>
  <p class="muted">这个页面只做一件事：把「当前最新」的文件直链给你，永远指向最新版本。</p>

  <?php if ($item): ?>
  <div class="card">
    <span class="tag <?= !empty($data['stale']) ? 'bad' : 'ok' ?>">
      <?= !empty($data['stale']) ? '缓存回退' : (!empty($data['cached']) ? '缓存 60s' : '实时读取') ?>
    </span>
    <div class="name"><?= h($item['name']) ?></div>
    <div class="meta">
      <span>大小 <b><?= h(size_h($item['size'] ?? 0)) ?></b></span>
      <span>来源 <b><?= h($item['from'] ?? '-') ?></b></span>
      <span>时间 <b><?= h(isset($item['ts']) ? date('Y-m-d H:i', (int) $item['ts']) : '-') ?></b></span>
    </div>
    <div class="row">
      <a class="btn" href="latest.php">下载最新</a>
      <a class="btn ghost" href="<?= h($item['url']) ?>">直达链接</a>
      <a class="btn ghost" href="?refresh=1">刷新状态</a>
    </div>
    <p class="muted" style="margin:12px 0 0">脚本用这个固定地址即可：<code><?= h((isset($_SERVER['HTTPS']) ? 'https://' : 'http://') . $_SERVER['HTTP_HOST']) ?>/latest.php</code></p>
  </div>
  <?php else: ?>
  <div class="card"><span class="tag bad">读取失败</span>
    <p class="muted"><?= h($data['error'] ?? '未知原因') ?></p>
  </div>
  <?php endif; ?>

  <div class="card">
    <div class="row" style="justify-content:space-between">
      <div>
        <b>触发一次采集</b>
        <div class="muted">让 GitHub Actions 立刻去频道检查是否有新版本</div>
      </div>
      <form method="post">
        <input type="hidden" name="k" value="<?= h($cfg['trigger_secret']) ?>">
        <button class="btn ghost" type="submit">立即检查</button>
      </form>
    </div>
    <?php if ($triggerResult !== null): ?>
      <p class="muted" style="margin:12px 0 0">
        上次触发：<span class="tag <?= $triggerResult['ok'] ? 'ok' : 'bad' ?>">HTTP <?= h($triggerResult['code']) ?></span>
        <?= $triggerResult['ok'] ? '已提交，几十秒后在仓库 Actions 里能看到运行。' : h($triggerResult['error'] ?: '失败，检查 token 权限') ?>
      </p>
    <?php elseif ($lastTrigger): ?>
      <p class="muted" style="margin:12px 0 0">上次触发时间：<?= h($lastTrigger) ?></p>
    <?php endif; ?>
  </div>

  <div class="card">
    <b>说明</b>
    <p class="muted" style="margin:8px 0 0">
      这台主机只能做「固定入口 + 手动触发」，实际采集由 GitHub Actions 完成（每 30 分钟，可能延迟）。<br>
      页面数据源：<code><?= h($cfg['manifest_url']) ?></code>
    </p>
  </div>
</div>
</body>
</html>
