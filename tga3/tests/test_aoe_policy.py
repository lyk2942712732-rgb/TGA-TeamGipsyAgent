from uuid import uuid4

from tga3.aoe_models import active_attempts, joint_ready, next_joint_question, next_solo_question, schedulable_questions
from tga3.domain import TaskRun

WORKERS = ("worker-openai", "worker-claude")


def question(name, difficulty=None, tried=(), running=None, status="queued", ordinal=0):
    attempts = [{"agent_id": agent, "phase": "solo", "outcome": "timeout"} for agent in tried]
    if running:
        attempts.append({"agent_id": running, "phase": "solo", "outcome": None})
    return TaskRun(
        title=name,
        scene_id="aoe",
        parent_task_id=uuid4(),
        metadata={
            "difficulty": difficulty,
            "ordinal": ordinal,
            "status": status,
            "attempts": attempts,
        },
    )


def test_untouched_precedes_easy_peer_question_and_known_difficulty_orders_new_questions():
    peer_easy = question("peer easy", "easy", tried=[WORKERS[1]])
    new_hard, new_easy = question("new hard", "hard"), question("new easy", "easy")
    tasks = [peer_easy, new_hard, new_easy]
    assert next_solo_question(tasks, WORKERS[0]).id == new_easy.id
    assert next_solo_question([peer_easy, new_hard], WORKERS[0]).id == new_hard.id
    assert next_solo_question([peer_easy], WORKERS[0]).id == peer_easy.id


def test_no_repeat_or_simultaneous_ownership_and_wait_does_not_force_joint_early():
    busy = question("busy", running=WORKERS[1])
    own = question("already tried", tried=[WORKERS[0]])
    both = question("both tried", tried=WORKERS)
    assert next_solo_question([busy, own, both], WORKERS[0]) is None
    assert not joint_ready([busy, own, both], WORKERS)
    assert not joint_ready([own, both], WORKERS)
    assert joint_ready([both], WORKERS)
    assert next_joint_question([both]).id == both.id
    assert active_attempts(busy)[0]["agent_id"] == WORKERS[1]


def test_terminal_questions_never_lease_and_unknown_difficulty_preserves_source_order():
    solved = question("solved", "easy", status="solved")
    abandoned = question("abandoned", "easy", status="abandoned")
    earlier, later = question("earlier", "unrated", ordinal=1), question("later", ordinal=2)
    assert next_solo_question([solved, abandoned, later, earlier], WORKERS[0]).id == earlier.id
    assert not joint_ready([solved, abandoned], WORKERS)


def test_only_collection_limits_difficulty_and_original_priority_returns_afterwards():
    easy = question("easy retry", "简单", tried=[WORKERS[1]])
    hard = question("untouched hard", "hard")
    assert schedulable_questions([easy, hard], collection_complete=False) == [easy]
    assert next_solo_question(schedulable_questions([easy, hard]), WORKERS[0]).id == hard.id
    unrated = question("unrated", "unrated")
    assert schedulable_questions([unrated], collection_complete=False) == [unrated]
    assert schedulable_questions([hard, unrated], collection_complete=False) == []
