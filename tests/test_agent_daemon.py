"""Tests for agent_daemon and subtask_chunker."""
import os, sys, tempfile
from pathlib import Path
SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))
import pytest

def test_import_agent_daemon():
    import agent_daemon
    assert agent_daemon is not None

def test_import_subtask_chunker():
    import subtask_chunker
    assert subtask_chunker is not None

class TestAgentConfig:
    def test_defaults(self):
        from agent_daemon import AgentConfig
        cfg = AgentConfig(agent_id="ta", cli_path="/usr/bin/test")
        assert cfg.agent_id == "ta"
        assert cfg.max_subtask_timeout == 600
        assert cfg.max_retries == 3
        assert cfg.checkpoint_dir == ".checkpoints"

    def test_custom(self):
        from agent_daemon import AgentConfig
        cfg = AgentConfig(agent_id="c", cli_path="/b", max_subtask_timeout=30, max_retries=5)
        assert cfg.max_subtask_timeout == 30
        assert cfg.max_retries == 5

    def test_post_init_work_dir(self):
        from agent_daemon import AgentConfig
        cfg = AgentConfig(agent_id="a", cli_path="/b", work_dir="")
        assert cfg.work_dir == str(Path.cwd())

    def test_post_init_checkpoint_dir(self):
        from agent_daemon import AgentConfig
        cfg = AgentConfig(agent_id="a", cli_path="/b", checkpoint_dir="")
        assert cfg.checkpoint_dir == ".checkpoints"


class TestExecuteSubtask:
    def test_real_command_success(self):
        from agent_daemon import AgentConfig, AgentDaemon
        from message_queue import MessageQueue, Task
        mq = MessageQueue(":memory:")
        cfg = AgentConfig(agent_id="test", cli_path="echo")
        daemon = AgentDaemon(cfg, mq)
        task = Task(target_agent="test", task_type="code", max_retries=0)
        result = daemon._execute_subtask(task, attempt=1)
        assert result.success is True
        assert result.exit_code == 0
        assert result.attempt == 1
        assert result.agent_id == "test"

    def test_real_command_failure(self):
        from agent_daemon import AgentConfig, AgentDaemon
        from message_queue import MessageQueue, Task
        mq = MessageQueue(":memory:")
        cfg = AgentConfig(agent_id="test", cli_path="python")
        daemon = AgentDaemon(cfg, mq)
        task = Task(target_agent="test", task_type="-c", context={"args":["exit(1)"]}, max_retries=0)
        result = daemon._execute_subtask(task, attempt=1)
        assert result.success is False
        assert result.exit_code == 1

    def test_command_not_found(self):
        from agent_daemon import AgentConfig, AgentDaemon
        from message_queue import MessageQueue, Task
        mq = MessageQueue(":memory:")
        cfg = AgentConfig(agent_id="test", cli_path="/nonexistent/xyz")
        daemon = AgentDaemon(cfg, mq)
        task = Task(target_agent="test", task_type="code", max_retries=0)
        result = daemon._execute_subtask(task, attempt=1)
        assert result.success is False
        assert result.exit_code == -1

    def test_timeout(self):
        from agent_daemon import AgentConfig, AgentDaemon
        from message_queue import MessageQueue, Task
        mq = MessageQueue(":memory:")
        cfg = AgentConfig(agent_id="test", cli_path="python", max_subtask_timeout=1)
        daemon = AgentDaemon(cfg, mq)
        task = Task(target_agent="test", task_type="-c", context={"args":["import time; time.sleep(10)"]}, max_retries=0)
        result = daemon._execute_subtask(task, attempt=1)
        assert result.success is False
        assert result.exit_code == -1
        assert "timed out" in result.error.lower()

    def test_build_command(self):
        from agent_daemon import AgentConfig, AgentDaemon
        from message_queue import MessageQueue, Task
        mq = MessageQueue(":memory:")
        cfg = AgentConfig(agent_id="test", cli_path="/bin/cli")
        daemon = AgentDaemon(cfg, mq)
        task = Task(target_agent="test", task_type="review", context={"feature_id":"F1","project_dir":"/p","prompt":"hi","file":"f.py","output":"/o.json","args":["-v","-s"]}, max_retries=0)
        cmd = daemon._build_command(task)
        assert cmd[0] == "/bin/cli"
        assert cmd[1] == "review"
        assert "--feature-id" in cmd
        assert "-v" in cmd

    def test_build_env(self):
        from agent_daemon import AgentConfig, AgentDaemon
        from message_queue import MessageQueue, Task
        mq = MessageQueue(":memory:")
        cfg = AgentConfig(agent_id="test", cli_path="echo")
        daemon = AgentDaemon(cfg, mq)
        task = Task(target_agent="test", task_type="code", context={"env":{"X":"1"}}, max_retries=0)
        env = daemon._build_env(task)
        assert env["X"] == "1"
        assert "PATH" in env

