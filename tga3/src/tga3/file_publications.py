"""System-owned import of agent-authored Finding and final-candidate files."""

import asyncio
import hashlib
import json
from collections import defaultdict
from pathlib import Path
from uuid import uuid4

from .action_findings import ActionFindingInput, ActionFindingPublisher
from .domain import FinalCandidateBody
from .errors import ContractError
from .final_candidates import FinalCandidatePublisher


class FilePublications:
    def __init__(self, config, board):
        self.config, self.board = config, board
        self.findings = ActionFindingPublisher(config, board)
        self.finals = FinalCandidatePublisher(config, board)
        self.locks = defaultdict(asyncio.Lock)
        self.question_locks = defaultdict(asyncio.Lock)
        self.versions = {}

    def _receipt_path(self, task_id, agent_id, label):
        token = hashlib.sha256(label.encode()).hexdigest()
        return self.config.task_path(task_id, 'workspaces', agent_id) / '.tga3/publication-receipts' / (token+'.json')

    @staticmethod
    def _save(path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix('.'+uuid4().hex+'.tmp')
        try:
            temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)

    def _read(self, task_id, agent_id, path, label):
        if path.is_symlink() or path.resolve() != path.absolute() or not path.is_file():
            raise ContractError('Publication must be a regular file in the assigned agent directory')
        if path.stat().st_size > 128*1024:
            raise ContractError('Publication JSON exceeds 128 KiB')
        data = path.read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        receipt_path = self._receipt_path(task_id, agent_id, label)
        previous = json.loads(receipt_path.read_text()) if receipt_path.is_file() else {}
        if previous.get('status') == 'imported':
            if previous['sha256'] != digest:
                raise ContractError('Imported publication is immutable; write a new Finding file for a new fact')
            return None, digest, receipt_path
        return json.loads(data), digest, receipt_path

    async def sync_findings(self, task_id, agent_id):
        if self.config.agents.agents[agent_id].role != 'worker':
            return []
        root = self.config.task_path(task_id, 'findings', agent_id)
        if not root.exists():
            return []
        if root.resolve() != root.absolute():
            raise ContractError('Finding directory cannot be a symlink')
        receipts = []
        async with self.locks[task_id, agent_id]:
            for path in sorted(root.glob('*.json')):
                label = '/findings/'+path.name
                version = (path.stat().st_mtime_ns, path.stat().st_ctime_ns, path.stat().st_size)
                key = (task_id, agent_id, label)
                if self.versions.get(key) == version:
                    continue
                try:
                    value, digest, receipt_path = self._read(task_id, agent_id, path, label)
                    if value is None:
                        self.versions[key] = version
                        receipts.append(json.loads(receipt_path.read_text()))
                        continue
                    request = ActionFindingInput.model_validate(value)
                    result = await self.findings.publish(task_id, agent_id, request)
                    receipt = {'path': label, 'status': 'imported', 'entry_id': result['entry']['id'],
                               'action_id': request.action_id, 'sha256': digest}
                    self._save(receipt_path, receipt)
                    self.versions[key] = version
                except Exception as exc:
                    receipt = {'path': label, 'status': 'error', 'error': str(exc)}
                receipts.append(receipt)
        return receipts

    def final_pending(self, task_id, agent_id):
        path = self.config.task_path(task_id, 'workspaces', agent_id) / 'final-candidate.json'
        receipt = self._receipt_path(task_id, agent_id, '/workspace/final-candidate.json')
        return path.is_file() and (not receipt.is_file() or
               json.loads(receipt.read_text()).get('status') != 'imported')

    async def sync_final(self, task_id, agent_id):
        if self.config.agents.agents[agent_id].role != 'worker' or not self.final_pending(task_id, agent_id):
            return []
        label = '/workspace/final-candidate.json'
        path = self.config.task_path(task_id, 'workspaces', agent_id) / 'final-candidate.json'
        async with self.locks[task_id, agent_id], self.question_locks[task_id]:
            try:
                value, digest, receipt_path = self._read(task_id, agent_id, path, label)
                if value is None:
                    return []
                body = FinalCandidateBody.model_validate(value)
                result = await self.finals.publish(task_id, agent_id, body, 'general')
                # The first accepted candidate is the same one AOE settles by seq.
                # Only the system writes this shared canonical file.
                canonical = self.config.task_path(task_id) / 'final-candidate.json'
                if not canonical.exists():
                    self._save(canonical, result['body'])
                receipt = {'path': label, 'status': 'imported', 'entry_id': result['id'], 'sha256': digest}
                self._save(receipt_path, receipt)
                return [receipt]
            except Exception as exc:
                waiting = isinstance(exc, ContractError) and 'Unread blackboard updates' in str(exc)
                return [{'path': label, 'status': 'waiting_for_blackboard' if waiting else 'error', 'error': str(exc)}]
