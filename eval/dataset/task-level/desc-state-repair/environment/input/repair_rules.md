# REPAIR 阶段规则与 Schema

## 修订原则
- 只在窗口证据明确支持时才修改；模棱两可的留在原状或写进 `ambiguities`。
- 不要删掉已有合理事实；以增补和澄清为优先。
- 若 desc_state 与窗口证据矛盾，按时间序列以更晚/更明确的证据为准。
- 未解决问题写入 `open_questions`，延期事项写入 `deferred_followups`，**不要**塞进
  `ambiguities`。
- **不要使用** `...`、`…`、`省略`、`etc.`、`and so on`、`TODO`、`TBD` 等截断式占位表达。
  某个事实缺失时写 `unknown` 并（在该字段内）说明缺失维度。
- 中文“等”仅当替代“必须完整列出的封闭清单、否则任务不可执行/不可验收”时才算截断式占位；
  普通开放类别或非关键举例中的“等”不算问题。

## Schema（输出必须完全符合）
```json
{
  "task_overview":  { "main_goal": "...", "task_threads": ["..."], "final_status": "..." },
  "user_intent":    { "current_requirements": ["..."], "superseded_requirements": ["..."],
                      "ambiguities": ["..."], "open_questions": ["..."],
                      "deferred_followups": ["..."], "user_persona_hint": "..." },
  "workspace":      { "working_directories": ["..."],
                      "files_or_paths": [{"path": "...", "purpose": "..."}],
                      "data_files": [{"path": "...", "purpose": "..."}],
                      "services_or_modules": [{"name": "...", "role": "..."}],
                      "environment_constraints": ["..."], "capabilities_required": ["..."] },
  "reward":         { "reference_solution_path": "...", "verifiable_checks": ["..."] }
}
```