class TestRetryLogic:
    def test_retry_exhausts(self):
        from agent_daemon import AgentConfig, AgentDaemon
        from message_queue import MessageQueue, Task
        mq = MessageQueue(":memory:")
        cfg = AgentConfig(agent_id="test", cli_path="python", max_retries=3)
        daemon = AgentDaemon(cfg, mq)
        task = Task(target_agent="test", task_type="-c", context={"args":["exit(1)"]}, max_retries=3)
        result = daemon._execute_with_retry(task)
        assert result.success is False
        assert result.attempt == 3

    def test_retry_succeeds_second_attempt(self):
        from agent_daemon import AgentConfig, AgentDaemon
        from message_queue import MessageQueue, Task
        mq = MessageQueue(":memory:")
        cfg = AgentConfig(agent_id="test", cli_path="python", max_retries=3)
        daemon = AgentDaemon(cfg, mq)
        import tempfile as _tf
        _tmp = _tf.NamedTemporaryFile(mode="w", delete=False, suffix=".txt")
        _tmp.write("0")
        _tmp.close()
        _script = "import sys;p=" + repr(_tmp.name) + ";f=open(p);c=int(f.read().strip());f.close();f=open(p,'w');f.write(str(c+1));f.close();sys.exit(1) if c<1 else print('ok')"
        task = Task(target_agent="test", task_type="-c", context={"args":[_script]}, max_retries=3)
        result = daemon._execute_with_retry(task)
        os.unlink(_tmp.name)
        assert result.success is True
        assert result.attempt >= 2

    def test_retry_respects_task_max(self):
        from agent_daemon import AgentConfig, AgentDaemon
        from message_queue import MessageQueue, Task
        mq = MessageQueue(":memory:")
        cfg = AgentConfig(agent_id="test", cli_path="python", max_retries=5)
        daemon = AgentDaemon(cfg, mq)
        task = Task(target_agent="test", task_type="-c", context={"args":["exit(1)"]}, max_retries=2)
        result = daemon._execute_with_retry(task)
        assert result.success is False
        assert result.attempt == 2


class TestShutdownHandling:
    def test_shutdown_stops_loop(self):
        from agent_daemon import AgentConfig, AgentDaemon, create_shutdown_task
        from message_queue import MessageQueue
        _tdb = tempfile.NamedTemporaryFile(delete=False, suffix=".db")
        _tdb.close()
        try:
            mq = MessageQueue(_tdb.name)
            cfg = AgentConfig(agent_id="test", cli_path="echo")
            mq.push(create_shutdown_task("test"))
            daemon = AgentDaemon(cfg, mq)
            daemon.run()
            assert daemon._running is False
        finally:
            os.unlink(_tdb.name)

    def test_request_shutdown(self):
        from agent_daemon import AgentConfig, AgentDaemon
        from message_queue import MessageQueue
        mq = MessageQueue(":memory:")
        cfg = AgentConfig(agent_id="test", cli_path="echo")
        daemon = AgentDaemon(cfg, mq)
        daemon._running = True
        daemon.request_shutdown()
        assert daemon._running is False

