<?php
function vh_config(): array {
    static $cfg = null;
    if ($cfg === null) { $cfg = require __DIR__ . '/config.php'; }
    return $cfg;
}

function vh_http(string $url, array $opt = []): array {
    $method  = $opt['method'] ?? 'GET';
    $body    = $opt['body'] ?? null;
    $headers = $opt['headers'] ?? [];
    $timeout = $opt['timeout'] ?? 8;

    if (function_exists('curl_init')) {
        $ch = curl_init($url);
        curl_setopt_array($ch, [
            CURLOPT_RETURNTRANSFER => true,
            CURLOPT_TIMEOUT        => $timeout,
            CURLOPT_FOLLOWLOCATION => true,
            CURLOPT_CUSTOMREQUEST  => $method,
            CURLOPT_HTTPHEADER     => $headers,
            CURLOPT_USERAGENT      => 'vh-latest/' . ($opt['ua'] ?? '1.0'),
        ]);
        if ($body !== null) { curl_setopt($ch, CURLOPT_POSTFIELDS, $body); }
        $raw  = curl_exec($ch);
        $code = (int) curl_getinfo($ch, CURLINFO_HTTP_CODE);
        $err  = curl_error($ch);
        curl_close($ch);
        return ['ok' => $raw !== false && $code < 400, 'code' => $code, 'body' => (string) $raw, 'error' => $err];
    }

    $ctx = stream_context_create(['http' => [
        'method' => $method, 'header' => implode("\r\n", $headers),
        'content' => $body ?? '', 'timeout' => $timeout, 'ignore_errors' => true,
    ]]);
    $raw = @file_get_contents($url, false, $ctx);
    $code = 0;
    foreach ($http_response_header ?? [] as $line) {
        if (preg_match('#^HTTP/\S+\s+(\d{3})#', $line, $m)) { $code = (int) $m[1]; }
    }
    return ['ok' => $raw !== false && $code < 400, 'code' => $code, 'body' => (string) $raw, 'error' => ''];
}

function vh_latest(bool $force = false): array {
    $cfg    = vh_config();
    $cache  = __DIR__ . '/.cache.json';
    $ttl    = 60;
    if (!$force && is_file($cache) && (time() - filemtime($cache)) < $ttl) {
        $hit = json_decode((string) file_get_contents($cache), true);
        if (is_array($hit) && !empty($hit['items'][0]['url'])) { $hit['cached'] = true; return $hit; }
    }
    $res = vh_http($cfg['manifest_url'], ['timeout' => 8]);
    $data = json_decode($res['body'], true);
    if (!is_array($data) || empty($data['items'][0]['url'])) {
        if (is_file($cache)) {
            $old = json_decode((string) file_get_contents($cache), true);
            if (is_array($old)) { $old['stale'] = true; return $old; }
        }
        return ['error' => 'manifest 读取失败', 'code' => $res['code'], 'raw' => substr($res['body'], 0, 200)];
    }
    @file_put_contents($cache, json_encode($data, JSON_UNESCAPED_UNICODE));
    return $data;
}

function vh_trigger(): array {
    $cfg = vh_config();
    if (!$cfg['token']) {
        return ['ok' => false, 'error' => 'config.php 里的 token 还没填'];
    }
    $url = 'https://api.github.com/repos/' . $cfg['repo'] . '/actions/workflows/collect.yml/dispatches';
    $res = vh_http($url, [
        'method'  => 'POST',
        'body'    => json_encode(['ref' => 'main']),
        'headers' => [
            'Authorization: Bearer ' . $cfg['token'],
            'Accept: application/vnd.github+json',
            'Content-Type: application/json',
        ],
        'timeout' => 10,
        'ua'      => 'trigger',
    ]);
    @file_put_contents(__DIR__ . '/.last-trigger', date('c'));
    return ['ok' => $res['ok'], 'code' => $res['code'], 'error' => $res['error']];
}

/**
 * 懒触发：距上次触发超过 $min_interval 秒就顺手让 GitHub 跑一次采集。
 * 用文件锁避免并发访问时重复触发；失败不影响页面本身。
 */
function vh_maybe_dispatch(int $min_interval = 1500): bool {
    $cfg = vh_config();
    if (empty($cfg['token'])) { return false; }

    $lockPath  = __DIR__ . '/.dispatch.lock';
    $stampPath = __DIR__ . '/.last-dispatch';
    $fh = @fopen($lockPath, 'c+');
    if (!$fh) { return false; }
    if (!flock($fh, LOCK_EX | LOCK_NB)) { fclose($fh); return false; }

    try {
        $last = is_file($stampPath) ? (int) file_get_contents($stampPath) : 0;
        if (time() - $last < $min_interval) { return false; }
        // 先落时间戳，避免慢请求期间其它请求重复触发
        @file_put_contents($stampPath, (string) time());
        $result = vh_trigger();
        return (bool) ($result['ok'] ?? false);
    } finally {
        flock($fh, LOCK_UN);
        fclose($fh);
    }
}
