<?php
// 复制成 config.php 后按需修改（config.php 已在 .gitignore 中，不会提交）
return [
    'repo'           => 'FRLnb886/tg-file-harvest',
    'manifest_url'   => 'https://frlnb886.github.io/tg-file-harvest/data/manifest.json',
    // 建议用 fine-grained token，只勾选 Actions: Read and write
    'token'          => '',
    // 触发密钥：建议改成你自己的随机串，本文件的触发接口和页面按钮都会用到
    'trigger_secret' => 'CHANGE_ME_RANDOM_SECRET',
];