class TestTaskResult:
    def test_defaults(self):
        from agent_daemon import TaskResult
        r = TaskResult(success=False)
        assert r.success is False
        assert r.exit_code == -1
        assert r.attempt == 1

    def test_success(self):
        from agent_daemon import TaskResult
        r = TaskResult(success=True, output="hi", subtask_id="s1", latency_ms=42, exit_code=0, attempt=2, agent_id="claude")
        assert r.output == "hi"
        assert r.latency_ms == 42

class TestSubtaskChunkerChunk:
    def test_chunk_with_files_and_deps(self):
        from subtask_chunker import SubtaskChunker
        chunker = SubtaskChunker()
        task = {"id":"t1","goal":"Build API","files":["src/auth.py","src/db.py","src/api.py","src/main.py"],"dependencies":{"src/api.py":["src/auth.py","src/db.py"],"src/main.py":["src/api.py"]}}
        subtasks = chunker.chunk(task)
        assert len(subtasks) >= 3
        for st in subtasks:
            assert st.parent_task_id == "t1"

    def test_topological_sort_order(self):
        from subtask_chunker import SubtaskChunker
        chunker = SubtaskChunker()
        task = {"id":"ts","goal":"Layers","files":["base.py","middle.py","top.py"],"dependencies":{"top.py":["middle.py"],"middle.py":["base.py"]}}
        subtasks = chunker.chunk(task)
        pos = {st.module_name:i for i,st in enumerate(subtasks)}
        for st in subtasks:
            for did in st.depends_on:
                ds = next((s for s in subtasks if s.id==did),None)
                if ds:
                    assert pos[ds.module_name] < pos[st.module_name]

    def test_no_deps_keeps_order(self):
        from subtask_chunker import SubtaskChunker
        chunker = SubtaskChunker()
        task = {"id":"tn","files":["a.py","b.py","c.py"]}
        subtasks = chunker.chunk(task)
        assert [s.module_name for s in subtasks] == ["a.py","b.py","c.py"]

    def test_large_module_splits_3_ways(self):
        from subtask_chunker import SubtaskChunker
        chunker = SubtaskChunker()
        task = {"id":"tl","modules":[{"name":"big.py","files":["big.py"],"lines":900,"goal":"Big"}]}
        subtasks = chunker.chunk(task, max_subtask_lines=300)
        assert len(subtasks) >= 3
        names = [s.module_name for s in subtasks]
        assert any("part-1" in n for n in names)
        assert any("part-2" in n for n in names)
        assert any("part-3" in n for n in names)

    def test_large_module_splits_4_ways(self):
        from subtask_chunker import SubtaskChunker
        chunker = SubtaskChunker()
        task = {"id":"th","modules":[{"name":"huge.py","files":["huge.py"],"lines":1200,"goal":"Huge"}]}
        subtasks = chunker.chunk(task, max_subtask_lines=300)
        assert len(subtasks) == 4

    def test_string_description(self):
        from subtask_chunker import SubtaskChunker
        chunker = SubtaskChunker()
        subtasks = chunker.chunk("Make a parser")
        assert len(subtasks) >= 1
        assert subtasks[0].parent_task_id == "task"


class TestResumeFromCheckpoint:
    def test_raises_without_checkpointer(self):
        from subtask_chunker import SubtaskChunker
        chunker = SubtaskChunker()
        with pytest.raises(RuntimeError, match="requires a Checkpointer"):
            chunker.resume_from_checkpoint("t1")

    def test_resume_with_checkpointer(self):
        from subtask_chunker import SubtaskChunker
        from checkpointer import Checkpointer
        from state_store import StateStore
        _tdb = tempfile.NamedTemporaryFile(delete=False, suffix=".db")
        _tdb.close()
        _store = None
        try:
            _store = StateStore(Path(_tdb.name))
            _cp = Checkpointer(store=_store)
            _cp.save("t1","t1-st-1","success",phase="dev",agent_id="a")
            _cp.save("t1","t1-st-2","success",phase="dev",agent_id="a")
            _chunker = SubtaskChunker(checkpointer=_cp)
            _rem = _chunker.resume_from_checkpoint("t1")
            assert isinstance(_rem, list)
        finally:
            try:
                os.unlink(_tdb.name)
            except PermissionError:
                pass

