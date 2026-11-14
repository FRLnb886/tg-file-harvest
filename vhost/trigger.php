<?php
require __DIR__ . '/lib.php';
$cfg = vh_config();
header('Content-Type: application/json; charset=utf-8');
if (($_GET['k'] ?? '') !== $cfg['trigger_secret']) {
    http_response_code(403);
    echo json_encode(['ok' => false, 'error' => 'forbidden'], JSON_UNESCAPED_UNICODE);
    exit;
}
$result = vh_trigger();
if (!$result['ok']) { http_response_code(502); }
echo json_encode($result, JSON_UNESCAPED_UNICODE);
