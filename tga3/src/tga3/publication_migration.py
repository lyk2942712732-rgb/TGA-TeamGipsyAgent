"""Update only obsolete publication wording in existing user-editable prompts."""

def update_prompt(text: str) -> str:
    return (text.replace('finding_publish(action_id, claim, scope)',
                         '在 /findings/ 写独立 JSON（action_id、claim、scope，可选 preconditions）')
            .replace('finding_publish', '写入 /findings/ 下的独立 JSON')
            .replace('确认 saved=true 且返回 entry.id', '确认系统导入回执 status=imported 且返回 entry_id')
            .replace('blackboard_publish(conclusion, rationale, finding_ids)',
                     '写入 /workspace/result/final-candidate.json（conclusion、rationale、finding_ids）')
            .replace('blackboard_publish', '写入 /workspace/result/final-candidate.json')
            .replace('调用 在 /findings/ 写独立 JSON', '在 /findings/ 写独立 JSON')
            .replace('用 在 /findings/ 写独立 JSON', '在 /findings/ 写独立 JSON')
            .replace('用 写入 /workspace/result/final-candidate.json',
                     '通过写入 /workspace/result/final-candidate.json'))
