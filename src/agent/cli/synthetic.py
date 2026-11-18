"""Fixed application fixtures, not MCP envelopes or real Linux evidence."""
from dataclasses import dataclass
import re

from jsonschema import Draft202012Validator

from agent.coordinator import Coordinator, Decision, Evidence, Observation

SCENARIOS = ('active', 'port-conflict', 'permission-denied')
READ_ONLY = frozenset({'service_status', 'query_journal', 'listening_ports'})
_SECRET = re.compile(
    r"(?:password|passwd|secret|token|api_key|mysql_pwd)\w*(?:\\?[\"'])?\s*[=:]|"
    r'password\s+is\s+\S+|\bBearer\s+\S+|://[^/\s]+:[^/\s]+@|'
    r'-----BEGIN .*PRIVATE KEY|synthetic-secret', re.IGNORECASE)
_WRITE = re.compile(r'\b(?:restart(?:_service)?|sudo|reboot|approve)\b|khởi động lại', re.I)


def redact(text):
    """Conservatively suppress a whole sensitive fixture/question, never log it.

    This is not a production real-log redactor. No external logs enter this demo.
    """
    if type(text) is not str:
        raise ValueError('DATA_POLICY')
    return '[DỮ LIỆU ĐÃ CHE]' if _SECRET.search(text) else text


def write_requested(question):
    return bool(_WRITE.search(question))


def schema(name):
    field = 'port' if name == 'listening_ports' else 'unit'
    return {'type': 'object', 'properties': {field: {'const': 80 if field == 'port' else 'nginx.service'}},
            'required': [field], 'additionalProperties': False}


@dataclass(frozen=True)
class Step:
    tool: str
    observation: Observation


def steps_for(scenario):
    if scenario not in SCENARIOS:
        raise ValueError('INVALID_SCENARIO')
    def evidence(identifier, source, text):
        return Observation((Evidence(identifier, 'synthetic:' + source, text),))
    if scenario == 'active':
        return (Step('service_status', evidence('active-01', 'service_status',
                    'nginx.service: active (running); dữ liệu giả lập, chưa kiểm tra HTTP.')),)
    failed = Step('service_status', evidence('failed-01', 'service_status',
                  'nginx.service: failed; dữ liệu giả lập.'))
    if scenario == 'port-conflict':
        return (failed, Step('query_journal', evidence('failed-02', 'query_journal',
                    'bind() to 0.0.0.0:80 failed: Address already in use; dữ liệu giả lập.')),
                Step('listening_ports', evidence('failed-03', 'listening_ports',
                    'port 80: synthetic-other-process; dữ liệu giả lập.')))
    return (failed, Step('query_journal', Observation(limited=True,
                        note='PERMISSION_DENIED: không đủ quyền đọc journal (giả lập).')))


@dataclass(frozen=True)
class ToolEvent:
    """Application-only synthetic trace, never SCRUM-12 wire metadata."""
    tool: str
    status: str
    evidence_ids: tuple[str, ...]


class SyntheticExecutor:
    """Only consumes in-memory steps. No subprocess, SDK, network or tool runner."""
    cleanup_state = 'idle'

    def __init__(self, steps):
        self.steps = steps
        self.calls = []
        self.events = []
        self.closed = False

    async def execute(self, name, arguments):
        if self.closed or name not in READ_ONLY:
            raise ValueError('POLICY_DENIED')
        Draft202012Validator(schema(name)).validate(arguments)
        index = len(self.calls)
        if index >= len(self.steps) or self.steps[index].tool != name:
            raise ValueError('FIXTURE_SEQUENCE')
        self.calls.append(name)
        observation = self.steps[index].observation
        self.events.append(ToolEvent(name, 'permission_denied' if observation.limited else 'ok',
                                     tuple(item.id for item in observation.evidence)))
        return observation

    async def finish_cleanup(self):
        self.closed = True


class ScriptedDecisions:
    """Fixed choices; user text cannot select a tool, registry, params or policy."""
    def __init__(self, scenario, steps):
        self.scenario, self.steps, self.index = scenario, steps, 0

    async def choose(self, question, observations):
        if self.index < len(self.steps):
            name = self.steps[self.index].tool
            self.index += 1
            return Decision(tool=name, arguments={'port': 80} if name == 'listening_ports'
                            else {'unit': 'nginx.service'})
        conclusions = {
            'active': 'Trong kịch bản giả lập, nginx.service active; chưa xác minh HTTP health.',
            'port-conflict': 'Trong kịch bản giả lập, nginx.service failed với bằng chứng xung đột port 80; chưa xác minh trên Linux thật.',
            'permission-denied': 'Không đủ bằng chứng xác định nguyên nhân; thiếu quyền đọc journal giả lập.',
        }
        ids = tuple(e.id for o in observations for e in o.evidence)
        return Decision(conclusion=conclusions[self.scenario], citations=ids)


def make_coordinator(scenario, *, seconds=120, model=None, executor=None):
    steps = steps_for(scenario)
    coordinator = Coordinator(
        model if model is not None else ScriptedDecisions(scenario, steps),
        executor if executor is not None else SyntheticExecutor(steps),
        read_only=READ_ONLY,
        scope_allows=lambda name, args: args == ({'port': 80} if name == 'listening_ports'
                                               else {'unit': 'nginx.service'}),
        interpret=lambda result: result, redact=redact, seconds=seconds)
    return coordinator
