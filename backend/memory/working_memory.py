from dataclasses import dataclass, field
from typing import Optional


@dataclass
class TurnContext:
    turn_id: str
    question_id: str
    question_text: str
    user_answer: Optional[str] = None
    evaluation: Optional[dict] = None
    clarification_rounds: int = 0


@dataclass
class WorkingMemory:
    session_id: str
    current_stage_id: int = 0
    stages: list[dict] = field(default_factory=list)
    current_turn: Optional[TurnContext] = None
    stage_turns: list[TurnContext] = field(default_factory=list)
    pending_questions: list[dict] = field(default_factory=list)
    current_explanation: str = ""
    stage_evaluations: list[dict] = field(default_factory=list)
    current_attempt: int = 1
    source_corpus: str = ""
    question_mode: str = "short_answer"
    current_teaching_intent: Optional[dict] = None

    def get_compressed_history(self, max_turns: int = 3) -> list[dict]:
        recent = self.stage_turns[-max_turns:]
        return [
            {"q": t.question_text, "a": t.user_answer or ""}
            for t in recent
            if t.user_answer
        ]

    def record_completed_turn(self) -> None:
        if self.current_turn:
            self.stage_turns.append(self.current_turn)
            if self.current_turn.evaluation:
                self.stage_evaluations.append(self.current_turn.evaluation)
            self.current_turn = None

    def reset_for_new_stage(self, stage_id: int) -> None:
        self.current_stage_id = stage_id
        self.current_turn = None
        self.stage_turns = []
        self.pending_questions = []
        self.current_explanation = ""
        self.stage_evaluations = []
        self.current_attempt = 1
        self.current_teaching_intent = None


_store: dict[str, WorkingMemory] = {}


def get_working_memory(session_id: str) -> WorkingMemory:
    if session_id not in _store:
        _store[session_id] = WorkingMemory(session_id=session_id)
    return _store[session_id]


def delete_working_memory(session_id: str) -> None:
    _store.pop(session_id, None)