class TestSubtaskModel:
    def test_roundtrip(self):
        from subtask_chunker import Subtask
        s = Subtask(id="s1",parent_task_id="p1",order=2,goal="G",expected_files=["a.py"],timeout=120.0,depends_on=["d1"],estimated_lines=150,module_name="m",context={"x":1})
        d = s.to_dict()
        s2 = Subtask.from_dict(d)
        assert s2.id == s.id and s2.goal == s.goal and s2.context == s.context

    def test_defaults(self):
        from subtask_chunker import Subtask
        s = Subtask()
        assert s.id == "" and s.expected_files == [] and s.context == {}

class TestTopologicalSort:
    def test_linear_chain(self):
        from subtask_chunker import Subtask, SubtaskChunker
        a=Subtask(id="s1",order=0,module_name="a")
        b=Subtask(id="s2",order=1,module_name="b",depends_on=["s1"])
        c=Subtask(id="s3",order=2,module_name="c",depends_on=["s2"])
        r=SubtaskChunker._topological_sort([c,a,b])
        assert [s.id for s in r] == ["s1","s2","s3"]

    def test_diamond(self):
        from subtask_chunker import Subtask, SubtaskChunker
        a=Subtask(id="s1",order=0,module_name="a")
        b=Subtask(id="s2",order=1,module_name="b",depends_on=["s1"])
        c=Subtask(id="s3",order=2,module_name="c",depends_on=["s1"])
        d=Subtask(id="s4",order=3,module_name="d",depends_on=["s2","s3"])
        r=SubtaskChunker._topological_sort([d,c,b,a])
        ids=[s.id for s in r]
        assert ids[0]=="s1" and ids[-1]=="s4"
        assert ids.index("s2") < ids.index("s4")
        assert ids.index("s3") < ids.index("s4")

    def test_cycle_fallback(self):
        from subtask_chunker import Subtask, SubtaskChunker
        a=Subtask(id="s1",order=0,depends_on=["s2"])
        b=Subtask(id="s2",order=1,depends_on=["s1"])
        r=SubtaskChunker._topological_sort([a,b])
        assert len(r)==2

    def test_empty(self):
        from subtask_chunker import SubtaskChunker
        assert SubtaskChunker._topological_sort([])==[]

    def test_single(self):
        from subtask_chunker import Subtask, SubtaskChunker
        r=SubtaskChunker._topological_sort([Subtask(id="x")])
        assert len(r)==1 and r[0].id=="x"

class TestCheckpointerProperty:
    def test_none_default(self):
        from subtask_chunker import SubtaskChunker
        assert SubtaskChunker().checkpointer is None

    def test_set(self):
        from subtask_chunker import SubtaskChunker
        from checkpointer import Checkpointer
        from state_store import StateStore
        _t = tempfile.NamedTemporaryFile(delete=False, suffix=".db")
        _t.close()
        _s = None
        try:
            _s = StateStore(Path(_t.name))
            _c = Checkpointer(store=_s)
            assert SubtaskChunker(checkpointer=_c).checkpointer is _c
        finally:
            try:
                os.unlink(_t.name)
            except PermissionError:
                pass

class TestCreateShutdownTask:
    def test_valid(self):
        from agent_daemon import create_shutdown_task
        t=create_shutdown_task("a1")
        assert t.target_agent=="a1" and t.task_type=="shutdown" and t.priority==2 and t.max_retries==0
