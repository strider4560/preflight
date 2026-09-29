# tests/test_resolvers.py
import hashlib

import pytest

from preflight.resolvers import (
    LazySsm,
    ResolveError,
    Resolver,
    apply_ssm_value,
    parse_reference,
)

TFVARS = (
    'account_id = "111111111111"\n'
    'zones = ["app"]\n'
    'm = { "k" = "v", n = 1 }\n'
    "h = <<EOT\nline\nEOT\n"
    "# a comment\n"
)


def write(root, relative, text):
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def resolve(root, table):
    return Resolver(root).resolve(parse_reference(table))


def test_tfvars_values_come_back_unquoted(tmp_path):
    write(tmp_path, "envs/dev.tfvars", TFVARS)
    get = lambda key: resolve(tmp_path, {"tfvars": "envs/dev.tfvars", "key": key})  # noqa: E731
    assert get("account_id") == "111111111111"
    assert get("zones") == ["app"]
    assert get("m") == {"k": "v", "n": 1}
    assert get("h") == "line"


@pytest.mark.parametrize("line", ['a = "${var.x}"\n', 'a = upper("x")\n'])
def test_tfvars_expressions_are_refused(tmp_path, line):
    write(tmp_path, "x.tfvars", line)
    with pytest.raises(ResolveError, match="expression"):
        resolve(tmp_path, {"tfvars": "x.tfvars", "key": "a"})


def test_missing_key_and_missing_file(tmp_path):
    write(tmp_path, "x.tfvars", 'a = "1"\n')
    with pytest.raises(ResolveError, match="has no variable b"):
        resolve(tmp_path, {"tfvars": "x.tfvars", "key": "b"})
    with pytest.raises(ResolveError, match="does not exist"):
        resolve(tmp_path, {"tfvars": "nope.tfvars", "key": "a"})


def test_yaml_and_json_paths(tmp_path):
    write(tmp_path, "a.yaml", "a:\n  b:\n    - x\n    - y\n")
    write(tmp_path, "a.json", '{"a": {"c": 2}}')
    assert resolve(tmp_path, {"yaml": "a.yaml", "path": "a.b.1"}) == "y"
    assert resolve(tmp_path, {"json": "a.json", "path": "a.c"}) == 2
    assert resolve(tmp_path, {"json": "a.json"}) == {"a": {"c": 2}}
    with pytest.raises(ResolveError, match="no 'z'"):
        resolve(tmp_path, {"yaml": "a.yaml", "path": "a.z"})


def test_yaml_glob_lists_matching_files_sorted(tmp_path):
    write(tmp_path, "apps/b.yaml", "name: b\n")
    write(tmp_path, "apps/a.yaml", "name: a\n")
    assert resolve(tmp_path, {"yaml_glob": "apps/*.yaml", "path": "name"}) == [
        {"file": "apps/a.yaml", "value": "a"},
        {"file": "apps/b.yaml", "value": "b"},
    ]


def test_ssm_references_are_lazy(tmp_path):
    lazy = resolve(
        tmp_path,
        {"ssm": "/platform/dns/name_servers", "identity": "admin", "format": "json", "path": "app"},
    )
    assert lazy == LazySsm("/platform/dns/name_servers", "admin", "json", "app", None)
    assert LazySsm.from_dict(lazy.to_dict()) == lazy
    assert apply_ssm_value('{"app": ["ns-1"]}', lazy) == ["ns-1"]
    with pytest.raises(ResolveError, match="not JSON"):
        apply_ssm_value("nope", lazy)


@pytest.mark.parametrize(
    ("table", "message"),
    [
        ({"tfvars": "x"}, "needs key"),
        ({"tfvars": "x", "key": "k", "yaml": "y"}, "more than one source"),
        ({"yaml": "x", "bogus": 1}, "unknown keys: bogus"),
        ({"ssm": "not-a-path", "identity": "admin"}, "parameter name"),
        ({"ssm": "/x"}, "needs identity"),
        ({"yaml": "x", "placeholder": "0"}, "placeholder must be a list"),
    ],
)
def test_malformed_references(table, message):
    with pytest.raises(ResolveError, match=message):
        parse_reference(table)


def test_a_table_without_a_source_is_not_a_reference():
    assert parse_reference({"k": 1}) is None


def test_inputs_record_what_was_read(tmp_path):
    path = write(tmp_path, "envs/dev.tfvars", TFVARS)
    resolver = Resolver(tmp_path)
    resolver.resolve(parse_reference({"tfvars": "envs/dev.tfvars", "key": "zones"}))
    assert resolver.inputs == {"envs/dev.tfvars": hashlib.sha256(path.read_bytes()).hexdigest()}


def test_paths_outside_the_repository_are_refused(tmp_path):
    (tmp_path / "repo").mkdir()
    write(tmp_path, "outside.yaml", "a: 1\n")
    with pytest.raises(ResolveError, match="outside the repository"):
        resolve(tmp_path / "repo", {"yaml": "../outside.yaml"})
