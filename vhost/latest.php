<?php
require __DIR__ . '/lib.php';
$data = vh_latest(!empty($_GET['refresh']));
if (empty($data['items'][0]['url'])) {
    http_response_code(503);
    header('Content-Type: text/plain; charset=utf-8');
    echo "最新文件暂时取不到：" . ($data['error'] ?? '未知原因');
    exit;
}
$item = $data['items'][0];
header('Cache-Control: no-store');
header('Location: ' . $item['url'], true, 302);
