# 验收记录

本目录存放只能在部署/验收环境完成的证据模板，与自动测试和 CI 门禁配套：

| 文件 | 用途 | 执行时机 |
| --- | --- | --- |
| [browser-checklist.md](browser-checklist.md) | 三角色登录、弱网、部分到货、库存审计与可访问性 | 每次发布候选版本 |
| [mysql-concurrency-record.md](mysql-concurrency-record.md) | 真实 MySQL 反序领用、入库/红冲、重复请求与回执丢失 | 每次发布候选版本 |
| [release-record.md](release-record.md) | 同提交 CI、两份镜像 digest、升级/回滚结果 | 每次发布 |

演示数据装载与门禁命令见 [deploy.md](../deploy.md) 第 13.5、13.6 节。记录中的截图、请求编号、digest 与 CI 链接必须可复查；模板中未填写的项不得标记“通过”。