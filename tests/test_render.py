# tests/test_render.py
import stat
from pathlib import Path
from xml.etree import ElementTree

from preflight.contract import Contract
from preflight.graph import Node
from preflight.outcome import Status, error, fail, ok, outcome, pending
from preflight.render import (
    instance_json,
    open_steps,
    render_check,
    report_json,
    to_junit,
    write_json,
)
from preflight.runner import NodeResult

CONTRACT = Contract(
    path=Path("dev.toml"),
    root=Path("/tmp"),
    scope="environment",
    environment="dev",
    identity_data={},
    sections={},
    placeholders=[],
    inputs={},
)


def result(node_id, out=None, blocked_by=()):
    node = Node(
        id=node_id,
        check_id=node_id.split("[")[0],
        key=node_id,
        gates=["delegation"],
        contract=CONTRACT,
        data={},
        identity=None,
        section_bound=False,
    )
    status = Status.BLOCKED if out is None else out.status
    return NodeResult(node, status, out, list(blocked_by), 0.5)


RESULTS = {
    r.node.id: r
    for r in [
        result("aws.session[admin]", outcome(ok())),
        result(
            "dns.delegated[delegation]",
            outcome(
                fail(
                    "app",
                    do="In Administration, set NS app.tellabs.dev to exactly these servers:",
                    paste="app.tellabs.dev. NS ns-1.example.\napp.tellabs.dev. NS ns-2.example.",
                    wait="up to 15 minutes",
                    ref="README, DNS step 1",
                ),
                ok("api"),
            ),
        ),
        result("acm.issued[root_certificate]", blocked_by=["dns.delegated[delegation]"]),
        result(
            "dns.cname[aliases]",
            outcome(fail("vault", do="Add CNAME vault.tellabs.dev.", advisory=True)),
        ),
    ]
}

EXPECTED = """preflight check delegation (dev)

  ok       aws.session[admin]
  FAIL     dns.delegated[delegation]:app
  ok       dns.delegated[delegation]:api
  blocked  acm.issued[root_certificate]  (waits on: dns.delegated[delegation])
  warn     dns.cname[aliases]:vault

Open steps:
> NEXT  dns.delegated[delegation]:app
        In Administration, set NS app.tellabs.dev to exactly these servers:
          app.tellabs.dev. NS ns-1.example.
          app.tellabs.dev. NS ns-2.example.
        wait: up to 15 minutes
        see: README, DNS step 1
        then unblocks: acm.issued[root_certificate]

Warnings:
  dns.cname[aliases]:vault: Add CNAME vault.tellabs.dev.
"""


def test_the_worklist():
    assert render_check("preflight check delegation (dev)", RESULTS, color=False) == EXPECTED


def test_color_only_when_asked():
    assert "\033[31mFAIL" in render_check("t", RESULTS, color=True)
    assert "\033" not in render_check("t", RESULTS, color=False)


def test_nothing_open():
    text = render_check("t", {"a": result("a[x]", outcome(ok()))}, color=False)
    assert text.endswith("Nothing open.\n")


def test_pending_comes_after_failures_and_identical_steps_merge():
    results = {
        r.node.id: r
        for r in [
            result("a[x]", outcome(pending(wait="an hour"))),
            result("b[x]", outcome(fail("1", do="Same."), fail("2", do="Same."))),
        ]
    }
    steps = open_steps(results)
    assert [s.item_id for s in steps] == ["b[x]:1", "a[x]"]
    assert steps[0].also == ("b[x]:2",)


def test_json_items_carry_the_public_next_step_shape():
    data = instance_json(RESULTS["dns.delegated[delegation]"])
    assert data["status"] == "fail"
    assert data["gate"] == "delegation"
    assert set(data["items"][0]["next_step"]) == {"do", "paste", "wait", "ref"}
    report = report_json(
        command="check",
        exit_code=1,
        started="s",
        finished="f",
        inputs={"a": "1"},
        runs=[],
        steps=open_steps(RESULTS),
        inputs_changed=[],
    )
    assert report["next"] == "dns.delegated[delegation]:app"
    assert report["inputs"] == [{"path": "a", "sha256": "1"}]


def test_json_files_are_private(tmp_path):
    path = tmp_path / "out.json"
    write_json(path, {"a": 1})
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert path.read_text() == '{\n  "a": 1\n}\n'


def test_junit_never_skips():
    extra = {"e[x]": result("e[x]", outcome(error(do="Sign in.", error_type="Timeout")))}
    text = to_junit("dev", {**RESULTS, **extra})
    suite = ElementTree.fromstring(text.split("?>", 1)[1])
    assert (suite.get("tests"), suite.get("failures"), suite.get("errors")) == ("6", "2", "1")
    assert "skipped" not in text
    vault = suite.find("testcase[@name='dns.cname[aliases]:vault']")
    assert vault.find("system-out").text == "warning: Add CNAME vault.tellabs.dev."
