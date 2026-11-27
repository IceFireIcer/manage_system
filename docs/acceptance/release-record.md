# 发布记录

## 版本

| 项 | 值 |
| --- | --- |
| 发布日期 | |
| 发布人 | |
| 提交 SHA | |
| 版本标签（可选） | |
| 门禁镜像标签 | `sha-<short>` |

## 同提交 CI 与镜像

| 项 | 值 |
| --- | --- |
| CI run 编号 / 链接 | |
| CI 结论 | |
| `labstock-api` digest | |
| `labstock-web` digest | |
| `release-manifest.json` artifact | |
| 门禁结论（deployable） | |

## 部署与回滚

| 项 | 值 |
| --- | --- |
| 升级命令 | `IMAGE_TAG=sha-<short> docker compose pull && docker compose up -d` |
| 升级后前后端登录结果 | |
| 回滚标签 | |
| 回滚后前后端登录结果 | |
| 回滚后迁移版本 | |
| 备份文件 / manifest | |

## 结论

- [ ] 两份镜像齐全且 digest 与同一提交绑定
- [ ] CI 结论为该提交的成功结果
- [ ] `release-manifest.json` 已归档
- [ ] 升级后三角色可登录
- [ ] 回滚演练结果已记录

备注：