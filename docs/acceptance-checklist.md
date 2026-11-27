# 验收清单

逐项核对 SPEC v1.1 验收标准。自动测试位于 `backend/tests/`，在 `backend` 目录执行：

```bash
pytest
ruff check .
```

本轮结果：**238 passed**；`ruff check .` 通过；前端 `npm run build` 成功；真实 API 冒烟主线通过（2026-11-27）。

## 1. 账号、权限与用户管理

| 验收项 | 自动验证 | 人工核对 | 结果 |
| ------ | -------- | -------- | ---- |
| 三角色登录、JWT Cookie、未登录 401 | `test_auth.py::test_login_success_for_each_role`、`test_me_requires_login`、`test_login_success_sets_secure_cookie` | 三个标签页分别登录 | 通过 |
| 三角色菜单与接口权限矩阵 | `test_auth.py::test_student_forbidden_on_restricted_route`、`test_teacher_forbidden_on_user_management`；`test_consumables.py::test_non_admin_create_forbidden`；`test_requisitions.py::test_student_cannot_register_for_others`；`test_reversal.py::test_student_cannot_reverse` | 切换三角色观察侧边栏 | 通过 |
| 停用账号不可登录，历史保留 | `test_auth.py::test_disabled_account_login_rejected`、`test_users.py::test_disable_then_login_rejected_and_record_kept` | 用户管理页启停学生 | 通过 |
| 班委只能新增/启停学生，不能提权 | `test_users.py::test_student_forbidden_on_all_user_endpoints`、`test_teacher_forbidden_on_all_user_endpoints`、`test_admin_cannot_create_teacher_or_admin`、`test_admin_cannot_change_own_status` | 新增用户对话框角色固定学生 | 通过 |
| 首次启动自动创建管理员、幂等 | — | `docker compose logs backend \| grep bootstrap`；重启后输出「管理员账号已存在，跳过」 | 通过 |

## 2. 耗材档案与领用

| 验收项 | 自动验证 | 人工核对 | 结果 |
| ------ | -------- | -------- | ---- |
| 迁移仅建四张核心表、无预置业务数据 | `test_migrations.py::test_alembic_upgrade_creates_four_tables_without_seed_rows` | 新库档案页为空 | 通过 |
| 建档字段校验、同班重名拒绝、安全线非负 | `test_consumables.py::test_create_negative_safety_stock_rejected`、`test_create_duplicate_name_in_same_class_rejected`、`test_create_blank_name_rejected` | 新增对话框 | 通过 |
| 修改档案、停用保留历史、重新启用 | `test_consumables.py::test_update_name_and_safety_stock_success`、`test_disable_and_default_list_filters_it`、`test_reenable_consumable` | 档案页停用/启用 | 通过 |
| 批量领用最多 5 行、多行成功 | `test_requisitions.py::test_three_items_success`、`test_more_than_five_rows_rejected`、`test_same_consumable_two_rows_accumulate` | 两行领用并提交 | 通过 |
| 任一行不足则整单回滚、指出具体行 | `test_requisitions.py::test_any_row_insufficient_rejects_whole_order`、`test_same_consumable_rows_exhaust_stock` | 旁白说明，测试覆盖 | 通过 |
| 补录 30 天内、禁止未来时间 | `test_requisitions.py::test_created_at_31_days_ago_rejected`、`test_created_at_29_days_ago_accepted`、`test_created_at_in_future_rejected` | 领用时间选择器 | 通过 |
| 并发无超卖（条件原子扣减） | `test_requisitions.py::test_no_oversell_sequential_submissions` | 见 SPEC 方言说明 | 通过 |

## 3. 撤销与查询

| 验收项 | 自动验证 | 人工核对 | 结果 |
| ------ | -------- | -------- | ---- |
| 撤销回加库存、原单标记、生成红冲 | `test_reversal.py::test_teacher_reverse_restores_stock_and_creates_reversal`、`test_admin_reverse_success` | 记录页撤销两笔 | 通过 |
| 撤销四态拒绝（重复撤销/撤销红冲/空原因/学生） | `test_reversal.py::test_reverse_twice_rejected`、`test_reverse_reversal_record_rejected`、`test_blank_reason_rejected`、`test_student_cannot_reverse` | 测试覆盖 | 通过 |
| 双向明细链路（origin / reversal） | `test_reversal.py::test_detail_bidirectional_link`、`test_student_cannot_view_others_detail` | 记录详情 | 通过 |
| 记录筛选（耗材/领用人/日期/类型/状态） | `test_requisition_query.py::test_list_filter_by_consumable`、`test_list_filter_by_user`、`test_list_filter_by_date_range`、`test_list_filter_by_type_and_status`、`test_list_filter_combination` | 记录页筛选条 | 通过 |
| 学生仅见本人流水 | `test_requisition_query.py::test_list_student_sees_only_own_records`、`test_list_student_user_id_param_forced_self` | 学生标签页记录页 | 通过 |
| CSV 导出（UTF-8 BOM、表头、筛选、学生 403） | `test_requisition_query.py::test_export_csv_content_and_headers`、`test_export_respects_filters`、`test_export_student_forbidden` | 记录页“导出 CSV” | 通过 |

## 4. 库存总览、建议与处置

| 验收项 | 自动验证 | 人工核对 | 结果 |
| ------ | -------- | -------- | ---- |
| 指标卡（总类数/低库存数/今日笔数） | `test_inventory.py::test_overview_summary_counts`、`test_overview_today_requisitions` | 总览三张卡 | 通过 |
| 低库存/缺货置顶与状态标签 | `test_inventory.py::test_overview_items_ordering`、`test_overview_safety_zero_not_low_but_out`、`test_overview_item_fields` | 新建低库存耗材后置顶标红 | 通过 |
| 巡检按紧急→一般→观察排序，字段完整 | `test_inventory_agent.py::test_suggestions_sorted_tiers_with_metrics` | 建议页分级卡片 | 通过 |
| 规则引擎边界（S=0、30%、D=1/3、趋势 50%、Qprev7=0、ceil） | `test_agent_rules.py` 全部参数化用例 | 测试覆盖 | 通过 |
| 正常档不出建议且不调用大模型 | `test_inventory_agent.py::test_normal_tier_does_not_call_llm`、`test_agent_rules.py::test_normal_or_skipped_tier` | 库存充足耗材无卡 | 通过 |
| 大模型超时/无 Key 降级规则模板 | `test_inventory_agent.py::test_rationale_falls_back_on_llm_timeout`、`test_rationale_falls_back_without_api_key` | 未配置 Key 时功能正常 | 通过 |
| 巡检缓存 10 分钟、重新巡检刷新、入库清缓存 | `test_inventory_agent.py::test_cache_reused_until_refresh`、`test_restock_success_updates_stock_logs_and_cache` | 建议页“重新巡检” | 通过 |
| 处置入库仅班委、原子加库存、写日志 | `test_inventory_agent.py::test_restock_forbidden_for_teacher_and_student`、`test_restock_rejects_invalid_payload`、`test_restock_unknown_consumable_returns_404` | 低库存耗材处置入库 | 通过 |

## 5. 统计、日志与工程交付

| 验收项 | 自动验证 | 人工核对 | 结果 |
| ------ | -------- | -------- | ---- |
| 净消耗 = 领用 − 红冲（负值截断），7/30 天口径 | `test_consumption.py::test_summary_net_consumption_and_zero_clamp`、`test_summary_reversed_normal_flow_is_offset_by_reversal`、`test_summary_window_7_boundaries`、`test_summary_window_30_includes_older_flow` | 消耗汇总切换 7/30 天 | 通过 |
| 撤销后统计自洽 | `test_consumption.py::test_summary_matches_requisition_records` | 领用并撤销后净消耗归零 | 通过 |
| 库存反推审计（流水 + 补货事件） | `test_consumption.py::test_audit_reconstructs_stock_from_all_sources`、`test_reconstruct_stock_detects_inconsistent_sequence` | 可选展示审计接口 | 通过 |
| 操作日志覆盖登录/领用/撤销/档案/用户/入库 | `test_logs.py::test_login_and_logout_are_logged`、`test_requisition_reversal_and_restock_logged`、`test_consumable_lifecycle_logged_with_snapshots`、`test_user_create_logged_without_password` | 日志页筛选各动作 | 通过 |
| 日志不含敏感字段、只读、分页 | `test_logs.py::test_logs_have_no_sensitive_fields`、`test_logs_read_only`、`test_logs_pagination_and_desc_order` | 日志页 | 通过 |
| 全量自动测试与 Lint | `cd backend && pytest`、`cd backend && ruff check .` | 238 passed；ruff 通过 | 通过 |
| 前端生产构建 | `cd frontend && npm run build` | 构建成功 | 通过 |
| 本地零 Docker 启动 | — | `scripts/dev-start.ps1` / `dev-start.sh`；见 README | 通过 |
| 云端部署与 GitHub Actions + GHCR 镜像 | `docs/deploy.md` 第 13 节 | 由用户在自有服务器执行 | 待用户执行 |

## 6. 遗留事项（不属于代码验收）

- 云端 ECS 开通、DeepSeek API Key、真实安全库存校准由用户在部署前完成，见 [delivery.md](delivery.md) 与 [deploy.md](deploy.md)
- 管理员初始密码需在首次启动后立即修改，见 [README「管理员初始化」](../README.md#管理员初始化)